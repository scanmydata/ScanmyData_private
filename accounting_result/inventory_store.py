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


def _read(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"years": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("years"), dict):
            return {"years": {}}
        return data
    except Exception:
        return {"years": {}}


def _write(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def get_year_record(path: str, year: int) -> Optional[Dict[str, Any]]:
    return _read(path).get("years", {}).get(str(year))


def get_opening_inventory(path: str, year: int) -> Dict[str, float]:
    """Returns year-1's closing inventory, or {} if unknown."""
    prev = get_year_record(path, year - 1)
    if prev and isinstance(prev.get("closing_inventory"), dict):
        return {k: float(v) for k, v in prev["closing_inventory"].items()}
    return {}


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
