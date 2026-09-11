# -*- coding: utf-8 -*-
"""
accounting_result/engine.py

Computes a ΕΓΛΣ-style "Λογιστικό Αποτέλεσμα" (P&L) report for one company/period,
derived from myDATA characterizations already present in the app.

Both sides of the report (Αγορές/Δαπάνες 20-28/60-67 AND Πωλήσεις 70-82) are
sourced from the SAME live AADE RequestE3Info pull — the official myDATA E3
income/expense classification the E3-check page already uses — rather than the
app's local "epsilon" per-invoice characterization cache. Two reasons:
  1. There is no local storage of the company's own issued invoices at all, so
     sales could never come from there.
  2. Local expense characterization coverage is typically partial (accountants
     mostly tag goods-for-resale purchases, not every rent/utility/payroll
     invoice), which made the ΔΑΠΑΝΕΣ columns read near-zero even for companies
     with real expenses — AADE's own official classification is complete because
     it's what the taxpayer's E3 filing is built from.
  The local epsilon-cache purchase pass is still used, but only as the source of
  ΦΠΑ ΕΙΣΡΟΩΝ (input VAT isn't present in RequestE3Info's classification amounts,
  which are net-of-VAT) and to surface "unresolved" local-categorization gaps.

Ε3-code -> ΓΛΣ crosswalk (see docs/E3_MAPPING_REFERENCE.md and the official Ε3
field map in e3/e3_field_map.py, cross-checked against the ΕΓΛΣ Ομάδα 7 chart of
accounts):
  - Sales: myDATA classifies income entries under the "Σύνολο" codes 561-570
    (confirmed against the E3-check page's own build_e3_report(), which has
    always read revenue off this exact range — the per-activity codes
    161/261/361/461 documented in e3_field_map.py are Ε3-*form* aggregation
    targets, not tags that ever appear on a live classified entry). 561
    ("Πωλήσεις αγαθών και υπηρεσιών") mixes goods and services together, so
    it's split into 70 (Πωλήσεις Εμπορευμάτων) vs 73 (Πωλήσεις Υπηρεσιών) using
    the entry's classification_category text ("ΥΠΗΡΕΣ..."), falling back to
    invoice_type ("2.x" = AADE services-invoice type) — myDATA has no other
    per-entry goods-vs-services signal, so this is a best-effort split, not an
    exact one. 563/564/565 (credit interest/FX gains/investment income)->76
    (Έσοδα Κεφαλαίων), 568 (κέρδη επιμέτρησης εύλογη αξία)->77, 570 (ασυνήθη
    έσοδα)->81. 562/566/567/569 have no confident GLS home and are left out.
  - Stock purchases: 102 (αγορές εμπορευμάτων, εμπορική)->20, 202/302 (αγορές
    πρώτων υλών, παραγωγική/αγροτική)->24, 313 (αγορές ζώων-φυτών)->27 (Βιολ.
    Περ. Στοιχ.).
  - Capex: 802/822/842/862/882 (αγορές παγίων, table Ζ3) -> ΑΓΟΡΕΣ ΠΑΓΙΩΝ.
  - Production expenses: 105/212/317 (λοιπά έξοδα εμπορικής/παραγωγικής/
    αγροτικής δραστηριότητας) -> ΔΑΠΑΝΕΣ ΠΑΡΑΓΩΓΗΣ.
  - Operating expenses (table Ζ2, "Σύνολο" codes 581-589): 581 (παροχές σε
    εργαζόμενους)->60, 586 (χρεωστικοί τόκοι)->65, 587 (αποσβέσεις)->66,
    582/583/584/588/589 (ζημιές επιμέτρησης/συναλλαγματικές διαφορές/ζημιές
    διάθεσης παγίων/ασυνήθη έξοδα/προβλέψεις)->67, 585 (διάφορα λειτουργικά,
    sub-coded OPEX_SUB) split by sub-code into 61/62/64 (see
    _OPEX_585_SUBCODE_MAP below).

  This is an approximation — myDATA's E3 classification granularity doesn't
  perfectly mirror the ΓΛΣ chart, and even accountants make judgment calls on
  borderline sub-codes — but every bucket is grounded in an actual documented
  Ε3 code rather than guessed.

  - Depreciation (66): NOT the current period's own 587 entries (depreciation
    is rarely re-classified invoice-by-invoice mid-year) but the PRIOR full
    year's 587 entries, prorated by months in the requested period. If the
    prior year has more than one distinct 587 entry (by invoice mark), the
    caller must resolve which to use (sum all / pick specific marks) before
    calling build_report — see depreciation_entries_from().
  - Unclassified ("ΜΗ ΧΑΡΑΚΤΗΡΙΣΜΕΝΟ...") myDATA marks are excluded from the
    totals above and their net value is subtracted from ΦΟΡΟΛΟΓΗΤΕΟ ΑΠΟΤΕΛΕΣΜΑ,
    mirroring the detection already used by the E3-check page (api_e3_fetch).

v1 known gaps (left blank/omitted rather than guessed):
  - ΠΩΛΗΣΕΙΣ ΠΑΓΙΩΝ: no reliable myDATA signal, always 0.
  - ΦΠΑ ΕΚΡΟΩΝ/ΕΙΣΡΟΩΝ: sourced from a live AADE RequestVatInfo pull
    (accounting_result/fetch_vat.py), summing Vat301-306 (εκροές) and
    Vat331-336 (εισροές) across every non-cancelled invoice on file for the
    period — independent of E3 characterization, so unclassified invoices are
    included too (RequestE3Info's classification amount is net-of-VAT and only
    covers classified marks, which is why the local epsilon-characterization
    pass was never a correct source for this). ΧΡΕΩΣΤΙΚΟ ΥΠΟΛΟΙΠΟ ΠΕΡΙΟΔΟΥ =
    εκροών − εισροών. ΠΙΣΤ.ΥΠΟΛ.ΠΡΟΗΓ.ΠΕΡ. and ΠΛΗΡΩΜΕΣ ΣΤΟ ΔΗΜΟΣΙΟ still have
    no myDATA source and stay None ("—" in the UI).
  - ΖΗΜΙΕΣ ΠΡΟΗΓΟΥΜΕΝΟΥ ΕΤΟΥΣ: not derivable from myDATA, always 0.
  - Run-rate "υποχρέωση απογραφής" warning box: no confirmed legal threshold
    found in the codebase, not implemented.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from epsilon_bridges.epsilon_bridge_multiclient_strict import (
    _canon_category,
    _infer_vat_rate_for_line,
    _is_receipt,
    _get_account_for,
    _merge_custom_accounts,
)
from epsilon_bridges.epsilon_bridge_g_category import (
    _get_account_for_g,
    _merge_custom_accounts_g,
)
from g_category_helpers import is_g_category_active
from e3.checks.fetch_e3 import fetch_e3_entries
from accounting_result.fetch_vat import fetch_vat_totals


STOCK_CODES = ["20", "21", "23", "24", "25", "26", "27", "28"]
STOCK_LABELS = {
    "20": "ΕΜΠΟΡΕΥΜΑΤΑ",
    "21": "ΠΡΟΙΟΝΤΑ",
    "23": "ΠΑΡ. ΣΕ ΕΞΕΛ.",
    "24": "ΠΡΩΤΕΣ ΥΛΕΣ",
    "25": "ΑΝΑΛ. ΠΑΡ.",
    "26": "ΑΝΤΑΛ. ΠΑΓΙΩΝ",
    "27": "ΒΙΟΛ.ΠΕΡ.ΣΤΟΙΧ.",
    "28": "ΕΙΔΗ ΣΥΣΚΕΥΑΣΙΑΣ",
}
GOODS_STOCK_CODE = "20"

EXPENSE_CODES = ["60", "61", "62", "63", "64", "65", "66", "67"]
EXPENSE_LABELS = {
    "60": "ΑΜΟΙΒΗ ΠΡΟΣΩΠ.",
    "61": "ΑΜΟΙΒΗ ΤΡΙΤΩΝ",
    "62": "ΠΑΡΟΧ.ΤΡΙΤΩΝ",
    "63": "ΦΟΡΟΙ - ΤΕΛΗ",
    "64": "ΔΙΑΦΟΡΑ ΕΞΟΔΑ",
    "65": "ΤΟΚΟΙ",
    "66": "ΑΠΟΣΒΕΣΕΙΣ",
    "67": "ΖΗΜ. ΕΠΙΜ.ΠΕΡ.ΣΤΟΙΧ.-ΑΣΥΝΗΘ.ΕΞ.ΠΡΟΣΤ.",
}
EXTRA_EXPENSE_CODES = ["81", "82"]
EXTRA_EXPENSE_LABELS = {"81": "ΕΚΤΑΚΤΑ ΕΞΟΔΑ", "82": "ΕΞΟΔΑ ΠΡΟΗΓ. ΧΡΗΣΕΩΝ"}

SALES_CODES = ["70", "71", "72", "73", "74", "75", "76", "77"]
SALES_LABELS = {
    "70": "ΠΩΛΗΣΕΙΣ ΕΜΠΟΡΕΥΜΑΤΩΝ",
    "71": "ΠΩΛΗΣΕΙΣ ΠΡΟΪΟΝΤΩΝ",
    "72": "ΠΩΛΗΣΕΙΣ ΑΠΟΘΕΜΑΤΩΝ",
    "73": "ΠΩΛΗΣΕΙΣ ΥΠΗΡΕΣΙΩΝ",
    "74": "ΕΠΙΧΟΡΗΓ. & ΔΙΑΦ. ΕΣΟΔΑ ΠΩΛ.",
    "75": "ΕΣΟΔΑ ΠΑΡ. ΑΣΧΟΛΙΩΝ",
    "76": "ΕΣΟΔΑ ΚΕΦΑΛΑΙΩΝ",
    "77": "ΚΕΡΔΗ ΑΠΟ ΕΠΙΜ. ΣΤΗΝ ΕΥΛ. ΑΞΙΑ",
}
EXTRA_SALES_CODES = ["78", "81", "82"]
EXTRA_SALES_LABELS = {
    "78": "ΙΔΙΟΠΑΡΑΓΩΓΗ",
    "81": "ΕΚΤΑΚΤΑ ΕΣΟΔΑ",
    "82": "ΕΣΟΔΑ ΠΡΟΗΓ. ΧΡΗΣΕΩΝ",
}

# ── Ε3 -> ΓΛΣ crosswalk (see module docstring for sourcing) ──────────────────
# Code 561 ("Πωλήσεις αγαθών και υπηρεσιών — Σύνολο") is what myDATA actually
# tags classified income entries with — NOT the per-activity codes 161/261/
# 361/461, which only exist as Ε3-form aggregation targets (confirmed against
# the already-working E3-check page's own build_e3_report(), which has always
# read revenue off 561-570). 561 mixes goods AND services together, so it's
# split into 70/73 using the entry's classification_category text (falling
# back to invoice_type — "2.x" = AADE services-invoice type) since myDATA has
# no other per-entry goods-vs-services signal.
_SALES_TOTAL_CODE = "561"
_SERVICE_TYPE_RE = re.compile(r"^\s*2(\.\d+)?\b")
_INCOME_DIRECT_TO_GLS = {"563": "76", "564": "76", "565": "76", "568": "77", "570": "81"}
_STOCK_PURCHASE_TO_GLS = {"102": "20", "202": "24", "302": "24", "313": "27"}
_PRODUCTION_EXPENSE_CODES = {"105", "212", "317"}
_CAPEX_CODES = {"802", "822", "842", "862", "882"}
_OPEX_DIRECT_TO_GLS = {"581": "60", "586": "65", "587": "66"}
_OPEX_IMPAIRMENT_CODES = {"582", "583", "584", "588", "589"}  # -> 67
_OPEX_585_SUBCODE_TO_GLS = {
    "001": "61", "002": "61", "003": "61",           # management/affiliated-co fees
    "004": "64", "005": "64", "006": "64",           # events/hospitality/travel
    "007": "61", "008": "61", "009": "61", "010": "61",  # self-employed contrib. + agent fees + other fees
    "011": "62", "012": "62", "013": "62", "014": "62",  # energy/water/telecom/rent
    "015": "61",                                      # advertising
    "016": "64", "017": "64",                          # misc / 7% ΕΛΚΕ
}
DEPRECIATION_E3_CODE = "587"

# Ε3 "closing stock" info codes (table Δ2/Δ3/Δ4) — only present in a company's
# official myDATA E3 classification when the accountant has actually declared
# an απογραφή for that year. Used to decide whether to ask about the current
# year's closing inventory at all.
INVENTORY_CLOSING_CODES = {"104", "204", "209", "304", "309", "315"}


def _digits(s: Any) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def norm_afm(v: Any) -> str:
    d = _digits(v)
    if not d:
        return ""
    if len(d) >= 9:
        d = d[-9:]
    return d.zfill(9)


def parse_date(s: Any) -> Optional[date]:
    s = str(s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except Exception:
            pass
    return None


def to_ddmmyyyy(s: Any) -> str:
    """Normalizes a date string (ISO or dd/mm/yyyy) to dd/mm/yyyy — the format
    AADE's RequestE3Info expects. Passing the wrong format silently yields an
    empty/garbage response from AADE, not an error."""
    d = parse_date(s)
    return d.strftime("%d/%m/%Y") if d else str(s or "").strip()


def compute_vat_declaration_period(vat_period_type: str, today: Optional[date] = None) -> Tuple[str, str]:
    """The ΦΠΑ block reports ONE official declaration period — not a
    cumulative sum across however many months the report's own date_from/
    date_to happens to span (that produced meaningless multi-month totals
    like "Χρεωστικό Υπόλοιπο Περιόδου" in the thousands for a report
    covering half a year). The period is derived from TODAY, independent of
    the report's own range:
      - Monthly filers: the most recently CLOSED calendar month — the
        current month is still in progress, so nothing's been declared for
        it yet.
      - Quarterly filers: the CURRENT calendar quarter (in progress) — the
        accountant wants what's accruing this quarter, not the already-filed
        previous one. AADE simply won't have data past today, so an
        in-progress quarter's end date is safe to use as-is.
      - Unknown/undetected filing frequency: falls back to monthly — the
        stricter/more common cadence, and still far more meaningful than the
        old whole-report-range behavior.
    Returns (date_from, date_to) as ISO "YYYY-MM-DD" strings."""
    today = today or date.today()
    if vat_period_type == "quarterly":
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        period_from = date(today.year, q_start_month, 1)
        q_end_month = q_start_month + 2
        q_end_day = calendar.monthrange(today.year, q_end_month)[1]
        period_to = date(today.year, q_end_month, q_end_day)
    else:
        first_of_this_month = today.replace(day=1)
        last_of_prev_month = first_of_this_month - timedelta(days=1)
        period_from = last_of_prev_month.replace(day=1)
        period_to = last_of_prev_month
    return period_from.isoformat(), period_to.isoformat()


def _fnum(v: Any) -> float:
    try:
        return float(str(v).replace(",", "."))
    except Exception:
        return 0.0


def _group_of(code: str) -> str:
    m = re.match(r"(\d{2})", str(code or "").strip())
    return m.group(1) if m else ""


def _months_in_period(date_from: date, date_to: date) -> int:
    months = (date_to.year - date_from.year) * 12 + (date_to.month - date_from.month) + 1
    return max(1, min(12, months))


class LineItem:
    __slots__ = ("mark", "gls_code", "group", "net", "vat_amount", "category", "resolved")

    def __init__(self, mark, gls_code, group, net, vat_amount, category, resolved):
        self.mark = mark
        self.gls_code = gls_code
        self.group = group
        self.net = net
        self.vat_amount = vat_amount
        self.category = category
        self.resolved = resolved


def _purchase_resolver(settings: Dict[str, Any], credential: Optional[Dict[str, Any]]):
    """Returns resolve_fn(canon_category, vat_rate, is_receipt) -> gls_code, used
    only for the local VAT-εισροών pass now (see module docstring)."""
    if is_g_category_active(credential):
        merged = _merge_custom_accounts_g(settings, credential)
        return lambda canon, rate, is_receipt: _get_account_for_g(merged, canon, rate, is_receipt)
    merged = _merge_custom_accounts(settings, credential)
    return lambda canon, rate, is_receipt: _get_account_for(merged, canon, rate)


def load_purchase_lines(
    vat: str,
    date_from: str,
    date_to: str,
    epsilon_records: List[dict],
    raw_invoices: List[dict],
    settings: Dict[str, Any],
    credential: Optional[Dict[str, Any]],
) -> Tuple[List[LineItem], List[LineItem]]:
    """Local epsilon-cache purchase/expense lines for the period. Only used as
    the source of ΦΠΑ ΕΙΣΡΟΩΝ and an "unresolved local categorization" note —
    the report's ΔΑΠΑΝΕΣ/ΑΓΟΡΕΣ totals themselves come from official myDATA E3
    classification (see build_expense_groups), not from this local pass.

    Returns (resolved, unresolved) — unresolved lines are classified (have a
    category) but no GLS account is configured for that (category, VAT rate)
    combination in the client's settings.
    """
    raw_by_mark: Dict[str, dict] = {}
    for doc in raw_invoices or []:
        mk = str(doc.get("mark") or doc.get("MARK") or "").strip()
        if mk:
            raw_by_mark[mk] = doc

    resolve_fn = _purchase_resolver(settings, credential)

    d_from = parse_date(date_from)
    d_to = parse_date(date_to)

    resolved: List[LineItem] = []
    unresolved: List[LineItem] = []

    for rec in epsilon_records or []:
        mark = str(rec.get("mark") or rec.get("MARK") or "").strip()
        raw = raw_by_mark.get(mark) or {}
        issue_date = parse_date(raw.get("issueDate") or rec.get("issueDate"))
        if issue_date is None:
            continue
        if d_from and issue_date < d_from:
            continue
        if d_to and issue_date > d_to:
            continue

        is_receipt = _is_receipt(raw or rec)
        for ln in rec.get("lines") or []:
            category_raw = str(ln.get("category") or "").strip()
            if not category_raw:
                continue
            net = _fnum(ln.get("amount", 0))
            vat_amt = _fnum(ln.get("vat", 0))

            canon = _canon_category(category_raw)
            vr, _src = _infer_vat_rate_for_line(ln, raw or rec)
            gls_code = ""
            if vr is not None:
                gls_code = resolve_fn(canon, vr, is_receipt) or ""

            group = _group_of(gls_code) if gls_code else ""
            item = LineItem(mark, gls_code, group, net, vat_amt, canon, bool(gls_code))
            (resolved if gls_code else unresolved).append(item)

    return resolved, unresolved


def fetch_and_split_e3_entries(
    date_from: str, date_to: str, aade_user: str, aade_key: str
) -> Tuple[List[dict], float, List[Dict[str, Any]]]:
    """Live AADE RequestE3Info pull, split into classified entries vs unclassified marks.

    Mirrors the exact detection app.py's api_e3_fetch already uses, so unclassified
    totals here match what the E3-check page shows for the same period.
    """
    df = to_ddmmyyyy(date_from)
    dt = to_ddmmyyyy(date_to)
    raw_entries = fetch_e3_entries("0", df, dt, aade_user, aade_key, debug=False)

    mark_totals: Dict[str, float] = {}
    mark_is_unclassified: Dict[str, bool] = {}
    for row in raw_entries or []:
        mk = str(row.get("invoice_mark") or "").strip()
        if not mk:
            continue
        amount = _fnum(row.get("amount"))
        mark_totals[mk] = mark_totals.get(mk, 0.0) + amount
        category = str(row.get("classification_category") or "").strip().upper()
        is_unclassified = category.startswith("ΜΗ") and ("ΧΑΡΑΚΤΗΡΙΣΜ" in category)
        if mk not in mark_is_unclassified:
            mark_is_unclassified[mk] = bool(is_unclassified)
        elif is_unclassified:
            mark_is_unclassified[mk] = True

    unclassified_total = 0.0
    unclassified_marks: List[Dict[str, Any]] = []
    classified_marks = set()
    for mk, is_unclassified in mark_is_unclassified.items():
        if is_unclassified:
            amount = round(mark_totals.get(mk, 0.0), 2)
            unclassified_total += amount
            unclassified_marks.append({"mark": mk, "net": amount})
        else:
            classified_marks.add(mk)

    classified_entries = [
        row for row in (raw_entries or [])
        if str(row.get("invoice_mark") or "").strip() in classified_marks
    ]
    return classified_entries, round(unclassified_total, 2), unclassified_marks


def build_sales_groups(classified_entries: List[dict]) -> Dict[str, float]:
    """Buckets classified official-E3 income entries into GLS sales/other-income
    accounts. See the crosswalk comment above _SALES_TOTAL_CODE for sourcing."""
    totals: Dict[str, float] = {}

    def add(gls_code: str, amount: float) -> None:
        totals[gls_code] = round(totals.get(gls_code, 0.0) + amount, 2)

    for row in classified_entries:
        code = str(row.get("code") or "").strip()
        amount = _fnum(row.get("amount"))
        if not code or amount == 0:
            continue

        if code == _SALES_TOTAL_CODE:
            category = str(row.get("classification_category") or "").upper()
            invoice_type = str(row.get("invoice_type") or "")
            is_service = "ΥΠΗΡΕΣ" in category or bool(_SERVICE_TYPE_RE.match(invoice_type))
            add("73" if is_service else "70", amount)
        elif code in _INCOME_DIRECT_TO_GLS:
            add(_INCOME_DIRECT_TO_GLS[code], amount)

    return totals


def build_expense_groups(classified_entries: List[dict]) -> Dict[str, Any]:
    """Buckets classified official-E3 expense-side entries into GLS stock-purchase
    groups (20/24/27), operating-expense groups (60-67), ΔΑΠΑΝΕΣ ΠΑΡΑΓΩΓΗΣ and
    ΑΓΟΡΕΣ ΠΑΓΙΩΝ. See module docstring for the full crosswalk."""
    groups: Dict[str, float] = {}
    production_expenses = 0.0
    capex = 0.0

    def add(gls_code: str, amount: float) -> None:
        groups[gls_code] = round(groups.get(gls_code, 0.0) + amount, 2)

    for row in classified_entries:
        code = str(row.get("code") or "").strip()
        sub = str(row.get("sub_code") or "").strip()
        amount = _fnum(row.get("amount"))
        if not code or amount == 0:
            continue

        if code in _STOCK_PURCHASE_TO_GLS:
            add(_STOCK_PURCHASE_TO_GLS[code], amount)
        elif code in _PRODUCTION_EXPENSE_CODES:
            production_expenses = round(production_expenses + amount, 2)
        elif code in _CAPEX_CODES:
            capex = round(capex + amount, 2)
        elif code in _OPEX_DIRECT_TO_GLS:
            add(_OPEX_DIRECT_TO_GLS[code], amount)
        elif code in _OPEX_IMPAIRMENT_CODES:
            add("67", amount)
        elif code == "585":
            add(_OPEX_585_SUBCODE_TO_GLS.get(sub, "64"), amount)

    return {"groups": groups, "production_expenses": production_expenses, "capex": capex}


def company_tracks_inventory(classified_entries: List[dict]) -> bool:
    for row in classified_entries:
        code = str(row.get("code") or "").strip()
        if code in INVENTORY_CLOSING_CODES and abs(_fnum(row.get("amount"))) > 0.005:
            return True
    return False


def depreciation_entries_from(classified_entries: List[dict]) -> List[Dict[str, Any]]:
    """Distinct (by invoice mark) code-587 "Αποσβέσεις — Σύνολο" entries. More than
    one means the caller must ask the accountant whether to sum all of them or use
    only specific ones (see compute_depreciation_amount)."""
    by_mark: Dict[str, float] = {}
    for row in classified_entries:
        if str(row.get("code") or "").strip() != DEPRECIATION_E3_CODE:
            continue
        mark = str(row.get("invoice_mark") or "").strip()
        by_mark[mark] = by_mark.get(mark, 0.0) + _fnum(row.get("amount"))
    return [
        {"mark": mk, "amount": round(amt, 2)}
        for mk, amt in by_mark.items()
        if abs(amt) > 0.005
    ]


def fetch_prior_year_classified_entries(year: int, aade_user: str, aade_key: str) -> List[dict]:
    """Live AADE pull for the PRIOR full year's classified E3 entries — the
    source for both the closing-inventory-applicability check and the
    depreciation lookup, fetched once and shared by both."""
    prior_from = f"01/01/{year - 1}"
    prior_to = f"31/12/{year - 1}"
    classified_entries, _unclassified_total, _unclassified_marks = fetch_and_split_e3_entries(
        prior_from, prior_to, aade_user, aade_key,
    )
    return classified_entries


def compute_depreciation_amount(
    dep_entries: List[Dict[str, Any]],
    date_from: str,
    date_to: str,
    selection: Optional[Dict[str, Any]] = None,
) -> float:
    """Prorates the selected prior-year depreciation entries by months in the
    requested period. `selection` is None/{"mode":"sum_all"} (sum every entry —
    the only sane default when there's 0 or 1 entries) or
    {"mode":"marks","marks":[...]} to sum only the chosen marks."""
    d_from = parse_date(date_from)
    d_to = parse_date(date_to)
    if not d_from or not d_to:
        return 0.0

    if selection and selection.get("mode") == "marks":
        chosen = set(str(m) for m in (selection.get("marks") or []))
        total = sum(e["amount"] for e in dep_entries if e["mark"] in chosen)
    else:
        total = sum(e["amount"] for e in dep_entries)

    months = _months_in_period(d_from, d_to)
    return round(total * (months / 12.0), 2)


def merge_excel_overrides(account_totals: Dict[str, float], group_totals: Dict[str, float]) -> Dict[str, float]:
    """Per-account-group Excel values REPLACE the myDATA-computed total for that group."""
    merged = dict(account_totals or {})
    for grp, amount in (group_totals or {}).items():
        merged[grp] = round(_fnum(amount), 2)
    return merged


def build_report(
    vat: str,
    date_from: str,
    date_to: str,
    credential: Dict[str, Any],
    settings: Dict[str, Any],
    epsilon_records: List[dict],
    raw_invoices: List[dict],
    aade_user: str,
    aade_key: str,
    opening_inventory: Dict[str, float],
    closing_inventory: Dict[str, float],
    depreciation_amount: float = 0.0,
    excel_group_totals: Optional[Dict[str, float]] = None,
    vat_applicable: bool = True,
    vat_period_type: str = "",
) -> Dict[str, Any]:
    opening_inventory = {k: _fnum(v) for k, v in (opening_inventory or {}).items()}
    closing_inventory = {k: _fnum(v) for k, v in (closing_inventory or {}).items()}

    # Local pass: kept only for the "unresolved local categorization"
    # transparency note now — ΦΠΑ itself comes from RequestVatInfo below,
    # which (unlike this local characterization-derived pass) covers every
    # submitted invoice, not just the ones an accountant has tagged.
    _purchase_resolved, purchase_unresolved = load_purchase_lines(
        vat, date_from, date_to, epsilon_records, raw_invoices, settings, credential,
    )

    # A company explicitly marked as NOT subject to VAT (accounting_result/
    # vat_profile_store.py — set manually or detected from the ΑΑΔΕ Μητρώο's
    # own "ypagwghfpa" flag) skips the RequestVatInfo call entirely and the
    # report's ΦΠΑ block is omitted, regardless of whatever VatInfo rows
    # myDATA happens to return for it (a non-VAT-subject entity can still
    # have stray classified rows that shouldn't drive a ΦΠΑ figure that
    # doesn't apply to it).
    if vat_applicable:
        vat_period_from, vat_period_to = compute_vat_declaration_period(vat_period_type)
        vat_outflow, vat_inflow = fetch_vat_totals(
            vat, to_ddmmyyyy(vat_period_from), to_ddmmyyyy(vat_period_to), aade_user, aade_key,
        )
        vat_period_balance = round(vat_outflow - vat_inflow, 2)
    else:
        vat_period_from = vat_period_to = None
        vat_outflow = vat_inflow = vat_period_balance = None

    classified_entries, unclassified_net, unclassified_marks = fetch_and_split_e3_entries(
        date_from, date_to, aade_user, aade_key,
    )
    sales_groups = build_sales_groups(classified_entries)
    expense_result = build_expense_groups(classified_entries)

    account_totals: Dict[str, float] = {}
    account_totals.update(expense_result["groups"])
    for grp, amt in sales_groups.items():
        account_totals[grp] = round(account_totals.get(grp, 0.0) + amt, 2)

    # Depreciation is resolved by the caller from the PRIOR year's 587 entries
    # (possibly disambiguated by the accountant) — it always overrides whatever
    # the CURRENT period's own 587 entries would have summed to.
    account_totals["66"] = round(_fnum(depreciation_amount), 2)

    if excel_group_totals:
        account_totals = merge_excel_overrides(account_totals, excel_group_totals)

    def g(code: str) -> float:
        return round(_fnum(account_totals.get(code, 0.0)), 2)

    # ---- stocks table ----
    stock_rows = []
    cogs_goods = 0.0
    cogs_products = 0.0
    for code in STOCK_CODES:
        opening = round(opening_inventory.get(code, 0.0), 2)
        purchases = g(code)
        closing = round(closing_inventory.get(code, 0.0), 2)
        cogs = round(opening + purchases - closing, 2)
        stock_rows.append({
            "code": code, "label": STOCK_LABELS[code],
            "opening": opening, "purchases": purchases, "closing": closing, "cogs": cogs,
        })
        if code == GOODS_STOCK_CODE:
            cogs_goods = cogs
        else:
            cogs_products += cogs
    cogs_products = round(cogs_products, 2)
    cogs_total = round(cogs_goods + cogs_products, 2)

    # ---- sales / expenses table ----
    sales_rows = [{"code": c, "label": SALES_LABELS[c], "amount": g(c)} for c in SALES_CODES]
    sales_ekm = round(sum(r["amount"] for r in sales_rows), 2)
    extra_sales_rows = [{"code": c, "label": EXTRA_SALES_LABELS[c], "amount": g(c)} for c in EXTRA_SALES_CODES]
    sales_total = round(sales_ekm + sum(r["amount"] for r in extra_sales_rows), 2)
    sales_paggion = 0.0  # v1: not derivable from myDATA, see module docstring

    expense_rows = [{"code": c, "label": EXPENSE_LABELS[c], "amount": g(c)} for c in EXPENSE_CODES]
    expenses_ekm = round(sum(r["amount"] for r in expense_rows), 2)
    extra_expense_rows = [{"code": c, "label": EXTRA_EXPENSE_LABELS[c], "amount": g(c)} for c in EXTRA_EXPENSE_CODES]
    expenses_total = round(expenses_ekm + sum(r["amount"] for r in extra_expense_rows), 2)
    production_expenses = expense_result["production_expenses"]
    fixed_asset_purchases = expense_result["capex"]

    # ---- gross margin ----
    gross_goods = round(g("70") - cogs_goods, 2)
    gross_products = round(g("71") - cogs_products, 2)
    gross_total = round(gross_goods + gross_products, 2)
    pct_gross_on_cost = round((gross_total / cogs_total) * 100, 2) if cogs_total else None
    pct_gross_on_sales = round((gross_total / (g("70") + g("71"))) * 100, 2) if (g("70") + g("71")) else None
    services_sales = g("73")
    pct_services = round(((services_sales - production_expenses) / services_sales) * 100, 2) if services_sales else None

    # ---- net result ----
    net_profit = round(sales_total - expenses_total - cogs_total + sales_paggion, 2)
    prior_year_losses = 0.0  # v1: not derivable from myDATA
    final_net_profit = round(net_profit - prior_year_losses, 2)
    taxable_result = round(final_net_profit - unclassified_net, 2)

    return {
        "vat": vat,
        "date_from": date_from,
        "date_to": date_to,
        "stock_rows": stock_rows,
        "cogs_goods": cogs_goods,
        "cogs_products": cogs_products,
        "cogs_total": cogs_total,
        "production_expenses": production_expenses,
        "fixed_asset_purchases": fixed_asset_purchases,
        "sales_rows": sales_rows,
        "extra_sales_rows": extra_sales_rows,
        "sales_ekm": sales_ekm,
        "sales_total": sales_total,
        "sales_paggion": sales_paggion,
        "expense_rows": expense_rows,
        "extra_expense_rows": extra_expense_rows,
        "expenses_ekm": expenses_ekm,
        "expenses_total": expenses_total,
        "gross_goods": gross_goods,
        "gross_products": gross_products,
        "gross_total": gross_total,
        "pct_gross_on_cost": pct_gross_on_cost,
        "pct_gross_on_sales": pct_gross_on_sales,
        "pct_services": pct_services,
        "net_profit": net_profit,
        "prior_year_losses": prior_year_losses,
        "final_net_profit": final_net_profit,
        "unclassified_net": unclassified_net,
        "unclassified_marks": unclassified_marks,
        "taxable_result": taxable_result,
        "depreciation": depreciation_amount,
        "vat_outflow": vat_outflow,
        "vat_inflow": vat_inflow,
        "vat_prior_credit": None,  # v1: not derivable, see module docstring
        "vat_state_payments": None,  # v1: not derivable, see module docstring
        "vat_period_balance": vat_period_balance,
        "vat_applicable": vat_applicable,
        "vat_period_from": vat_period_from,
        "vat_period_to": vat_period_to,
        "opening_inventory": opening_inventory,
        "closing_inventory": closing_inventory,
        "unresolved_purchase_total": round(sum(i.net for i in purchase_unresolved), 2),
        "unresolved_purchase_count": len(purchase_unresolved),
    }
