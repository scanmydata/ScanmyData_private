"""
Scrape resilience: failed/incomplete-URL tracking and per-group receipt review.

Two independent stores, both decoupled from Flask/SQLAlchemy so they work from
any context (request, background worker, CLI):

1. Admin-only failed-URL log — a single global JSON file recording every URL a
   scraper could not fully resolve (unknown viewer, empty result, or a receipt
   that came back missing required fields). Deduplicated by URL, with a hit
   count and first/last-seen timestamps. This is the list the general admin
   reviews to decide which viewers to add to the scrapers next.

2. Per-group receipt review queue — receipts that scraped with something missing
   (e.g. no αριθμός/σειρά/ημερομηνία/σύνολο). Stored under the group's own data
   folder so each team reviews its own items. The export flow warns (does not
   block) while this queue is non-empty.
"""

from __future__ import annotations

import os
import json
import threading
import datetime
import logging
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)

_lock = threading.Lock()

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Admin-only global log. Overridable via env for tests / alternate deployments.
_ADMIN_DIR = os.environ.get("SCRAPE_REVIEW_ADMIN_DIR") or os.path.join(_BASE_DIR, "data", "_admin")
_FAILED_URLS_PATH = os.path.join(_ADMIN_DIR, "failed_scrape_urls.json")

_REVIEW_QUEUE_FILENAME = "receipt_review_queue.json"

# Fields a *complete* receipt is expected to carry. A missing one does not stop
# the user (they can fill it in), but it flags the receipt for review.
#
# NOTE: MARK is deliberately NOT here. Receipts (ΑΛΠ/ΑΠΥ) frequently have no
# myDATA MARK at all — they get a local pseudo-MARK (5000…). A missing MARK on a
# receipt is normal, not an error, so it must never flag the receipt for review.
RECEIPT_REQUIRED_FIELDS = ("issuer_vat", "issue_date", "total_amount", "progressive_aa")

# Legacy entries may have been queued for a missing MARK before the rule above.
# These are filtered out on read so the queue self-heals without a migration.
_NON_ISSUE_FIELDS = {"mark", "MARK"}

_FIELD_LABELS_EL = {
    "mark": "ΜΑΡΚ",
    "issuer_vat": "ΑΦΜ εκδότη",
    "issuer_name": "Επωνυμία",
    "issue_date": "Ημερομηνία",
    "total_amount": "Σύνολο",
    "progressive_aa": "Αριθμός",
    "series": "Σειρά",
}


def field_label(key: str) -> str:
    return _FIELD_LABELS_EL.get(str(key or "").strip(), str(key or ""))


def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="seconds")


def _domain_of(url: str) -> str:
    try:
        return (urlparse(str(url or "")).netloc or "").lower()
    except Exception:
        return ""


