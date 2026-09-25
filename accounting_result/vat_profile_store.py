# -*- coding: utf-8 -*-
"""
accounting_result/vat_profile_store.py

Per-company ΦΠΑ profile (subject to VAT or not, κατηγορία βιβλίων, filing
frequency) — a company-level attribute, not period-specific, so it lives
alongside (not instead of) the per-year opening/closing inventory already
stored by inventory_store.py, in the SAME file
(data/<group>/accounting_result/<AFM>.json, via the caller-supplied path).
Both modules do a full read-modify-write of that file, so they coexist
safely as long as neither ever replaces the whole document — only reads
already do that (`{"years": {}}` default when the file is missing/corrupt).

Populated either manually or via detect_and_store_vat_profile() in app.py,
which pulls κατηγορία βιβλίων / ΦΠΑ υπαγωγή straight from the ΑΑΔΕ Μητρώο
(e3/checks/aade_profile.py — the same TAXISnet-login-based fetch already
used for the address-retrieval fallback elsewhere in the app).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

_UNSET = object()


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
    # PID-namespaced tmp filename — this file is shared with
    # inventory_store.py/compliance_notes_store.py (same per-company JSON),
    # which both already got this fix after a bare ".tmp" name let two
    # processes writing the same company at once silently drop one write.
    tmp = path + "." + str(os.getpid()) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def get_vat_profile(path: str) -> Dict[str, Any]:
    return dict(_read(path).get("vat_profile") or {})


def set_vat_profile(
    path: str,
    vat_subject: Optional[bool],
    books_category: str = "",
    vat_period_type: str = "",
    source: str = "manual",
    legal_kind: Any = _UNSET,
    vat_regime: Any = _UNSET,
    vat_entry_mode: Any = _UNSET,
    small_business_exemption: Any = _UNSET,
) -> Dict[str, Any]:
    """`vat_subject`: True/False once known, None to explicitly clear (treat
    as unknown -> the report defaults to showing the ΦΠΑ block, i.e. the
    same behavior as before this feature existed).

    `legal_kind`: "sole_proprietor" | "legal_entity" | None (unknown), only
    ever set by the ΑΑΔΕ Μητρώο auto-detect path (_ar_detect_vat_profile_for_afm
    in app.py) — drives which single ΕΦΚΑ Μη-Μισθωτών exception reason the
    accounting-result UI offers instead of always showing both. Left as the
    _UNSET default (rather than None) so the MANUAL profile-edit endpoint,
    which only ever touches vat_subject/books_category/vat_period_type,
    doesn't wipe out a previously auto-detected legal_kind just by calling
    this with its own defaults."""
    data = _read(path)
    existing = data.get("vat_profile") if isinstance(data.get("vat_profile"), dict) else {}
    profile = {
        "vat_subject": vat_subject,
        "books_category": str(books_category or ""),
        "vat_period_type": str(vat_period_type or ""),  # "monthly" | "quarterly" | ""
        "legal_kind": existing.get("legal_kind") if legal_kind is _UNSET else legal_kind,
        # ΑΑΔΕ «Καθεστώς ΦΠΑ» text (e.g. "ΕΙΔΙΚΟ ΕΓΧΩΡΙΟ ΚΑΘΕΣΤΩΣ ΜΙΚΡΩΝ
        # ΕΠΙΧΕΙΡΗΣΕΩΝ") — same _UNSET rule as legal_kind.
        "vat_regime": existing.get("vat_regime") if vat_regime is _UNSET else str(vat_regime or ""),
        # ΑΑΔΕ «Τρόπος Ένταξης ΦΠΑ» (ΥΠΟΧΡΕΩΤΙΚΑ / ΠΡΟΑΙΡΕΤΙΚΑ).
        "vat_entry_mode": existing.get("vat_entry_mode") if vat_entry_mode is _UNSET else str(vat_entry_mode or ""),
        # Απαλλαγή μικρών επιχειρήσεων (όριο 10.000€), derived at detect time.
        "small_business_exemption": existing.get("small_business_exemption") if small_business_exemption is _UNSET else bool(small_business_exemption),
        "source": source,
        "updated_at": datetime.now().isoformat(),
    }
    data["vat_profile"] = profile
    _write(path, data)
    return profile
