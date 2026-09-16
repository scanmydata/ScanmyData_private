# -*- coding: utf-8 -*-
"""
accounting_result/compliance_notes_store.py

Per-company, per-year resolutions for the monthly-completeness checks this
module backs:
  - payroll (μισθοδοσία, Ε3 code 581)
  - rent (ενοίκια, Ε3 code 585/014)
  - ΕΦΚΑ Μη-Μισθωτών (self-employed social security, Ε3 code 585/007)

Both checks (see accounting_result.engine.check_monthly_completeness)
compare the number of DISTINCT myDATA submission marks found for their code
against the number of calendar months in the period under examination —
fewer marks than months means some months look like they're missing. This
store holds what the accountant decided to do about that, per (company,
year).

These behave differently on purpose:
  - payroll_checks and rent_checks are SINGLE-USE, exactly like
    inventory_store's closing stock: app.py clears the record the moment
    it's consumed by a computation (see clear_payroll_check/clear_rent_check),
    so the accountant is asked again on every future computation of that
    company/year rather than the decision being silently remembered forever.
  - efka_self_employed_checks is a STANDING exception: it stays saved until
    explicitly cleared, because the reasons it records (sole proprietor also
    employed elsewhere, company partners exempt via their own sole
    proprietorships) are structural facts about the company, not something
    that needs re-confirming on every computation.

Lives in the SAME per-company file as inventory_store.py / vat_profile_store.py
(data/<group>/accounting_result/<AFM>.json) under two new top-level keys,
"payroll_checks" and "efka_self_employed_checks", each a {year: {...}} map —
a full read-modify-write of the whole document, coexisting safely with the
other two modules' own top-level keys the same way they already do with
each other (see vat_profile_store's module docstring for why that's safe).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional


def _read(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def get_payroll_check(path: str, year: int) -> Dict[str, Any]:
    return dict((_read(path).get("payroll_checks") or {}).get(str(year)) or {})


def set_payroll_check(
    path: str,
    year: int,
    resolution: str,
    monthly_totals: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """`resolution`: "skip" (proceed as-is for THIS computation) or "manual"
    (the accountant keyed in totals for the missing months, stored in
    `monthly_totals` as {"YYYY-MM": amount} and ADDED on top of whatever
    myDATA itself already reported for code 581 — see
    accounting_result.engine.build_report's payroll_manual_addition). Either
    way this is single-use: app.py calls clear_payroll_check right after the
    resulting report is built, so the next computation of this company/year
    asks again instead of silently reusing the same answer forever."""
    data = _read(path)
    checks = data.setdefault("payroll_checks", {})
    rec = {
        "resolution": resolution,
        "monthly_totals": {k: round(float(v), 2) for k, v in (monthly_totals or {}).items()},
        "updated_at": datetime.now().isoformat(),
    }
    checks[str(year)] = rec
    data["payroll_checks"] = checks
    _write(path, data)
    return rec


def clear_payroll_check(path: str, year: int) -> None:
    """Called by app.py right after a resolved payroll_check has been
    consumed to build a report — see set_payroll_check's docstring for why
    this is single-use rather than a standing exception."""
    data = _read(path)
    checks = data.get("payroll_checks")
    if isinstance(checks, dict) and str(year) in checks:
        del checks[str(year)]
        _write(path, data)


def get_rent_check(path: str, year: int) -> Dict[str, Any]:
    return dict((_read(path).get("rent_checks") or {}).get(str(year)) or {})


def set_rent_check(
    path: str,
    year: int,
    resolution: str,
    monthly_totals: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Same shape and single-use lifecycle as set_payroll_check, for ενοίκια
    (Ε3 code 585/014): `resolution` "skip" or "manual" (monthly_totals ADDED
    on top of myDATA's own group-62 total — see engine.build_report's
    rent_manual_addition). Cleared by clear_rent_check right after use."""
    data = _read(path)
    checks = data.setdefault("rent_checks", {})
    rec = {
        "resolution": resolution,
        "monthly_totals": {k: round(float(v), 2) for k, v in (monthly_totals or {}).items()},
        "updated_at": datetime.now().isoformat(),
    }
    checks[str(year)] = rec
    data["rent_checks"] = checks
    _write(path, data)
    return rec


def clear_rent_check(path: str, year: int) -> None:
    """Called by app.py right after a resolved rent_check has been consumed
    to build a report — see set_rent_check's docstring."""
    data = _read(path)
    checks = data.get("rent_checks")
    if isinstance(checks, dict) and str(year) in checks:
        del checks[str(year)]
        _write(path, data)


def get_efka_self_employed_check(path: str, year: int) -> Dict[str, Any]:
    return dict((_read(path).get("efka_self_employed_checks") or {}).get(str(year)) or {})


def set_efka_self_employed_check(path: str, year: int, reason: str) -> Dict[str, Any]:
    """`reason`: "sole_prop_also_employed" (ατομική επιχείρηση — ο πελάτης
    είναι παράλληλα μισθωτός αλλού) | "company_partners_exempt" (εταιρία —
    οι εταίροι δεν είναι υπόχρεοι σε ΕΦΚΑ Μη-Μισθωτών εδώ, γιατί έχουν δικές
    τους ατομικές επιχειρήσεις όπου καταχωρείται/υπολογίζεται ο ΕΦΚΑ τους).
    Saving any non-empty reason silences the auto-note for this company/year
    on future computations; pass "" to clear it back to pending (the note
    reappears on the next compute if the shortfall still holds)."""
    data = _read(path)
    checks = data.setdefault("efka_self_employed_checks", {})
    rec = {"reason": str(reason or ""), "updated_at": datetime.now().isoformat()}
    checks[str(year)] = rec
    data["efka_self_employed_checks"] = checks
    _write(path, data)
    return rec
