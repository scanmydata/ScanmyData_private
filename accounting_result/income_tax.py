# -*- coding: utf-8 -*-
"""
accounting_result/income_tax.py

Εκτίμηση φόρου εισοδήματος πάνω στο Φορολογητέο Αποτέλεσμα του Λογιστικού
Αποτελέσματος — Python μεταφορά του «Profit Planner» (LogApot) του HyperLog,
όπως τεκμηριώθηκε στο decode/forms/PROFIT_PLANNER_CALC.md και υλοποιήθηκε στο
decode/lib/profitplanner.js:

    φορολογητέο -> φόρος (ανά τύπο) -> προκαταβολή τρέχοντος έτους
                -> εκκαθάριση (− προκαταβολή προηγούμενου έτους)

Η «ανάκτηση περσινού» (§2 της προδιαγραφής: ο πίνακας LOGAPOT, τελευταίο
σενάριο του προηγούμενου ETOSXR) αντιστοιχεί εδώ στο ιστορικό υπολογισμών
της εταιρίας (history_store): παίρνουμε τον πιο πρόσφατο υπολογισμό του
προηγούμενου έτους και από αυτόν την προκαταβολή που είχε βεβαιωθεί/εκτιμηθεί.

Οι συντελεστές είναι ΝΟΜΟΘΕΤΙΚΟΙ και ενημερώνονται ανά φορολογικό έτος
(άρθρα 15/29, 58, 69/71 ν.4172/2013 — κλίμακα 2026+ από ν.5246/2025).
Οι μειώσεις ηλικίας/τέκνων/αγρότη της κλίμακας δεν εφαρμόζονται (εκτίμηση).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# Προκαταβολή φόρου (άρθρο 69/71): φυσικά 55%, νομικά 80%, τράπεζες 100%.
ADVANCE_RATE = {"natural": 0.55, "legal": 0.80, "bank": 1.00}
# Φόρος νομικών προσώπων και νομικών οντοτήτων (άρθρο 58).
LEGAL_TAX_RATE = 0.22

# Κλίμακα φυσικών προσώπων (άρθρα 15/29), ανά πρώτο φορολογικό έτος ισχύος.
_INF = float("inf")
NATURAL_SCALES: List[Tuple[int, List[Tuple[float, float]]]] = [
    (2020, [(10000, 0.09), (20000, 0.22), (30000, 0.28), (40000, 0.36), (_INF, 0.44)]),
    # ν.5246/2025: εισοδήματα από 1.1.2026.
    (2026, [(10000, 0.09), (20000, 0.20), (30000, 0.26), (40000, 0.34), (60000, 0.39), (_INF, 0.44)]),
]


def _num(v: Any) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v or "").strip().replace(" ", "").replace(",", "."))
    except ValueError:
        return 0.0


def _round(v: float) -> float:
    return round(_num(v) + 1e-9, 2) if _num(v) >= 0 else round(_num(v) - 1e-9, 2)


def natural_scale_for(year: int) -> List[Tuple[float, float]]:
    """Η κλίμακα που ισχύει για εισοδήματα του `year`."""
    scale = NATURAL_SCALES[0][1]
    for first_year, s in NATURAL_SCALES:
        if int(year or 0) >= first_year:
            scale = s
    return scale


def company_type_from_legal_kind(legal_kind: Optional[str]) -> str:
    """"sole_proprietor" -> natural, "legal_entity" -> legal (άγνωστο -> natural)."""
    return "legal" if legal_kind == "legal_entity" else "natural"


def income_tax(taxable: float, company_type: str = "natural", year: int = 0) -> float:
    t = _num(taxable)
    if t <= 0:
        return 0.0
    if company_type in ("legal", "bank"):
        return _round(t * LEGAL_TAX_RATE)
    tax, prev = 0.0, 0.0
    for up_to, rate in natural_scale_for(year):
        slice_ = min(t, up_to) - prev
        if slice_ <= 0:
            break
        tax += slice_ * rate
        prev = up_to
        if t <= up_to:
            break
    return _round(tax)


def advance_payment(tax: float, company_type: str = "natural", reduction_pct: float = 0.0,
                    new_business_half: bool = False) -> float:
    rate = ADVANCE_RATE.get(company_type, ADVANCE_RATE["natural"])
    adv = _num(tax) * rate * (1 - _num(reduction_pct))
    if new_business_half and company_type == "natural":
        adv *= 0.5
    return _round(max(0.0, adv))


def clearance(tax: float, advance: float, prev_advance: float = 0.0, withheld: float = 0.0,
              prepaid: float = 0.0, foreign_credit: float = 0.0) -> Dict[str, float]:
    balance = _num(tax) + _num(advance) - _num(prev_advance) - _num(withheld) - _num(prepaid) - _num(foreign_credit)
    return {
        "balance": _round(balance),
        "to_pay": _round(balance) if balance > 0 else 0.0,
        "to_refund": _round(-balance) if balance < 0 else 0.0,
    }


def compute(taxable: float, company_type: str = "natural", year: int = 0,
            prev_advance: float = 0.0, **kw: Any) -> Dict[str, Any]:
    tax = income_tax(taxable, company_type, year)
    advance = advance_payment(tax, company_type, kw.get("reduction_pct", 0.0), kw.get("new_business_half", False))
    cl = clearance(tax, advance, prev_advance, kw.get("withheld", 0.0), kw.get("prepaid", 0.0), kw.get("foreign_credit", 0.0))
    return {
        "company_type": company_type,
        "year": int(year or 0),
        "taxable": _round(taxable),
        "tax": tax,
        "tax_rate_label": ("22%" if company_type in ("legal", "bank") else "κλίμακα φυσικών προσώπων"),
        "advance_rate": ADVANCE_RATE.get(company_type, ADVANCE_RATE["natural"]),
        "advance": advance,
        "prev_advance": _round(prev_advance),
        **cl,
    }


def _is_full_year(date_from: Any, date_to: Any, parse_date: Callable[[Any], Any]) -> bool:
    d1, d2 = parse_date(date_from), parse_date(date_to)
    return bool(d1 and d2 and d1.month == 1 and d1.day == 1 and d2.month == 12 and d2.day == 31 and d1.year == d2.year)


def fetch_prev_year_advance(entries: Iterable[Dict[str, Any]], year: int, company_type: str,
                            parse_date: Callable[[Any], Any]) -> Optional[Dict[str, Any]]:
    """«Ανάκτηση περσινού» (LOGAPOT): από τα αποθηκευμένα αποτελέσματα της
    εταιρίας (νεότερο πρώτο) βρίσκει τον πιο πρόσφατο υπολογισμό του έτους
    `year - 1` — προτιμώντας όσους καλύπτουν όλο το έτος (01/01–31/12), όπως
    και η πραγματική δήλωση — και επιστρέφει την προκαταβολή του:
      * αν εκείνος ο υπολογισμός είχε ήδη φόρο εισοδήματος -> την προκαταβολή
        του αυτούσια (source="history"),
      * αλλιώς την υπολογίζει από το φορολογητέο του (source="history_derived").
    None όταν δεν υπάρχει υπολογισμός του προηγούμενου έτους."""
    prev_year = int(year) - 1
    candidates = []
    for e in entries or []:
        d_to = parse_date(e.get("date_to"))
        if d_to and d_to.year == prev_year:
            candidates.append(e)
    if not candidates:
        return None
    full = [e for e in candidates if _is_full_year(e.get("date_from"), e.get("date_to"), parse_date)]
    chosen = (full or candidates)[0]  # entries are newest-first
    report = chosen.get("report") or {}
    it = report.get("income_tax") if isinstance(report, dict) else None
    if isinstance(it, dict) and it.get("advance") is not None:
        amount, source = _num(it.get("advance")), "history"
    else:
        taxable = chosen.get("taxable_result")
        if taxable is None and isinstance(report, dict):
            taxable = report.get("taxable_result")
        amount = advance_payment(income_tax(_num(taxable), company_type, prev_year), company_type)
        source = "history_derived"
    return {
        "amount": _round(amount),
        "source": source,
        "year": prev_year,
        "entry_id": chosen.get("id"),
        "date_from": chosen.get("date_from"),
        "date_to": chosen.get("date_to"),
        "computed_at": chosen.get("timestamp"),
        "partial_period": not full,
    }


def build_income_tax_block(taxable: float, legal_kind: Optional[str], year: int,
                           history_entries: Iterable[Dict[str, Any]],
                           parse_date: Callable[[Any], Any],
                           prev_advance_override: Any = None) -> Dict[str, Any]:
    """Ό,τι μπαίνει ως report["income_tax"]: υπολογισμός + από πού ήρθε η
    προκαταβολή του προηγούμενου έτους (χειροκίνητα / ιστορικό / δεν βρέθηκε)."""
    company_type = company_type_from_legal_kind(legal_kind)
    prev_info: Optional[Dict[str, Any]] = None
    if prev_advance_override not in (None, ""):
        prev_amount = _num(prev_advance_override)
        prev_source = "manual"
    else:
        prev_info = fetch_prev_year_advance(history_entries, year, company_type, parse_date)
        prev_amount = prev_info["amount"] if prev_info else 0.0
        prev_source = prev_info["source"] if prev_info else "not_found"
    block = compute(taxable, company_type, year, prev_amount)
    block["company_type_assumed"] = legal_kind not in ("sole_proprietor", "legal_entity")
    block["prev_advance_source"] = prev_source
    block["prev_advance_info"] = prev_info
    return block
