# -*- coding: utf-8 -*-
"""
accounting_result/inventory_store.py

Per-company, per-fiscal-year closing/opening inventory (απογραφή), the one piece
of the Λογιστικό Αποτέλεσμα report myDATA cannot supply (it's a physical stocktake,
not a transaction flow). Nothing like this existed anywhere in the app before this
feature, so year N's closing value is persisted here and read back as year N+1's
opening value automatically.

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


def get_opening_inventory(path: str, year: int) -> Dict[str, float]:
    """Returns this year's explicitly-confirmed opening inventory if one has
    been set (see set_opening_inventory), otherwise falls back to year-1's
    closing inventory, or {} if neither is known."""
    rec = get_year_record(path, year)
    if rec and isinstance(rec.get("opening_inventory"), dict) and rec["opening_inventory"]:
        return {k: float(v) for k, v in rec["opening_inventory"].items()}
    prev = get_year_record(path, year - 1)
    if prev and isinstance(prev.get("closing_inventory"), dict):
        return {k: float(v) for k, v in prev["closing_inventory"].items()}
    return {}


def set_opening_inventory(path: str, year: int, values: Dict[str, float]) -> Dict[str, Any]:
    """Explicitly confirms/overrides this year's opening inventory (the
    accountant reviewing/correcting the carried-forward figure at the start
    of a new fiscal year, rather than the report silently trusting whatever
    was auto-seeded from last year's closing). Raises InventoryStoreCorruptError
    rather than write if the existing file can't be read — see _read."""
    data = _read(path)
    years = data.setdefault("years", {})
    rec = years.setdefault(str(year), {})
    rec["opening_inventory"] = {k: round(float(v), 2) for k, v in (values or {}).items()}
    rec["opening_confirmed_at"] = datetime.now().isoformat()
    _write(path, data)
    return rec


def resolve_or_flag_closing_inventory(path: str, year: int) -> Tuple[Optional[Dict[str, float]], Dict[str, float], bool]:
    """Returns (closing_inventory_or_None, opening_inventory, needs_user_input)."""
    rec = get_year_record(path, year)
    opening = get_opening_inventory(path, year)
    if rec and isinstance(rec.get("closing_inventory"), dict) and rec["closing_inventory"]:
        return rec["closing_inventory"], opening, False
    return None, opening, True


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
    if "opening_inventory" not in rec:
        rec["opening_inventory"] = get_opening_inventory(path, year)

    # Auto-seed next year's opening inventory, without clobbering it if the
    # accountant already set it independently.
    next_rec = years.setdefault(str(year + 1), {})
    if "opening_inventory" not in next_rec:
        next_rec["opening_inventory"] = rec["closing_inventory"]

    _write(path, data)
    return rec
