import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Tuple

import requests

try:
    from lxml import etree as ET
except ImportError:
    import xml.etree.ElementTree as ET

from ..e3_field_map import E3_FIELD_MAP


_URL_REQUEST_E3 = "https://mydatapi.aade.gr/myDATA/RequestE3Info"
_CLASS_TYPE_RE = re.compile(r"E3_(\d{3})(?:_(\d{3}))?", re.IGNORECASE)


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


def _extract_invoice_type(invoice_node) -> str:
    if invoice_node is None:
        return ""
    type_names = {
        "invoiceType",
        "InvoiceType",
        "invoiceTypeCode",
        "InvoiceTypeCode",
        "documentType",
        "docType",
        "document_type",
        "doctype",
        "invoice_category",
        "documentCategory",
        "type",
        "invoiceKind",
        "kind",
    }
    return _find_text_by_localnames(invoice_node, type_names)


def _extract_pagination_cursors(root) -> Dict[str, str]:
    cursors = {
        "nextPartitionKey": "",
        "nextRowKey": "",
        "nextPartitionToken": "",
    }
    for elem in root.iter():
        lname = _local_name(elem.tag)
        text = _safe_strip(elem.text)
        if text and lname in cursors and not cursors[lname]:
            cursors[lname] = text
    return cursors


def _iter_classification_nodes(invoice_node):
    target_names = {
        "incomeClassificationDetailData",
        "expensesClassificationDetailData",
        "incomeClassification",
        "expensesClassification",
        "IncomeClassification",
        "ExpensesClassification",
    }
    for node in invoice_node.iter():
        if _local_name(node.tag) in target_names:
            yield node


def _parse_ddmmyyyy(s: str) -> date:
    return datetime.strptime(_safe_strip(s), "%d/%m/%Y").date()


