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


def get_vat_profile(path: str) -> Dict[str, Any]:
    return dict(_read(path).get("vat_profile") or {})


def set_vat_profile(
    path: str,
    vat_subject: Optional[bool],
    books_category: str = "",
    vat_period_type: str = "",
    source: str = "manual",
) -> Dict[str, Any]:
    """`vat_subject`: True/False once known, None to explicitly clear (treat
    as unknown -> the report defaults to showing the ΦΠΑ block, i.e. the
    same behavior as before this feature existed)."""
    data = _read(path)
    profile = {
        "vat_subject": vat_subject,
        "books_category": str(books_category or ""),
        "vat_period_type": str(vat_period_type or ""),  # "monthly" | "quarterly" | ""
        "source": source,
        "updated_at": datetime.now().isoformat(),
    }
    data["vat_profile"] = profile
    _write(path, data)
    return profile
