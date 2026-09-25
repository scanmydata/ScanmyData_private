"""
accounting_result/inventory_rules.py

Υποχρέωση απογραφής λήξης (ν.4308/2014 άρθ. 30, ΠΟΛ.1003/2014 §30.3.1,
ΠΟΛ.1019/2015) — implementation of the spec «Έλεγχος υποχρέωσης απογραφής
αποθεμάτων» (apografi_spec.md, 2026-09-25), rules R1–R10.

Pure logic, no I/O: the caller supplies the company facts (books category,
legal form, ΚΑΔ with titles, business start) and the period's myDATA
classified entries; everything here is re-evaluated per computation (R10).

The ΠΟΛ.1019 exemption is defined by activity DESCRIPTION, not by ΚΑΔ — the
ΚΑΔ match below is an indication only (spec §3), so a match on the main ΚΑΔ
without a per-category sales breakdown yields LIKELY_EXEMPT_POL1019, and
unclear cases yield REVIEW.
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date
from typing import Any, Dict, List, Optional

GOODS_THRESHOLD_EUR = 150000.0

OBLIGED = "OBLIGED"
EXEMPT_THRESHOLD = "EXEMPT_THRESHOLD"
EXEMPT_POL1019 = "EXEMPT_POL1019"
LIKELY_EXEMPT_POL1019 = "LIKELY_EXEMPT_POL1019"
FUEL_SPECIAL = "FUEL_SPECIAL"
REVIEW = "REVIEW"

# Legal forms that always keep double-entry books (R1).
_DOUBLE_ENTRY_FORMS = ("ΑΕ", "ΕΠΕ", "ΙΚΕ", "Α.Ε.", "Ε.Π.Ε.", "Ι.Κ.Ε.")


def normalize_title(s: str) -> str:
    """Upper case, no accents/diaeresis, collapsed spaces (spec §4.3)."""
    nf = unicodedata.normalize("NFD", str(s or ""))
    nf = "".join(c for c in nf if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", nf.upper()).strip()


def normalize_kad(code: str) -> str:
    """'4711' / '47110000' / '47.11.00.00' / '1110000' -> 'NN.NN.NN.NN'."""
    digits = re.sub(r"\D", "", str(code or ""))
    if not digits:
        return ""
    if len(digits) < 8:
        digits = digits.zfill(8) if len(digits) == 7 else digits.ljust(8, "0")
    digits = digits[:8]
    return ".".join(digits[i:i + 2] for i in range(0, 8, 2))


# (prefix or "*", title regex or None) — spec §3.2, ΚΑΔ 2025 column.
# Regexes are applied to normalize_title() output (no accents, upper case).
_RS_C24 = r"ΡΑΠΤΙΚ|ΚΛΩΣΤ|ΝΗΜ|ΚΟΥΜΠ|ΦΕΡΜΟΥΑΡ|ΒΕΛΟΝ|ΥΠΟΔΗΜΑΤΟΠ"
_RS_C19 = r"ΧΑΡΤ|ΓΡΑΦΙΚ|ΣΧΕΔ|ΦΑΚΕΛ|ΣΧΟΛ"
_RS_C23 = r"ΜΕΡΩΝ|ΕΞΑΡΤΗΜ|ΑΝΤΑΛΛΑΚΤ"
CATEGORIES: List[Dict[str, Any]] = [
    {"id": "C01", "label": "Λατομείο", "retail": False, "rules": [("08.11", None), ("23.70", None)]},
    {"id": "C02", "label": "Σφραγίδες, επιγραφές, σήματα", "retail": False, "rules": [("*", r"ΣΦΡΑΓΙΔ|ΕΠΙΓΡΑΦ|ΣΗΜΑΤ(ΩΝ|Α)\b")]},
    {"id": "C03", "label": "Τυπογραφείο", "retail": False, "rules": [("18.11", None), ("18.12", None), ("18.13", None)]},
    {"id": "C04", "label": "Φωτογραφείο", "retail": False, "rules": [("74.20", None), ("*", r"ΦΩΤΟΓΡΑΦ|ΦΙΛΜ")]},
    {"id": "C05", "label": "Εκδόσεις εφημερίδων/περιοδικών", "retail": False, "rules": [("58.12", None), ("58.13", None)]},
    {"id": "C06", "label": "Βιβλιοδετείο", "retail": False, "rules": [("18.14", None)]},
    {"id": "C07", "label": "Φωτοτυπίες/πολυγραφήσεις", "retail": False, "rules": [("82.10", r"ΦΩΤΟΑΝΤΙΓΡ|ΦΩΤΟΤΥΠ|ΠΟΛΥΓΡΑΦ")]},
    {"id": "C08", "label": "Αρτοποιείο / ζαχαροπλαστικής", "retail": True, "rules": [("10.71", None), ("47.24", None)]},
    {"id": "C09", "label": "Γαλακτοζαχαροπλαστείο, γαλακτοπώλης", "retail": True, "rules": [("47.27", r"ΓΑΛΑΚΤ")]},
    {"id": "C10", "label": "Είδη διατροφής (παντοπωλείο, μίνι/σούπερ μάρκετ)", "retail": True, "rules": [("47.11", None), ("47.27", None)]},
    {"id": "C11", "label": "Ιχθυοπωλείο", "retail": True, "rules": [("47.23", None)]},
    {"id": "C12", "label": "Οπωρολαχανοπώλης", "retail": True, "rules": [("47.21", None)]},
    {"id": "C13", "label": "Ράκη/απορρίμματα", "retail": False, "rules": [("46.87", None)]},
    {"id": "C14", "label": "Ψιλικά / καπνικά", "retail": True, "rules": [("47.12.10.01", None), ("47.26", None)]},
    {"id": "C15", "label": "Πρακτορείο εφημερίδων", "retail": False, "rules": [("47.62", r"ΕΦΗΜΕΡΙΔ|ΠΕΡΙΟΔΙΚ"), ("46.49", r"ΕΦΗΜΕΡΙΔ|ΠΕΡΙΟΔΙΚ")]},
    {"id": "C16", "label": "Ανθοπωλείο", "retail": False, "rules": [("47.76", r"ΑΝΘ|ΛΟΥΛΟΥΔ|ΦΥΤ")]},
    {"id": "C17", "label": "Εστίαση, καφετέριες, μπαρ, κυλικεία", "retail": False,
     "rules": [("56.11", None), ("56.12", None), ("56.21", None), ("56.22", None), ("56.30", None), ("47", r"ΜΕΣΩ ΑΥΤΟΜΑΤΩΝ ΠΩΛΗΤΩΝ")]},
    {"id": "C18", "label": "Βιβλιοπωλείο", "retail": False, "rules": [("47.61", None), ("46.49", r"ΒΙΒΛΙ")]},
    {"id": "C19", "label": "Χαρτοπωλείο, γραφική ύλη", "retail": False, "rules": [("47.62", _RS_C19), ("46.49", _RS_C19)]},
    {"id": "C20", "label": "Εκδόσεις βιβλίων", "retail": False, "rules": [("58.11", None)]},
    {"id": "C21", "label": "Τυρόπιτες, σάντουιτς", "retail": False,
     "rules": [("10.71", r"ΠΙΤ"), ("10.89", r"ΣΑΝΤΟΥΙΤΣ|ΠΙΤ"), ("*", r"ΣΑΝΤΟΥΙΤΣ|ΤΥΡΟΠΙΤ")]},
    {"id": "C22", "label": "Φαρμακείο", "retail": False, "rules": [("47.73", None)]},
    {"id": "C23", "label": "Ηλεκτρονικά ανταλλακτικά/εξαρτήματα", "retail": False,
     "rules": [("47.40", _RS_C23), ("46.50", _RS_C23 + r"|ΗΛΕΚΤΡΟΝΙΚ")]},
    {"id": "C24", "label": "Υλικά ραπτικής/υποδηματοποιίας", "retail": False, "rules": [("47.51", _RS_C24), ("46.41", _RS_C24)]},
    {"id": "C25", "label": "Σιδηρικά, κιγκαλερία", "retail": True,
     "rules": [("47.52.01", r"ΕΡΓΑΛΕ|ΚΙΓΚΑΛ|ΚΛΕΙΔΑΡ|ΜΕΝΤΕΣ|ΚΑΡΦ|ΚΟΧΛ|ΒΙΔ|ΠΑΞΙΜΑΔ|ΣΥΝΔΕΤΗΡ|ΛΟΥΚΕΤ")]},
    {"id": "C26", "label": "Είδη υγιεινής διατροφής", "retail": True, "rules": [("47.27", r"ΥΓΙΕΙΝ|ΒΙΟΛΟΓ|ΔΙΑΙΤ")]},
    {"id": "C27", "label": "Παλαιοσίδερα/σκραπ", "retail": False, "rules": [("46.87", None), ("38.21", r"ΜΕΤΑΛΛ|ΣΙΔΗΡ|ΣΚΡΑΠ")]},
    {"id": "C28", "label": "Μεταχειρισμένα ανταλλακτικά αυτοκινήτων", "retail": False,
     "rules": [("47.82", r"ΜΕΤΑΧΕΙΡΙΣ"), ("*", r"ΜΕΤΑΧΕΙΡΙΣ.*ΑΝΤΑΛΛΑΚΤ")]},
    {"id": "C29", "label": "Ψιλικά και ζαχαρώδη (χονδρική)", "retail": False,
     "rules": [("46.36", None), ("46.39", r"ΨΙΛΙΚ"), ("47.12.10.01", None)]},
    {"id": "C30", "label": "Παλαιά γραμματόσημα", "retail": False, "rules": [("*", r"ΓΡΑΜΜΑΤΟΣΗΜ")]},
    {"id": "C31", "label": "Κλωστικά, νήματα, εργόχειρα", "retail": True, "rules": [("47.51", r"ΚΛΩΣΤ|ΝΗΜ|ΠΛΕΞ|ΕΡΓΟΧΕΙΡ|ΚΕΝΤΗΜ|ΒΕΛΟΝ")]},
    {"id": "C32", "label": "Αγρότες / αγροτικές εκμεταλλεύσεις", "retail": False, "rules": [("01.", None)]},
    {"id": "C33", "label": "Παλαιοπώλης", "retail": True, "rules": [("47.79", None)]},
    {"id": "C34", "label": "Μεταχειρισμένα είδη ένδυσης (με το βάρος)", "retail": False,
     "rules": [("47.79", r"ΕΝΔΥ|ΡΟΥΧ|ΥΦΑΣΜ"), ("*", r"ΜΕΤΑΧΕΙΡΙΣ.*(ΕΝΔΥ|ΡΟΥΧ|ΥΦΑΣΜ)")]},
    {"id": "C35", "label": "Ψευδοκοσμήματα", "retail": False, "rules": [("*", r"ΨΕΥΔΟΚΟΣΜ|ΑΠΟΜΙΜΗΣ.*ΚΟΣΜΗΜ|ΦΑΝΤΕΖΙ|ΜΠΙΖΟΥ")]},
    {"id": "C37", "label": "Καπνοβιομηχανικά (χονδρική)", "retail": False, "rules": [("46.35", None)]},
    {"id": "C38", "label": "Κατοικίδια, τροφές, διακοσμητικά ψάρια", "retail": True, "rules": [("47.76", r"ΖΩ|ΠΤΗΝ|ΨΑΡ|ΚΑΤΟΙΚΙΔ|ΤΡΟΦ")]},
    {"id": "C39", "label": "Χρώματα, βερνίκια, στόκοι", "retail": True, "rules": [("47.52.02", None)]},
    {"id": "C40", "label": "Κτηνιατρικό φαρμακείο", "retail": False, "rules": [("*", r"ΚΤΗΝΙΑΤΡ")]},
    {"id": "C41", "label": "Κρεοπωλείο", "retail": True, "rules": [("47.22", None)]},
    {"id": "C42", "label": "Περίπτερο", "retail": False, "rules": [("47.12.10.02", None)]},
]
_FUEL_RULES = [("47.30", None), ("*", r"ΠΕΤΡΕΛΑΙΟΥ ΘΕΡΜΑΝΣ")]
# Intermediation services (47.92.xx) and the department-store code that
# shares the 47.12.10 prefix are never a ΠΟΛ.1019 activity (spec §3.3).
_EXCLUDED_PREFIXES = ("47.92", "47.12.10.03")


def _rule_matches(kad: str, title_norm: str, prefix: str, regex: Optional[str]) -> bool:
    if prefix != "*" and not kad.startswith(prefix):
        return False
    if regex and not re.search(regex, title_norm):
        return False
    return True


def match_category(kad_code: str, title: str) -> Optional[Dict[str, Any]]:
    kad = normalize_kad(kad_code)
    if not kad or kad.startswith(_EXCLUDED_PREFIXES):
        return None
    t = normalize_title(title)
    for cat in CATEGORIES:
        if any(_rule_matches(kad, t, p, rx) for p, rx in cat["rules"]):
            return cat
    return None


def is_fuel_kad(kad_code: str, title: str) -> bool:
    kad = normalize_kad(kad_code)
    t = normalize_title(title)
    return bool(kad) and any(_rule_matches(kad, t, p, rx) for p, rx in _FUEL_RULES)


def count_months(start: date, end: date) -> int:
    """R5: a calendar month counts when the period covers ≥ 15 of its days."""
    if not start or not end or end < start:
        return 0
    months = 0
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        first = date(y, m, 1)
        last = date(y, m, calendar.monthrange(y, m)[1])
        days = (min(last, end) - max(first, start)).days + 1
        if days >= 15:
            months += 1
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return months


def _gr(v: float) -> str:
    """Greek money format: 120000 -> '120.000,00'."""
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fnum(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


_SERVICE_TYPE_RE = re.compile(r"^\s*2(\.\d+)?\b")
_RETAIL_SUBCODES = ("003", "004")


def goods_sales_breakdown(entries: List[dict]) -> Dict[str, float]:
    """Net sales of GOODS (εμπορεύματα + προϊόντα, R4 — services excluded) and
    the part of them sold to private consumers (retail: Ε3 561_003/004 or a
    retail receipt, invoice type 11.x), from the period's classified entries."""
    goods = retail = 0.0
    for row in entries or []:
        if str(row.get("code") or "").strip() != "561":
            continue
        category = str(row.get("classification_category") or "").upper()
        invoice_type = str(row.get("invoice_type") or "")
        if "ΥΠΗΡΕΣ" in category or _SERVICE_TYPE_RE.match(invoice_type):
            continue
        amount = _fnum(row.get("amount"))
        goods += amount
        if str(row.get("sub_code") or "").strip() in _RETAIL_SUBCODES or invoice_type.strip().startswith("11"):
            retail += amount
    return {"goods": round(goods, 2), "retail": round(retail, 2)}


