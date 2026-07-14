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

def record_failed_url(
    url: str,
    reason: str = "incomplete",
    missing_fields: Optional[List[str]] = None,
    group: Optional[str] = None,
    scraper_source: Optional[str] = None,
) -> bool:
    """
    Append (or refresh) a URL in the global admin log. Deduplicated by URL: a
    repeat bumps the hit count and last-seen timestamp instead of adding a row.
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
) -> bool:
    """
    Add/refresh a receipt in the active group's review queue. Keyed by MARK when
    present, otherwise by URL, so re-scanning the same receipt updates in place.
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
            store[key] = row
            _write_json_atomic(path, store)
        return True
    except Exception:
        log.exception("scrape_review: add_to_review_queue failed (%s)", group_dir)
        return False


def list_review_queue(group_dir: str) -> List[Dict[str, Any]]:
    if not group_dir:
        return []
    store = _read_json(_queue_path(group_dir), {})
    if not isinstance(store, dict):
        return []
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
