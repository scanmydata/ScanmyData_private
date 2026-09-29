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


def upsert_bulk_batch(path: str, batch: Dict[str, Any]) -> Dict[str, Any]:
    """Insert or replace (by id) one bulk-run index entry. Used to write the
    run's index at its START and refresh it after every company, so a run
    whose HTTP request dies midway (gateway timeout, restart) still leaves a
    retrievable/deletable folder instead of orphaned per-company entries."""
    batches = [b for b in _read(path) if b.get("id") != batch.get("id")]
    batches.append(batch)
    batches.sort(key=lambda b: str(b.get("timestamp") or ""), reverse=True)
    if len(batches) > _MAX_BULK_BATCHES:
        batches = batches[:_MAX_BULK_BATCHES]
    _write(path, batches)
    return batch


def recover_orphan_bulk_batches(path: str, history_dir: str, gap_minutes: int = 30) -> int:
    """Re-index mode="bulk" company-history entries that no bulk-run folder
    references (runs whose index was never written because the request died
    before the end, or folders deleted index-only). Entries are grouped into
    one recovered folder per run: same period, consecutive timestamps no
    more than `gap_minutes` apart. Returns how many folders were added."""
    batches = _read(path)
    referenced = {str(c.get("entry_id")) for b in batches for c in (b.get("companies") or []) if c.get("entry_id")}
    orphans: List[Dict[str, Any]] = []
    if os.path.isdir(history_dir):
        for fname in os.listdir(history_dir):
            if not fname.endswith(".json"):
                continue
            for e in _read(os.path.join(history_dir, fname)):
                if e.get("mode") == "bulk" and e.get("id") and str(e.get("id")) not in referenced:
                    orphans.append(e)
    if not orphans:
        return 0
    orphans.sort(key=lambda e: str(e.get("timestamp") or ""))
    groups: List[List[Dict[str, Any]]] = []
    for e in orphans:
        g = groups[-1] if groups else None
        if g:
            last = g[-1]
            same_period = (last.get("date_from"), last.get("date_to")) == (e.get("date_from"), e.get("date_to"))
            try:
                gap = (datetime.fromisoformat(str(e.get("timestamp"))) - datetime.fromisoformat(str(last.get("timestamp")))).total_seconds()
            except Exception:
                gap = gap_minutes * 60 + 1
            # The same company showing up again means a new run started.
            repeat = any(str(x.get("vat")) == str(e.get("vat")) for x in g)
            if same_period and gap <= gap_minutes * 60 and not repeat:
                g.append(e)
                continue
        groups.append([e])
    for g in groups:
        batches.append({
            "id": "recovered-" + str(g[0].get("id")),
            "timestamp": g[0].get("timestamp"),
            "date_from": g[0].get("date_from"),
            "date_to": g[0].get("date_to"),
            "computed_by": g[0].get("computed_by"),
            "aborted": False,
            "recovered": True,
            "companies": [{
                "credential_name": e.get("credential_name"), "vat": e.get("vat"),
                "entry_id": e.get("id"), "ok": True, "error": None,
            } for e in g],
        })
    batches.sort(key=lambda b: str(b.get("timestamp") or ""), reverse=True)
    _write(path, batches)
    return len(groups)


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