def _read_json(path: str, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception:
        log.exception("scrape_review: failed reading %s", path)
    return default


def _write_json_atomic(path: str, data) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        log.exception("scrape_review: failed writing %s", path)
        return False


# ---------------------------------------------------------------------------
# 1) Admin-only failed / incomplete URL log
# ---------------------------------------------------------------------------

_MAX_EVENTS = 12          # τελευταίες εμφανίσεις ανά URL (ποιος/πού/πότε)


def record_failed_url(
    url: str,
    reason: str = "incomplete",
    missing_fields: Optional[List[str]] = None,
    group: Optional[str] = None,
    scraper_source: Optional[str] = None,
    user: Optional[str] = None,
    customer_vat: Optional[str] = None,
    customer_name: Optional[str] = None,
    error: Optional[str] = None,
    kind: Optional[str] = None,
) -> bool:
    """
    Append (or refresh) a URL in the global admin log. Deduplicated by URL: a
    repeat bumps the hit count and last-seen timestamp instead of adding a row.

    user / customer_* / group λένε ποιος και πού το συνάντησε (λίστες ανά URL + τελευταίες εμφανίσεις),
    error το μήνυμα/εξαίρεση του scraper, kind: invoice | receipt | pdf.
    """
    u = str(url or "").strip()
    if not u:
        return False
    try:
        with _lock:
            store = _read_json(_FAILED_URLS_PATH, {})
            if not isinstance(store, dict):
                store = {}
            now = _now_iso()
            row = store.get(u) or {
                "url": u,
                "domain": _domain_of(u),
                "first_seen": now,
                "count": 0,
                "groups": [],
            }
            row["last_seen"] = now
            row["count"] = int(row.get("count") or 0) + 1
            row["reason"] = str(reason or "incomplete")
            if missing_fields:
                row["missing_fields"] = sorted(set(str(x) for x in missing_fields))
            if scraper_source:
                row["scraper_source"] = str(scraper_source)
            if group:
                groups = set(row.get("groups") or [])
                groups.add(str(group))
                row["groups"] = sorted(groups)
            if user:
                users = set(row.get("users") or [])
                users.add(str(user))
                row["users"] = sorted(users)
            cust_label = ""
            if customer_vat:
                cust_label = f"{customer_vat}" + (f" · {customer_name}" if customer_name else "")
                customers = set(row.get("customers") or [])
                customers.add(cust_label)
                row["customers"] = sorted(customers)
            if error:
                row["last_error"] = str(error)[:400]
            if kind:
                row["kind"] = str(kind)
            events = list(row.get("events") or [])
            events.append({
                "ts": now, "group": str(group or ""), "user": str(user or ""), "customer": cust_label,
                "reason": str(reason or "incomplete"), "source": str(scraper_source or ""),
                "error": (str(error)[:160] if error else ""),
            })
            row["events"] = events[-_MAX_EVENTS:]
            store[u] = row
            _write_json_atomic(_FAILED_URLS_PATH, store)
        return True
    except Exception:
        log.exception("scrape_review: record_failed_url failed for %s", u)
        return False


def list_failed_urls(limit: int = 500) -> List[Dict[str, Any]]:
    store = _read_json(_FAILED_URLS_PATH, {})
    if not isinstance(store, dict):
        return []
    rows = list(store.values())
    rows.sort(key=lambda r: str(r.get("last_seen") or ""), reverse=True)
    if limit and limit > 0:
        rows = rows[:limit]
    return rows


def clear_failed_url(url: str) -> bool:
    u = str(url or "").strip()
    if not u:
        return False
    try:
        with _lock:
            store = _read_json(_FAILED_URLS_PATH, {})
            if isinstance(store, dict) and u in store:
                store.pop(u, None)
                _write_json_atomic(_FAILED_URLS_PATH, store)
                return True
    except Exception:
        log.exception("scrape_review: clear_failed_url failed for %s", u)
    return False


def failed_urls_path() -> str:
    return _FAILED_URLS_PATH


def clear_all_failed_urls() -> int:
    """Αδειάζει ολόκληρη τη λίστα (admin). Επιστρέφει πόσα URL αφαιρέθηκαν."""
    try:
        with _lock:
            store = _read_json(_FAILED_URLS_PATH, {})
            n = len(store) if isinstance(store, dict) else 0
            _write_json_atomic(_FAILED_URLS_PATH, {})
            return n
    except Exception:
        log.exception("scrape_review: clear_all_failed_urls failed")
        return 0


def merge_failed_urls(remote: Dict[str, Any]) -> int:
    """
    Συγχώνευση απομακρυσμένης λίστας (κοινό αντίγραφο στο Drive) στην τοπική — ένωση ανά URL:
    max(count) όχι άθροισμα (το ίδιο γεγονός υπάρχει και στα δύο), min(first_seen), max(last_seen),
    ένωση groups/users/customers/events. Δεν σβήνει ποτέ τίποτα. Επιστρέφει πόσα URL άλλαξαν/προστέθηκαν.
    """
    if not isinstance(remote, dict):
        return 0
    changed = 0
    try:
        with _lock:
            store = _read_json(_FAILED_URLS_PATH, {})
            if not isinstance(store, dict):
                store = {}
            for url, rr in remote.items():
                if not isinstance(rr, dict) or not str(url or "").strip():
                    continue
                cur = store.get(url)
                if not isinstance(cur, dict):
                    store[url] = rr
                    changed += 1
                    continue
                merged = dict(cur)
                before = json.dumps(cur, sort_keys=True, ensure_ascii=False)
                merged["count"] = max(int(cur.get("count") or 0), int(rr.get("count") or 0))
                fs = [x for x in (cur.get("first_seen"), rr.get("first_seen")) if x]
                ls = [x for x in (cur.get("last_seen"), rr.get("last_seen")) if x]
                if fs:
                    merged["first_seen"] = min(fs)
                if ls:
                    merged["last_seen"] = max(ls)
                for key in ("groups", "users", "customers", "missing_fields"):
                    u = set(cur.get(key) or []) | set(rr.get(key) or [])
                    if u:
                        merged[key] = sorted(str(x) for x in u)
                ev = {}
                for e in list(cur.get("events") or []) + list(rr.get("events") or []):
                    if isinstance(e, dict):
                        ev[(e.get("ts"), e.get("user"), e.get("group"), e.get("customer"))] = e
                if ev:
                    merged["events"] = [ev[k] for k in sorted(ev, key=lambda k: str(k[0]))][-_MAX_EVENTS:]
                newer = rr if str(rr.get("last_seen") or "") > str(cur.get("last_seen") or "") else cur
                for key in ("reason", "scraper_source", "last_error", "kind"):
                    if newer.get(key):
                        merged[key] = newer.get(key)
                if json.dumps(merged, sort_keys=True, ensure_ascii=False) != before:
                    store[url] = merged
                    changed += 1
            if changed:
                _write_json_atomic(_FAILED_URLS_PATH, store)
    except Exception:
        log.exception("scrape_review: merge_failed_urls failed")
    return changed


def list_all_failed(group_dirs: Dict[str, str], limit: int = 2000) -> List[Dict[str, Any]]:
    """
    Ενιαία λίστα για τον admin: το καθολικό αρχείο + οι ουρές ελέγχου αποδείξεων όλων των ομάδων
    (group_dirs: {όνομα ομάδας: φάκελος}). Οι ουρές επιβιώνουν σε κάθε redeploy (συγχρονίζονται με την ομάδα),
    άρα καλύπτουν και URL που δεν υπάρχουν πια στο καθολικό αρχείο. Dedupe ανά URL.
    """
    rows = {r["url"]: dict(r) for r in list_failed_urls(limit=0) if isinstance(r, dict) and r.get("url")}
    for gname, gdir in (group_dirs or {}).items():
        try:
            for item in list_review_queue(gdir):
                u = str(item.get("url") or "").strip()
                if not u:
                    continue
                r = rows.get(u)
                if r is None:
                    r = rows[u] = {
                        "url": u, "domain": _domain_of(u), "first_seen": item.get("added_at") or "",
                        "last_seen": item.get("updated_at") or item.get("added_at") or "", "count": 1,
                        "reason": "incomplete", "missing_fields": item.get("missing_fields") or [],
                        "groups": [], "source": "review_queue", "kind": "receipt",
                    }
                groups = set(r.get("groups") or [])
                groups.add(gname)
                r["groups"] = sorted(groups)
                if item.get("customer_vat"):
                    lab = f"{item.get('customer_vat')}" + (f" · {item.get('customer_name')}" if item.get("customer_name") else "")
                    r["customers"] = sorted(set(r.get("customers") or []) | {lab})
                r["in_review_queue"] = True
        except Exception:
            log.exception("scrape_review: list_all_failed: group %s failed", gname)
    out = list(rows.values())
    out.sort(key=lambda r: str(r.get("last_seen") or ""), reverse=True)
    return out[:limit] if limit and limit > 0 else out


# ---------------------------------------------------------------------------
# 2) Per-group receipt review queue
# ---------------------------------------------------------------------------

def _queue_path(group_dir: str) -> str:
    return os.path.join(group_dir, _REVIEW_QUEUE_FILENAME)


def add_to_review_queue(
    group_dir: str,
    url: str,
    mark: str = "",
    missing_fields: Optional[List[str]] = None,
    fields: Optional[Dict[str, Any]] = None,
    customer_vat: str = "",
    customer_name: str = "",
) -> bool:
    """
    Add/refresh a receipt in the active group's review queue. Keyed by MARK when
    present, otherwise by URL, so re-scanning the same receipt updates in place.

    customer_vat/customer_name tag the entry with the customer that was active
    at scrape time, so list_review_queue() can scope the warning per customer.
    """
    if not group_dir:
        return False
    key = str(mark or "").strip() or str(url or "").strip()
    if not key:
        return False
    try:
        with _lock:
            path = _queue_path(group_dir)
            store = _read_json(path, {})
            if not isinstance(store, dict):
                store = {}
            now = _now_iso()
            row = store.get(key) or {"added_at": now}
            row.update({
                "key": key,
                "url": str(url or "").strip(),
                "mark": str(mark or "").strip(),
                "missing_fields": sorted(set(str(x) for x in (missing_fields or []))),
                "missing_labels": [field_label(x) for x in (missing_fields or [])],
                "fields": fields or {},
                "updated_at": now,
            })
            # A re-scan with no active customer keeps the tag already on file.
            cust_vat = str(customer_vat or "").strip()
            if cust_vat:
                row["customer_vat"] = cust_vat
                row["customer_name"] = str(customer_name or "").strip()
            store[key] = row
            _write_json_atomic(path, store)
        return True
    except Exception:
        log.exception("scrape_review: add_to_review_queue failed (%s)", group_dir)
        return False


def list_review_queue(group_dir: str, customer_vat: str = "") -> List[Dict[str, Any]]:
    """
    Receipts still awaiting completion. With customer_vat, entries tagged with a
    different customer are left out; untagged (legacy) entries are always listed.
    Without it, the whole group's queue is returned.
    """
    if not group_dir:
        return []
    store = _read_json(_queue_path(group_dir), {})
    if not isinstance(store, dict):
        return []
    only_vat = str(customer_vat or "").strip()
    rows = []
    stale_keys = []
    for key, row in store.items():
        if not isinstance(row, dict):
            continue
        # Drop MARK from the missing set (a missing receipt MARK is not an error).
        missing = [m for m in (row.get("missing_fields") or []) if str(m) not in _NON_ISSUE_FIELDS]
        if not missing:
            # Nothing genuinely missing anymore → this entry is stale.
            stale_keys.append(key)
            continue
        row_vat = str(row.get("customer_vat") or "").strip()
        if only_vat and row_vat and row_vat != only_vat:
            continue
        row = dict(row)
        row["missing_fields"] = missing
        row["missing_labels"] = [field_label(m) for m in missing]
        rows.append(row)
    # Persist the self-heal so the queue shrinks permanently, not just on read.
    if stale_keys:
        try:
            with _lock:
                cur = _read_json(_queue_path(group_dir), {})
                if isinstance(cur, dict):
                    changed = False
                    for k in stale_keys:
                        if k in cur:
                            missing = [m for m in ((cur[k] or {}).get("missing_fields") or [])
                                       if str(m) not in _NON_ISSUE_FIELDS]
                            if not missing:
                                cur.pop(k, None)
                                changed = True
                    if changed:
                        _write_json_atomic(_queue_path(group_dir), cur)
        except Exception:
            log.exception("scrape_review: self-heal of stale queue entries failed")
    rows.sort(key=lambda r: str(r.get("updated_at") or r.get("added_at") or ""), reverse=True)
    return rows


def set_customer_tag(group_dir: str, key: str, customer_vat: str, customer_name: str = "") -> bool:
    """Σημειώνει σε υπάρχουσα εγγραφή ουράς τον πελάτη στον οποίο ανήκει (για παλιές εγγραφές χωρίς tag)."""
    if not group_dir or not key or not customer_vat:
        return False
    try:
        with _lock:
            path = _queue_path(group_dir)
            store = _read_json(path, {})
            row = store.get(str(key).strip()) if isinstance(store, dict) else None
            if not isinstance(row, dict):
                return False
            row["customer_vat"] = str(customer_vat).strip()
            row["customer_name"] = str(customer_name or "").strip()
            _write_json_atomic(path, store)
            return True
    except Exception:
        log.exception("scrape_review: set_customer_tag failed (%s)", group_dir)
        return False


def resolve_review(group_dir: str, key: str) -> bool:
    """Remove one item from the queue (e.g. after the user completes it)."""
    if not group_dir or not key:
        return False
    k = str(key).strip()
    try:
        with _lock:
            path = _queue_path(group_dir)
            store = _read_json(path, {})
            if isinstance(store, dict) and k in store:
                store.pop(k, None)
                _write_json_atomic(path, store)
                return True
    except Exception:
        log.exception("scrape_review: resolve_review failed (%s)", group_dir)
    return False


def review_queue_count(group_dir: str) -> int:
    return len(list_review_queue(group_dir))


# ---------------------------------------------------------------------------
# Shared helper: which required fields are missing from a scraped receipt?
# ---------------------------------------------------------------------------

def missing_receipt_fields(fields: Dict[str, Any]) -> List[str]:
    """Return the list of RECEIPT_REQUIRED_FIELDS that are blank in ``fields``."""
    out = []
    f = fields or {}
    for key in RECEIPT_REQUIRED_FIELDS:
        val = f.get(key)
        if val is None or str(val).strip() in ("", "N/A", "None"):
            out.append(key)
    return out
