# -*- coding: utf-8 -*-
"""
accounting_result/inventory_store.py

Per-company, per-fiscal-year CLOSING inventory (απόθεμα λήξης) — the one piece
of the Λογιστικό Αποτέλεσμα report myDATA cannot supply for the year currently
being examined (it's a physical stocktake, not a transaction flow), so it's the
only inventory figure a user ever enters or edits in this app.

A stored value is SINGLE-USE: once a computation has actually consumed it to
build a report, it's cleared (see clear_closing_inventory) so the next
computation of that same year asks again instead of silently reusing it —
the physical count it represents can change between runs (a correction, a
recount). Already-COMPUTED reports are unaffected: each one's own
closing_inventory is captured into accounting_result/history_store.py at
compute time, independent of whatever this store holds afterward.

OPENING inventory is NOT stored here at all: it's always the prior year's
already-declared myDATA/Ε3 closing stock, re-derived fresh from AADE on every
computation (see engine.extract_prior_year_closing_inventory) rather than
carried forward through this file — that AADE figure is authoritative and
never needs (or allows) manual entry.

One JSON file per company at data/<group>/accounting_result/<AFM>.json (via the
caller-supplied path, following the app's group_path() convention).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


class InventoryStoreCorruptError(Exception):
    """Raised when an existing inventory file can't be parsed. Deliberately
    NOT swallowed into an empty {"years": {}} structure by callers that then
    write — two processes hitting the same company's file at once (e.g. a
    dev/test instance and the live server sharing the same data/ directory)
    previously produced a read that silently fell back to "empty", which a
    write-path caller then persisted, wiping out the other process's
    legitimately-stored closing/opening inventory. This is the same class of
    bug the credentials store had before it got the atomic-write +
    refuse-on-corrupt-read treatment; this store gets the same fix rather
    than risk a second incident with different data."""


def _read(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"years": {}}
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        data = json.loads(raw)
    except Exception as e:
        raise InventoryStoreCorruptError(f"Corrupt inventory file at {path}: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("years"), dict):
        raise InventoryStoreCorruptError(f"Malformed inventory file at {path}")
    return data


def _read_or_empty(path: str) -> Dict[str, Any]:
    """Read-only callers degrade to "unknown" on corruption rather than
    blocking the report entirely — they never write, so there's nothing to
    lose. Only used by accessors that don't persist anything."""
    try:
        return _read(path)
    except InventoryStoreCorruptError:
        return {"years": {}}


def _write(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + "." + str(os.getpid()) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def get_year_record(path: str, year: int) -> Optional[Dict[str, Any]]:
    return _read_or_empty(path).get("years", {}).get(str(year))


def resolve_or_flag_closing_inventory(path: str, year: int) -> Tuple[Optional[Dict[str, float]], bool]:
    """Returns (closing_inventory_or_None, needs_user_input)."""
    rec = get_year_record(path, year)
    if rec and isinstance(rec.get("closing_inventory"), dict) and rec["closing_inventory"]:
        return rec["closing_inventory"], False
    return None, True


def set_closing_inventory(
    path: str, year: int, values: Dict[str, float], method: str,
    period_from: Optional[str] = None, period_to: Optional[str] = None,
) -> Dict[str, Any]:
    data = _read(path)
    years = data.setdefault("years", {})
    y = str(year)
    rec = years.setdefault(y, {})
    rec["closing_inventory"] = {k: round(float(v), 2) for k, v in (values or {}).items()}
    rec["method"] = method
    rec["period_from"] = period_from
    rec["period_to"] = period_to
    rec["computed_at"] = datetime.now().isoformat()
    _write(path, data)
    return rec


def clear_closing_inventory(path: str, year: int) -> None:
    """Deletes a year's closing-stock record once a computation has actually
    consumed it to build a report — see the module docstring for why: the
    value is single-use, not a standing answer. Best-effort: a corrupt file
    is left alone rather than raised here, since the caller has already
    finished building and saving the report by the time this runs, and
    resolve_or_flag_closing_inventory degrading to needs_user_input=True on
    the same corruption is a safe-enough fallback for the next computation."""
    try:
        data = _read(path)
    except InventoryStoreCorruptError:
        return
    years = data.get("years")
    if not isinstance(years, dict) or str(year) not in years:
        return
    del years[str(year)]
    _write(path, data)
