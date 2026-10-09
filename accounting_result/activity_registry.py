# -*- coding: utf-8 -*-
"""
accounting_result/activity_registry.py

Δραστηριότητες που τρέχουν ΠΡΑΓΜΑΤΙΚΑ στον server, ανά ομάδα, ώστε:
  * να κλειδώνουν (ο ίδιος τύπος εργασίας δεν ξεκινά δεύτερη φορά όσο τρέχει η πρώτη), και
  * να εμφανίζονται σε ΟΛΟΥΣ τους χρήστες της ομάδας, με το ποιος έδωσε την εντολή.

Είδη (kind):
  "ar_single" : Λογιστικό Αποτέλεσμα — Ατομικός υπολογισμός (ζει όσο εκτελείται το αίτημα)
  "fetch"     : Λήψη παραστατικών (ατομική ή μαζική) — ζει όσο τρέχει το thread λήψης
(Ο Μαζικός Λογιστικού Αποτελέσματος ζει στο job_registry.py και ενώνεται με αυτά στο /api/group_activity.)

Κάθε εγγραφή έχει όριο ζωής (ttl) ώστε μια εργασία που δεν έκλεισε σωστά να μην κλειδώνει για πάντα.
In-process (όπως και το job_registry): ισχύει για μία διεργασία server.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

_LOCK = threading.RLock()
_ACTS: Dict[str, Dict[str, Any]] = {}

TTL_AR_SINGLE = 30 * 60
TTL_FETCH = 4 * 3600


def _prune(now: float) -> None:
    for aid in [a for a, e in _ACTS.items() if now > e.get("expires", 0)]:
        _ACTS.pop(aid, None)


def _public(e: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(e)
    title, detail = e.get("title") or "", e.get("detail") or ""
    out["label"] = f"{title} — {detail}" if (title and detail) else (title or detail)
    return out


def begin(kind: str, group: str, user_key: str, username: str, label: str = "",
          ttl: float = TTL_AR_SINGLE, ref: str = "") -> str:
    """Καταχωρεί νέα δραστηριότητα και επιστρέφει το id της. `label` = τίτλος (π.χ. όνομα πελάτη)."""
    now = time.time()
    aid = uuid.uuid4().hex[:12]
    with _LOCK:
        _prune(now)
        _ACTS[aid] = {
            "id": aid, "kind": kind, "group": group or "", "user_key": user_key or "",
            "username": username or "", "title": label or "", "detail": "", "ref": ref or "",
            "started": now, "updated": now, "expires": now + ttl,
            "percent": None, "current": None, "total": None,
        }
    return aid


def find_conflict(kinds: Iterable[str], group: str, exclude_id: str = "") -> Optional[Dict[str, Any]]:
    """Πρώτη ενεργή δραστηριότητα της ομάδας με είδος ∈ kinds (εκτός της exclude_id)."""
    kinds = set(kinds)
    now = time.time()
    with _LOCK:
        _prune(now)
        for e in sorted(_ACTS.values(), key=lambda x: x["started"]):
            if e["group"] == (group or "") and e["kind"] in kinds and e["id"] != exclude_id:
                return _public(e)
    return None


def begin_exclusive(kind: str, conflicts: Iterable[str], group: str, user_key: str, username: str,
                    label: str = "", ttl: float = TTL_AR_SINGLE, ref: str = "",
                    extra_conflict: Optional[Dict[str, Any]] = None):
    """Ατομικά: αν δεν υπάρχει σύγκρουση (ούτε `extra_conflict` από άλλο registry) ξεκινά τη δραστηριότητα.
    Επιστρέφει (id, None) ή (None, conflicting_entry)."""
    with _LOCK:
        if extra_conflict:
            return None, extra_conflict
        c = find_conflict(conflicts, group)
        if c:
            return None, c
        return begin(kind, group, user_key, username, label, ttl, ref), None


def update(aid: Optional[str], title: Optional[str] = None, detail: Optional[str] = None,
           percent: Optional[float] = None, current: Optional[int] = None,
           total: Optional[int] = None) -> None:
    if not aid:
        return
    with _LOCK:
        e = _ACTS.get(aid)
        if not e:
            return
        e["updated"] = time.time()
        if title is not None:
            e["title"] = str(title)
        if detail is not None:
            e["detail"] = str(detail)
        if percent is not None:
            e["percent"] = percent
        if current is not None:
            e["current"] = current
        if total is not None:
            e["total"] = total


def update_by_ref(ref: str, **kw) -> None:
    """Ενημέρωση βάσει του `ref` (π.χ. job_id μαζικής λήψης / κλειδί ατομικής λήψης)."""
    if not ref:
        return
    with _LOCK:
        for e in _ACTS.values():
            if e.get("ref") == ref:
                update(e["id"], **kw)


def end(aid: Optional[str]) -> None:
    if not aid:
        return
    with _LOCK:
        _ACTS.pop(aid, None)


def end_by_ref(ref: str) -> None:
    if not ref:
        return
    with _LOCK:
        for aid in [a for a, e in _ACTS.items() if e.get("ref") == ref]:
            _ACTS.pop(aid, None)


def active(group: str, kinds: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    kinds = set(kinds) if kinds else None
    now = time.time()
    with _LOCK:
        _prune(now)
        out = [_public(e) for e in _ACTS.values()
               if e["group"] == (group or "") and (kinds is None or e["kind"] in kinds)]
    out.sort(key=lambda x: x["started"])
    return out