def check_inventory_obligation(
    *,
    book_category: str,
    legal_form: str,
    kads: List[Dict[str, Any]],
    period_start: date,
    period_end: date,
    business_start: Optional[date],
    entries: List[dict],
    has_goods_activity: bool,
    did_inventory_prev_year: bool,
) -> Dict[str, Any]:
    """Spec §2.2. `kads`: [{"code", "title", "main": bool}] (active ones).
    Returns {status, required, reason, message, category_id, matched_kads,
    transition_note, warnings, goods_sales, goods_annual, retail_share}."""
    warnings: List[str] = []
    start = max(period_start, business_start) if business_start else period_start
    months = count_months(start, period_end)
    breakdown = goods_sales_breakdown(entries)
    goods = breakdown["goods"]
    retail_share = (breakdown["retail"] / goods) if goods > 0 else 0.0
    goods_annual = round(goods * 12 / months, 2) if 0 < months < 12 else goods
    if 0 < months < 12:
        warnings.append(f"Αναγωγή σε 12μηνο: {months} μήνες δραστηριότητας στην περίοδο (R5).")
    if period_end.month != 12 or period_end.day != 31:
        warnings.append("Η περίοδος δεν φτάνει στις 31/12 — ο έλεγχος γίνεται με τις πωλήσεις έως το «Έως».")

    base = {
        "category_id": None, "matched_kads": [], "warnings": warnings,
        "goods_sales": goods, "goods_annual": goods_annual, "retail_share": round(retail_share, 4),
        "months": months,
    }

    def result(status: str, required: bool, message: str, **extra: Any) -> Dict[str, Any]:
        out = dict(base, status=status, required=required, message=message, **extra)
        if required and not did_inventory_prev_year:
            out["transition_note"] = "Πρώτη χρονιά με απογραφή: το απόθεμα έναρξης είναι 0 (άρθ. 30 παρ. 5)."
        elif not required and did_inventory_prev_year:
            out["transition_note"] = ("Απαλλάσσεται φέτος ενώ πέρσι έκανε απογραφή: μπορεί να συνεχίσει οικειοθελώς· αν "
                                      "σταματήσει, το τελευταίο απόθεμα λήξης δεν λαμβάνεται υπόψη (άρθ. 30 παρ. 6).")
        else:
            out["transition_note"] = None
        return out

    # R1 — double-entry books (Γ κατηγορία) or a capital company.
    lf = str(legal_form or "").strip().upper()
    if str(book_category or "").strip().upper() in ("Γ", "G") or lf in _DOUBLE_ENTRY_FORMS:
        if not has_goods_activity:
            return result(OBLIGED, False,
                          "Διπλογραφικά βιβλία: η απογραφή είναι υποχρεωτική (R1), αλλά στην περίοδο δεν υπάρχουν "
                          "αγορές ή πωλήσεις αγαθών — απόθεμα 0.")
        return result(OBLIGED, True, "Διπλογραφικά βιβλία — υποχρεωτική απογραφή λήξης, χωρίς απαλλαγή (R1).")

    active = [k for k in (kads or []) if normalize_kad(k.get("code"))]
    # R7 — fuel stations come before R3/R6.
    fuel = [k for k in active if is_fuel_kad(k.get("code"), k.get("title"))]
    if fuel:
        base["matched_kads"] = [normalize_kad(k["code"]) for k in fuel]
        return result(FUEL_SPECIAL, True,
                      "Πρατήριο καυσίμων (R7): τα καύσιμα από το σύστημα εισροών-εκροών (όχι φυσική απογραφή)· "
                      "λιπαντικά/τσιγάρα και λοιπά αγαθά κρίνονται το καθένα αυτοτελώς με όριο 150.000€ — "
                      "συμπλήρωσε το απόθεμα λήξης ανάλογα.")

    # R3 — general threshold on goods sales (annualised, ≤ 150.000€).
    if goods_annual <= GOODS_THRESHOLD_EUR:
        return result(EXEMPT_THRESHOLD, False,
                      f"Απλογραφικά, πωλήσεις αγαθών {_gr(goods_annual)}€ ≤ 150.000€ — δεν υποχρεούται (R3).")

    # R6 — ΠΟΛ.1019 activities (by ΚΑΔ, as an indication).
    matches = []
    for k in active:
        cat = match_category(k.get("code"), k.get("title"))
        if cat:
            matches.append((k, cat))
    base["matched_kads"] = [normalize_kad(k["code"]) for k, _ in matches]
    main = next(((k, c) for k, c in matches if k.get("main")), None)
    over = f"Απλογραφικά, πωλήσεις αγαθών {_gr(goods_annual)}€ > 150.000€"
    if main:
        k, cat = main
        base["category_id"] = cat["id"]
        if cat["retail"] and retail_share <= 0.5:
            return result(OBLIGED, True,
                          f"{over}· ο κύριος ΚΑΔ ({normalize_kad(k['code'])}) είναι κλάδος ΠΟΛ.1019 «{cat['label']}», "
                          f"αλλά οι πωλήσεις σε ιδιώτες είναι {retail_share * 100:.0f}% (≤ 50%) — υποχρέωση απογραφής.")
        return result(LIKELY_EXEMPT_POL1019, False,
                      f"{over}, αλλά ο κύριος ΚΑΔ ({normalize_kad(k['code'])}) είναι κλάδος ΠΟΛ.1019 «{cat['label']}» — "
                      f"πιθανή απαλλαγή. Επιβεβαίωσε ότι > 50% των πωλήσεων αγαθών προέρχεται από αυτή τη δραστηριότητα"
                      + (f" (λιανική {retail_share * 100:.0f}%)." if cat["retail"] else "."))
    if matches:
        k, cat = matches[0]
        base["category_id"] = cat["id"]
        return result(REVIEW, True,
                      f"{over}· δευτερεύων ΚΑΔ ({normalize_kad(k['code'])}) σε κλάδο ΠΟΛ.1019 «{cat['label']}», όχι ο κύριος. "
                      "Απαλλαγή μόνο αν > 50% των πωλήσεων αγαθών είναι από αυτή τη δραστηριότητα — χρειάζεται έλεγχος.")
    if not active:
        warnings.append("Δεν βρέθηκαν ΚΑΔ της επιχείρησης — δεν ελέγχθηκε η ΠΟΛ.1019.")
    return result(OBLIGED, True, f"{over}, εκτός κλάδων ΠΟΛ.1019 — υποχρέωση απογραφής λήξης.")