def _fmt_ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _quarter_chunks(date_from: str, date_to: str) -> List[Tuple[str, str]]:
    """Split [date_from, date_to] into ≤92-day quarterly sub-ranges.

    Why: AADE's RequestE3Info returns the same entry across multiple
    paging windows when the range spans many months (observed for the
    full-year 2025 range on ΑΦΜ 036209456 in 06/2026 — page 4 and page 5
    both started with mark 400011925882593). With a yearly fetch + tuple
    dedup we accidentally dropped legitimate line items of the same
    invoice that happened to share an amount, mismatching the official
    AADE PDF totals by €1.4k on κωδ. 561 and €3k on κωδ. 102.

    Splitting into quarters keeps each paged result small enough that
    cross-page overlap doesn't happen, and a mark can only appear in
    one quarter (it has exactly one IssueDate), so quarter results are
    naturally disjoint.
    """
    try:
        start = _parse_ddmmyyyy(date_from)
        end = _parse_ddmmyyyy(date_to)
    except Exception:
        return [(date_from, date_to)]
    if end <= start:
        return [(date_from, date_to)]

    chunks: List[Tuple[str, str]] = []
    cursor = start
    while cursor <= end:
        year = cursor.year
        month = cursor.month
        q_start_month = ((month - 1) // 3) * 3 + 1
        q_start = date(year, q_start_month, 1)
        # Last day of the quarter
        if q_start_month + 3 > 12:
            q_end = date(year, 12, 31)
        else:
            q_end = date(year, q_start_month + 3, 1) - timedelta(days=1)
        chunk_from = max(cursor, q_start)
        chunk_to = min(end, q_end)
        chunks.append((_fmt_ddmmyyyy(chunk_from), _fmt_ddmmyyyy(chunk_to)))
        cursor = q_end + timedelta(days=1)
    return chunks


def fetch_e3_entries(mark: str, date_from: str, date_to: str, aade_user: str, aade_key: str, debug: bool = False) -> List[dict]:
    """Fetch and normalize RequestE3Info rows.

    Returns rows in the form:
      {
        "invoice_mark": "...",
        "code": "585",
        "sub_code": "016" or "",
        "classification_type": "E3_585_016",
        "classification_category": "...",
        "amount": 123.45,
      }

    Splits the range into quarterly sub-fetches automatically to avoid
    AADE's cross-page overlap on long ranges (see `_quarter_chunks`).
    """
    # Only chunk if a real range was given. The bulk callers pass
    # mark="0" + dates; if a specific mark is passed we don't chunk
    # because that lookup is for a single document.
    if _safe_strip(mark) and _safe_strip(mark) != "0":
        return _fetch_e3_entries_single_range(mark, date_from, date_to, aade_user, aade_key, debug=debug)

    chunks = _quarter_chunks(date_from, date_to)
    if len(chunks) <= 1:
        return _fetch_e3_entries_single_range(mark, date_from, date_to, aade_user, aade_key, debug=debug)

    if debug:
        print(f"[RequestE3Info] Splitting {date_from}–{date_to} into {len(chunks)} quarters")
    out: List[dict] = []
    for cf, ct in chunks:
        out.extend(_fetch_e3_entries_single_range(mark, cf, ct, aade_user, aade_key, debug=debug))
    return out


def _fetch_e3_entries_single_range(mark: str, date_from: str, date_to: str, aade_user: str, aade_key: str, debug: bool = False) -> List[dict]:
    """Inner fetcher for a single date range with its own pagination + dedup.

    Dedup tuple is (mark, classification_type, amount, category). This
    catches legitimate cross-page duplicates (AADE returns the same
    line in >1 paged windows on long ranges) without merging real
    line items, because callers chunk the range into quarters first
    via `fetch_e3_entries`.
    """
    headers = {
        "aade-user-id": aade_user,
        "Ocp-Apim-Subscription-Key": aade_key,
    }

    params = {
        "mark": _safe_strip(mark) or "0",
        "dateFrom": date_from,
        "dateTo": date_to,
    }

    all_entries: List[dict] = []
    seen_entries = set()

    while True:
        resp = requests.get(_URL_REQUEST_E3, params=params, headers=headers)
        if debug:
            print(f"[RequestE3Info] Status: {resp.status_code}")

        if resp.status_code != 200:
            if debug:
                print(f"[RequestE3Info] HTTP {resp.status_code}")
            break

        if not resp.content:
            break

        try:
            root = ET.fromstring(resp.content)
        except Exception:
            break

        invoice_node_names = {"expensesInvoiceClassification", "incomeInvoiceClassification", "E3Info"}
        for invoice_node in root.iter():
            if _local_name(invoice_node.tag) not in invoice_node_names:
                continue

            invoice_mark = _find_text_by_localnames(invoice_node, {"invoiceMark", "V_Mark"})
            if not invoice_mark:
                blob = "".join([_safe_strip(x.text) for x in invoice_node.iter() if x.text])
                match_mark = re.search(r"(\d{15})", blob)
                invoice_mark = match_mark.group(1) if match_mark else ""

            node_candidates = list(_iter_classification_nodes(invoice_node))
            if not node_candidates and _local_name(invoice_node.tag) == "E3Info":
                node_candidates = [invoice_node]

            invoice_type = _extract_invoice_type(invoice_node)

            for cls_node in node_candidates:
                cls_type = _find_text_by_localnames(cls_node, {"classificationType", "V_Class_Type"})
                m = _CLASS_TYPE_RE.search(_safe_strip(cls_type))
                if not m:
                    continue

                # RequestE3Info returns value fields as V_Class_Value in E3Info nodes.
                amount_text = _find_text_by_localnames(
                    cls_node,
                    {"amount", "V_Amount", "V_Class_Value", "classValue", "classificationValue"},
                )
                amount = _to_float(amount_text)
                if amount == 0:
                    continue

                invoice_type_str = str(invoice_type or "").strip()
                is_negative_type = bool(re.search(r"\b(5\.1|5\.2|11\.4)\b", invoice_type_str))
                negative_keywords = {"πιστωτ", "πιστωτικό", "πιστωτικο", "credit"}
                if not is_negative_type:
                    lower_type = invoice_type_str.lower()
                    is_negative_type = any(keyword in lower_type for keyword in negative_keywords)

                if is_negative_type:
                    amount = -abs(amount)

                # If the source amount is already negative, preserve it as subtraction.
                if amount < 0:
                    amount = -abs(amount)

                category = _find_text_by_localnames(cls_node, {"classificationCategory", "V_Class_Category"})
                # Dedupe across wrapper nodes for the same invoice. AADE
                # returns each classification line inside multiple parent
                # blocks; the (mark, cls_type, amount, category) tuple is
                # the natural unit of identity for a line item.
                dedup_key = (
                    _safe_strip(invoice_mark),
                    _safe_strip(cls_type).upper(),
                    round(amount, 2),
                    _safe_strip(category).upper(),
                )
                if dedup_key in seen_entries:
                    continue
                seen_entries.add(dedup_key)
                all_entries.append(
                    {
                        "invoice_mark": _safe_strip(invoice_mark),
                        "code": m.group(1),
                        "sub_code": m.group(2) or "",
                        "classification_type": _safe_strip(cls_type).upper(),
                        "classification_category": _safe_strip(category).upper(),
                        "amount": round(amount, 2),
                    }
                )

        cursors = _extract_pagination_cursors(root)
        next_partition_key = cursors.get("nextPartitionKey") or ""
        next_row_key = cursors.get("nextRowKey") or ""
        next_partition_token = cursors.get("nextPartitionToken") or ""

        if next_partition_key or next_row_key:
            params.pop("nextPartitionToken", None)
            if next_partition_key:
                params["nextPartitionKey"] = next_partition_key
            else:
                params.pop("nextPartitionKey", None)
            if next_row_key:
                params["nextRowKey"] = next_row_key
            else:
                params.pop("nextRowKey", None)
            continue

        if next_partition_token:
            params.pop("nextPartitionKey", None)
            params.pop("nextRowKey", None)
            params["nextPartitionToken"] = next_partition_token
            continue

        break

    return all_entries


def fetch_mark_classification_map(mark: str, date_from: str, date_to: str, aade_user: str, aade_key: str, debug: bool = False) -> Dict[str, str]:
    """Return mark -> classification using inverted rule requested by user.

    Rule:
    - If category starts with "ΜΗ" and contains "ΧΑΡΑΚΤΗΡΙΣΜ", mark is treated as "αχαρακτηριστο".
    - Otherwise, mark is treated as "χαρακτηρισμενο".
    """
    entries = fetch_e3_entries(mark, date_from, date_to, aade_user, aade_key, debug=debug)
    mark_class: Dict[str, str] = {}

    for row in entries:
        mk = _safe_strip(row.get("invoice_mark"))
        if not mk:
            continue
        category = _safe_strip(row.get("classification_category")).upper()

        classification = "χαρακτηρισμενο"
        if category.startswith("ΜΗ") and "ΧΑΡΑΚΤΗΡΙΣΜ" in category:
            classification = "αχαρακτηριστο"

        prev = mark_class.get(mk)
        # Prefer keeping αχαρακτηριστο if it appears for the mark.
        if prev != "αχαρακτηριστο":
            mark_class[mk] = classification

    return mark_class


def _row_for_code(code: str, amount: float) -> dict:
    code_int = int(code)
    meta = E3_FIELD_MAP.get(code_int, {})
    label = meta.get("label") or f"Κωδικός {code}"
    return {
        "code": str(code_int),
        "description": label,
        # Before Excel upload, MYDATA values fill only the MYDATA column.
        "e3_value": 0.0,
        "mydata_value": round(amount, 2),
        "diff": 0.0,
    }


def build_e3_report(entries: List[dict]) -> dict:
    totals_by_code = defaultdict(float)
    totals_by_sub = defaultdict(float)

    for row in entries:
        code = _safe_strip(row.get("code"))
        if not code.isdigit():
            continue
        amount = _to_float(row.get("amount"))
        if amount == 0:
            continue
        totals_by_code[code] += amount

        sub = _safe_strip(row.get("sub_code"))
        if sub:
            totals_by_sub[(code, sub)] += amount

    revenue_codes = [str(i) for i in range(561, 571)]
    purchase_codes = ["102", "202", "302", "313"]
    expense_codes = [str(i) for i in range(581, 590)]

    def _sub_sum(code: str, sub_keys: List[str]) -> float:
        return round(sum(totals_by_sub.get((code, sk), 0.0) for sk in sub_keys), 2)

    def _meta_subcodes(code: str) -> Dict[str, str]:
        try:
            return dict((E3_FIELD_MAP.get(int(code), {}) or {}).get("sub_codes") or {})
        except Exception:
            return {}

    def _append_subrows(rows: List[dict], code: str, parent_code: str = None) -> None:
        sub_codes = _meta_subcodes(code)
        if not sub_codes:
            return

        parent = parent_code or code
        for sub_key in sorted(sub_codes.keys()):
            rows.append(
                {
                    "code": f"{code}.{sub_key}",
                    "description": sub_codes[sub_key],
                    "e3_value": 0.0,
                    "mydata_value": _sub_sum(code, [sub_key]),
                    "diff": 0.0,
                    "is_subrow": True,
                    "parent_code": parent,
                }
            )

    revenue_rows = []
    revenue_rows.append(_row_for_code("561", totals_by_code.get("561", 0.0)))
    _append_subrows(revenue_rows, "561")
    for code in revenue_codes:
        if code == "561":
            continue
        revenue_rows.append(_row_for_code(code, totals_by_code.get(code, 0.0)))

    expense_rows = []
    expense_rows.append({"description": "Σύνολο Αγορών και Εξόδων", "is_group": True})

    purchases_total = 0.0
    for code in purchase_codes:
        row = _row_for_code(code, totals_by_code.get(code, 0.0))
        row["is_subrow"] = True
        expense_rows.append(row)
        purchases_total += float(totals_by_code.get(code, 0.0))
        _append_subrows(expense_rows, code)

    expense_rows.append(
        {
            "code": "",
            "description": "Σύνολο Αγορών",
            "e3_value": 0.0,
            "mydata_value": round(purchases_total, 2),
            "diff": 0.0,
        }
    )

    expense_rows.append({"description": "Σύνολο Έξοδα", "is_group": True})
    for code in expense_codes:
        row = _row_for_code(code, totals_by_code.get(code, 0.0))
        row["is_subrow"] = True
        expense_rows.append(row)
        if code in {"581", "585"}:
            _append_subrows(expense_rows, code)

    info_codes = ["101", "201", "301", "307", "312"]
    info_rows = [_row_for_code(code, totals_by_code.get(code, 0.0)) for code in info_codes]

    z1_total = round(sum(totals_by_code.get(code, 0.0) for code in revenue_codes), 2)
    z2_total = round(
        sum(totals_by_code.get(code, 0.0) for code in purchase_codes)
        + sum(totals_by_code.get(code, 0.0) for code in expense_codes),
        2,
    )

    z_rows = [
        {"description": "ΠΙΝΑΚΑΣ Ζ1 - Σύνολο Εσόδων", "is_group": True},
        _row_for_code("560", z1_total),
        {"description": "ΠΙΝΑΚΑΣ Ζ2 - Σύνολο Εξόδων", "is_group": True},
        _row_for_code("580", z2_total),
        {"description": "ΠΙΝΑΚΑΣ Ζ3 - Λοιπά Πληροφοριακά", "is_group": True},
        _row_for_code("595", totals_by_code.get("595", 0.0)),
        _row_for_code("596", totals_by_code.get("596", 0.0)),
        _row_for_code("598", totals_by_code.get("598", 0.0)),
        _row_for_code("880", totals_by_code.get("880", 0.0)),
    ]

    _append_subrows(z_rows, "880")

    for code in ("881", "882", "883"):
        row = _row_for_code(code, totals_by_code.get(code, 0.0))
        row["is_subrow"] = True
        # 881/882/883 are siblings of 880, NOT children — no parent_code
        z_rows.append(row)
        _append_subrows(z_rows, code)

    return {
        "revenue": revenue_rows,
        "expenses": expense_rows,
        "info": info_rows,
        "tableZ": z_rows,
    }


def fetch_e3_report(mark: str, date_from: str, date_to: str, aade_user: str, aade_key: str, debug: bool = False) -> dict:
    entries = fetch_e3_entries(mark, date_from, date_to, aade_user, aade_key, debug=debug)
    report = build_e3_report(entries)
    report["entries_count"] = len(entries)
    return report
