"""POL.1237/11.11.2014 mappings for E9 / ENFIA property data.

Codes are pulled from the official circular and applied when parsing the
ENFIA εκκαθαριστικό PDF — the per-ATAK row contains the numeric codes
(στήλη 9 «Κατηγορία Ακινήτου», στήλη 11 «Όροφος», στήλη 12 «Επιφάνεια
κύριων χώρων»). The UI shows the human-readable label next to the code
so the user can pick the right ATAK for ιδιόχρηση.
"""

import re
from typing import Any, Dict, List, Optional


PROPERTY_CATEGORY_LABELS: Dict[str, str] = {
    # Κτίσματα (Πίνακας 1)
    "1": "Κατοικία / Διαμέρισμα",
    "2": "Μονοκατοικία",
    "3": "Επαγγελματική στέγη",
    "4": "Οικόπεδο",
    "5": "Αποθήκη",
    "6": "Θέση στάθμευσης",
    "7": "Σταθμός αυτοκινήτων",
    "8": "Βιομηχανικό / βιοτεχνικό κτίριο",
    "9": "Τουριστική εγκατάσταση / Νοσηλευτήριο / Ευαγές Ίδρυμα",
    "10": "Εκπαιδευτήριο",
    "11": "Αθλητική εγκατάσταση",
    "12": "Ειδικό κτίσμα",
    "13": "Τίτλος μεταφοράς Σ.Δ.",
    # Οικόπεδα ειδικών χρήσεων
    "41": "Οικόπεδο - ελλιμενισμός α/φ δημ. χρήσης",
    "42": "Οικόπεδο - ελλιμενισμός α/φ ιδιωτ. χρήσης",
    "43": "Οικόπεδο - σιδηροτροχιές",
    "44": "Οικόπεδο - πύργοι / γραμμές μεταφοράς ηλ. ενέργειας",
    "45": "Οικόπεδο εντός βιομηχανικής περιοχής",
    "46": "Οικόπεδο εντός βιομηχ. επιχειρ. περιοχής",
    "47": "Οικόπεδο εντός επιχειρηματικού πάρκου",
    # Γεωργικά / κτηνοτροφικά (Πίνακας 1 ειδικά κτίρια)
    "51": "Ειδικό κτίριο γεωργικής χρήσης",
    "52": "Ειδικό κτίριο κτηνοτροφικής χρήσης",
}


FLOOR_LABELS: Dict[str, str] = {
    "Υ": "Υπόγειο",
    "Y": "Υπόγειο",
    "0": "Ισόγειο / ημιυπόγειο",
    "1": "1ος όροφος / ημιόροφος",
    "2": "2ος όροφος",
    "3": "3ος όροφος",
    "4": "4ος όροφος",
    "5": "5ος όροφος",
    "6": "6ος όροφος",
    "7": "7ος όροφος",
    "8": "8ος όροφος",
    "9": "9ος όροφος",
}


def label_for_category(code: Any) -> str:
    s = str(code or "").strip()
    return PROPERTY_CATEGORY_LABELS.get(s, "")


def label_for_floor(code: Any) -> str:
    s = str(code or "").strip().upper()
    if s in FLOOR_LABELS:
        return FLOOR_LABELS[s]
    if s.isdigit():
        n = int(s)
        if n > 9:
            return f"{n}ος όροφος"
    return ""


_DEC_RE = re.compile(r"^\d{1,4},\d{1,2}$")
_BIG_DEC_RE = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{2}$")
_OWN_RE = re.compile(r"^\d{1,3},\d{5}$")
_YEAR_RE = re.compile(r"^(18|19|20)\d{2}$")


def _to_float(text: str) -> Optional[float]:
    try:
        return float(str(text or "").replace(".", "").replace(",", "."))
    except Exception:
        return None


def parse_property_row(row_cells: List[Any]) -> Optional[Dict[str, Any]]:
    """Pick out category / όροφος / sqm / value / έτος / ποσοστό from a
    Πίνακας 1 ENFIA PDF row.

    The PDF layout (ΕΝΦΙΑ εκκαθαριστικό) places the per-ATAK fields in a
    deterministic order *after* the ATAK and address cells:

        Πλήθος Προσόψεων | ΑΑΠΑ Ένδειξη | Κατηγορία | (Ειδ. Συνθηκών) |
        Όροφος | Κύριοι Χώροι (τμ) | Βοηθητικοί | (μήκος) | Έτος |
        Είδος Δικαιώματος | Ποσοστό | … | Ηλεκτροδ. | … | Τιμή Ζώνης |
        Κύριος Φόρος | Αξία Ακινήτου

    pdfplumber's table extractor preserves the empty cells around merged
    headers; we drop empties first, then find the first cell that looks
    like Επιφάνεια (X,XX) and read the 3-5 short tokens that precede it.
    """
    cells = [str(c or "").strip() for c in (row_cells or [])]
    cells = [c for c in cells if c]
    if not cells:
        return None

    sqm_idx = None
    for i, c in enumerate(cells):
        if _DEC_RE.match(c):
            sqm_idx = i
            break

    sqm = _to_float(cells[sqm_idx]) if sqm_idx is not None else None

    short_tokens: List[str] = []
    if sqm_idx is not None:
        for c in cells[:sqm_idx]:
            cu = c.upper()
            if (len(c) <= 2 and c.isdigit()) or cu in ("Υ", "Y"):
                short_tokens.append(c)

    category_code = None
    floor_code = None
    if len(short_tokens) >= 5:
        # ΑΑΠΑ, Πλήθος, Κατηγορία, ΕιδΣυνθ, Όροφος
        category_code = short_tokens[-3]
        floor_code = short_tokens[-1]
    elif len(short_tokens) == 4:
        # ΑΑΠΑ, Πλήθος, Κατηγορία, Όροφος (ΕιδΣυνθ blank → omitted)
        category_code = short_tokens[-2]
        floor_code = short_tokens[-1]
    elif len(short_tokens) == 3:
        # Οικόπεδο case (no Όροφος): ΑΑΠΑ, Πλήθος, Κατηγορία
        category_code = short_tokens[-1]
        floor_code = None

    # When κατηγορία maps to οικόπεδο (4 / 41-47), the "floor" we picked up
    # is actually the κατηγορία digit — recover by demoting it.
    if category_code and category_code not in PROPERTY_CATEGORY_LABELS:
        if floor_code in PROPERTY_CATEGORY_LABELS:
            category_code, floor_code = floor_code, None

    value = None
    if sqm_idx is not None:
        big_nums = []
        for c in cells[sqm_idx + 1 :]:
            if _BIG_DEC_RE.match(c):
                v = _to_float(c)
                if v is not None and v > 0:
                    big_nums.append(v)
        if big_nums:
            value = max(big_nums)

    year = None
    for c in cells:
        if _YEAR_RE.match(c):
            year = int(c)
            break

    ownership_pct = None
    for c in cells:
        if _OWN_RE.match(c):
            v = _to_float(c)
            if v is not None and 0 < v <= 100:
                ownership_pct = v
                break

    return {
        "category_code": category_code,
        "category_label": label_for_category(category_code),
        "floor_code": floor_code,
        "floor_label": label_for_floor(floor_code),
        "sqm": sqm,
        "value": value,
        "year": year,
        "ownership_pct": ownership_pct,
    }
