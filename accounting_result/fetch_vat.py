# -*- coding: utf-8 -*-
"""
accounting_result/fetch_vat.py

Live AADE RequestVatInfo pull — the total ΦΠΑ εκροών/εισροών for a period,
independent of myDATA E3 income/expense CHARACTERIZATION. engine.py used to
derive "ΦΠΑ Εισροών" from the local epsilon-characterization cache, which
only covers invoices the accountant has actually tagged — RequestVatInfo
instead returns the raw per-invoice VAT boxes (Vat301-306 = εκροές,
Vat331-336 = εισροές, per the myDATA XSD's InvoiceVatDetailType) for every
submitted invoice regardless of characterization status, so it's the correct
source for the report's bottom-right ΦΠΑ block.
"""
from __future__ import annotations

from typing import Dict, Tuple

import requests

try:
    from lxml import etree as ET
except ImportError:
    import xml.etree.ElementTree as ET


_URL_REQUEST_VAT = "https://mydatapi.aade.gr/myDATA/RequestVatInfo"

# Έντυπο Φ2 (official AADE VAT-return layout, see docs/E3_MAPPING_REFERENCE.md
# neighbourhood / F2_TAXIS form) box pairing, confirmed against the real form:
#   301-306 = ΑΞΙΑ ΕΚΡΟΩΝ (net value of outputs, one box per VAT-rate bucket)
#   331-336 = ΦΟΡΟΣ ΕΚΡΟΩΝ που αναλογεί — the TAX amount for those SAME boxes
#             (i.e. still the output side, not εισροών despite the "33x"
#             numbering looking like a separate block)
#   361-366 = ΑΞΙΑ ΕΙΣΡΟΩΝ με δικαίωμα έκπτωσης (net value of inputs, one box
#             per purchase TYPE: domestic / capital-goods imports / other
#             imports / intra-EU goods / intra-EU services / other reverse-
#             charge)
#   381-386 = ΦΟΡΟΣ ΕΙΣΡΟΩΝ — the TAX amount for those SAME 361-366 boxes.
# So the report's ΦΠΑ ΕΚΡΟΩΝ total = Vat331..336, and ΦΠΑ ΕΙΣΡΟΩΝ total =
# Vat381..386 — NOT Vat301../Vat331.. as the box numbers alone might suggest.
_OUTPUT_TAX_FIELDS = ("Vat331", "Vat332", "Vat333", "Vat334", "Vat335", "Vat336")
_INPUT_TAX_FIELDS = ("Vat381", "Vat382", "Vat383", "Vat384", "Vat385", "Vat386")


def _safe_strip(value) -> str:
    return str(value).strip() if value is not None else ""


def _to_float(value) -> float:
    txt = _safe_strip(value)
    if not txt:
        return 0.0
    txt = txt.replace(" ", "").replace(",", ".")
    try:
        return float(txt)
    except Exception:
        return 0.0


def _local_name(tag) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _find_text_by_localnames(elem, names: set) -> str:
    if elem is None:
        return ""
    for sub in elem.iter():
        if _local_name(sub.tag) in names:
            text = _safe_strip(sub.text)
            if text:
                return text
    return ""


def _extract_pagination_cursors(root) -> Dict[str, str]:
    cursors = {"nextPartitionKey": "", "nextRowKey": "", "continuationToken": ""}
    for elem in root.iter():
        lname = _local_name(elem.tag)
        text = _safe_strip(elem.text)
        if text and lname in cursors and not cursors[lname]:
            cursors[lname] = text
    return cursors


def fetch_vat_totals(
    vat: str, date_from: str, date_to: str, aade_user: str, aade_key: str, debug: bool = False,
) -> Tuple[float, float]:
    """Returns (vat_outflow, vat_inflow) — total net ΦΠΑ εκροών/εισροών for the
    period, summed across every non-cancelled invoice AADE has on file for
    `vat`, regardless of myDATA characterization status. `date_from`/`date_to`
    must already be dd/mm/yyyy (use engine.to_ddmmyyyy)."""
    headers = {"aade-user-id": aade_user, "Ocp-Apim-Subscription-Key": aade_key}
    params = {
        "entityVatNumber": vat,
        "dateFrom": date_from,
        "dateTo": date_to,
        "GroupedPerDay": "false",
    }

    ekroon = 0.0
    eisroon = 0.0
    seen_marks = set()

    while True:
        # See fetch_e3.py's fetch_e3_entries for why this needs an explicit
        # timeout: without one, a hung/slow AADE response surfaces as a raw
        # HTML gateway-timeout page instead of a JSON error Flask can catch.
        resp = requests.get(_URL_REQUEST_VAT, params=params, headers=headers, timeout=60)
        if debug:
            print(f"[RequestVatInfo] Status: {resp.status_code}")
        if resp.status_code != 200 or not resp.content:
            break

        try:
            root = ET.fromstring(resp.content)
        except Exception:
            break

        for node in root.iter():
            if _local_name(node.tag).lower() != "vatinfo":
                continue

            is_cancelled = _find_text_by_localnames(node, {"IsCancelled"}).strip().lower()
            if is_cancelled == "true":
                continue

            mark = _find_text_by_localnames(node, {"Mark"})
            dedup_key = mark or id(node)
            if dedup_key in seen_marks:
                continue
            seen_marks.add(dedup_key)

            for field in _OUTPUT_TAX_FIELDS:
                ekroon += _to_float(_find_text_by_localnames(node, {field}))
            for field in _INPUT_TAX_FIELDS:
                eisroon += _to_float(_find_text_by_localnames(node, {field}))

        cursors = _extract_pagination_cursors(root)
        next_partition_key = cursors.get("nextPartitionKey") or ""
        next_row_key = cursors.get("nextRowKey") or ""
        continuation_token = cursors.get("continuationToken") or ""

        if next_partition_key or next_row_key:
            params.pop("continuationToken", None)
            if next_partition_key:
                params["nextPartitionKey"] = next_partition_key
            else:
                params.pop("nextPartitionKey", None)
            if next_row_key:
                params["nextRowKey"] = next_row_key
            else:
                params.pop("nextRowKey", None)
            continue

        if continuation_token:
            params.pop("nextPartitionKey", None)
            params.pop("nextRowKey", None)
            params["continuationToken"] = continuation_token
            continue

        break

    return round(ekroon, 2), round(eisroon, 2)
