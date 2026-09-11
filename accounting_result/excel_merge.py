# -*- coding: utf-8 -*-
"""
accounting_result/excel_merge.py

Thin wrapper around e3.e3_processor.extract_account_leaf_rows(): reshapes the raw
leaf rows of an uploaded Ισοζύγιο Excel (the same file format already used on the
E3-check page) into per-account-group totals, ready to override the myDATA-computed
totals in accounting_result.engine.build_report().
"""
from __future__ import annotations

import re
from typing import Any, Dict

from e3.e3_processor import extract_account_leaf_rows


def parse_accounting_result_excel(path: str) -> Dict[str, Any]:
    result = extract_account_leaf_rows(path)
    if not result.get("ok"):
        return result

    groups: Dict[str, float] = {}
    for row in result.get("leaf_rows", []):
        m = re.match(r"(\d{2})", str(row.get("account") or ""))
        if not m:
            continue
        grp = m.group(1)
        groups[grp] = round(groups.get(grp, 0.0) + float(row.get("amount") or 0.0), 2)

    result["group_totals"] = groups
    return result
