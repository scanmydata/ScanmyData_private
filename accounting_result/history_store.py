# -*- coding: utf-8 -*-
"""
accounting_result/history_store.py

Keeps a simple append-only log of when a Λογιστικό Αποτέλεσμα was computed for
a company and by whom, so the accountant can later tell what was already run
for a client and when. One JSON file per company at
data/<group>/accounting_result/history/<AFM>.json (via the caller-supplied
path, following the app's group_path() convention), newest entry first,
capped so the file can't grow unbounded.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional


_MAX_ENTRIES = 200


def _read(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write(path: str, entries: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def append_entry(
    path: str,
    credential_name: str,
    vat: str,
    date_from: str,
    date_to: str,
    computed_by: str,
    final_net_profit: float,
    taxable_result: float,
    mode: str,
    report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    entry = {
        "id": uuid.uuid4().hex,
        "timestamp": datetime.now().isoformat(),
        "credential_name": credential_name,
        "vat": vat,
        "date_from": date_from,
        "date_to": date_to,
        "computed_by": computed_by,
        "final_net_profit": round(float(final_net_profit or 0.0), 2),
        "taxable_result": round(float(taxable_result or 0.0), 2),
        "mode": mode,
        # Full report snapshot so a history row can be reopened later without
        # re-hitting AADE — see api_accounting_result_history_entry().
        "report": report,
    }
    entries = _read(path)
    entries.insert(0, entry)
    if len(entries) > _MAX_ENTRIES:
        entries = entries[:_MAX_ENTRIES]
    _write(path, entries)
    return entry


def get_history(path: str, limit: int = 20, include_report: bool = False) -> List[Dict[str, Any]]:
    entries = _read(path)
    entries = entries[:limit] if limit else entries
    if include_report:
        return entries
    # The list view doesn't need the (potentially sizeable) full report
    # payload for every row — only the single opened entry does.
    return [{k: v for k, v in e.items() if k != "report"} for e in entries]


def get_entry(path: str, entry_id: str) -> Optional[Dict[str, Any]]:
    for e in _read(path):
        if e.get("id") == entry_id:
            return e
    return None


def get_latest(path: str) -> Dict[str, Any]:
    entries = _read(path)
    return entries[0] if entries else {}


def delete_entry(path: str, entry_id: str) -> bool:
    """Remove one entry. Bulk-run entries (mode="bulk") are deliberately NOT
    deletable through this — they're the record of a shared multi-company
    run, not a single company's own individual computation, and the
    accountant asked that those stay retrievable rather than disappear one
    company at a time. Returns False (no-op) for a bulk entry or a missing
    id, True when something was actually removed."""
    entries = _read(path)
    target = next((e for e in entries if e.get("id") == entry_id), None)
    if not target or target.get("mode") == "bulk":
        return False
    entries = [e for e in entries if e.get("id") != entry_id]
    _write(path, entries)
    return True


def force_delete_entry(path: str, entry_id: str) -> bool:
    """Same as delete_entry but WITHOUT the mode="bulk" protection — used
    only by the bulk-runs "folder" popup's own delete flow, where deleting
    the underlying per-company entries is an explicit, deliberate choice
    the accountant makes (with a separate confirmation) rather than an
    accidental one-off click on a single company's history row."""
    entries = _read(path)
    if not any(e.get("id") == entry_id for e in entries):
        return False
    entries = [e for e in entries if e.get("id") != entry_id]
    _write(path, entries)
    return True


_MAX_BULK_BATCHES = 100


def append_bulk_batch(
    path: str,
    date_from: str,
    date_to: str,
    computed_by: str,
    companies: List[Dict[str, Any]],
    aborted: bool = False,
) -> Dict[str, Any]:
    """Record one Μαζικός run as a single retrievable "folder" entry — the
    per-company results themselves already live in each company's own
    history file (append_entry above, mode="bulk"); this is just the index
    that lets the accountant find "the bulk run from <date>" again and jump
    back into it, since individual bulk entries can't be deleted/browsed
    from a single company's own history view. `companies` is a list of
    {credential_name, vat, entry_id, ok, error} per company in the run —
    entry_id is None for companies that failed."""
    batch = {
        "id": uuid.uuid4().hex,
        "timestamp": datetime.now().isoformat(),
        "date_from": date_from,
        "date_to": date_to,
        "computed_by": computed_by,
        "aborted": bool(aborted),
        "companies": companies,
    }
    batches = _read(path)
    batches.insert(0, batch)
    if len(batches) > _MAX_BULK_BATCHES:
        batches = batches[:_MAX_BULK_BATCHES]
    _write(path, batches)
    return batch


def get_bulk_batches(path: str, limit: int = 30) -> List[Dict[str, Any]]:
    batches = _read(path)
    return batches[:limit] if limit else batches


def get_bulk_batch(path: str, batch_id: str) -> Optional[Dict[str, Any]]:
    for b in _read(path):
        if b.get("id") == batch_id:
            return b
    return None


def delete_bulk_batch(path: str, batch_id: str) -> bool:
    """Remove the batch INDEX entry only — never touches the per-company
    history entries it references (the caller force_delete_entry()s those
    separately, only when the accountant explicitly opted into that)."""
    batches = _read(path)
    if not any(b.get("id") == batch_id for b in batches):
        return False
    batches = [b for b in batches if b.get("id") != batch_id]
    _write(path, batches)
    return True
