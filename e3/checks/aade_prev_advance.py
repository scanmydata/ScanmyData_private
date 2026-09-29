"""Προκαταβολή φόρου εισοδήματος προηγούμενου έτους από την ΑΑΔΕ (TAXISnet) —
PURE HTTP, ίδιο GSIS OAM login με το aade_profile.py.

Η προκαταβολή που εκπίπτει από τον φόρο του έτους Y είναι αυτή που
βεβαιώθηκε με τη δήλωση του φορολογικού έτους Y-1 (υποβάλλεται μέσα στο Y):

* Φυσικά πρόσωπα / ατομικές — Πράξη Διοικητικού Προσδιορισμού (εκκαθαριστικό,
  E0) φορολογικού έτους Y-1, από το μενού webtax/incomefp του έτους υποβολής Y.
  Μεταφορά του configs/aade-income.js του Tax Center (Easy_Aade.GetE1E2E3Fysiko).
  Στο πλαίσιο «Εκκαθάριση» του φορολογουμένου: «Προκαταβολή (+) <ποσό>»
  (επιβεβαιωμένο σε πραγματικά εκκαθαριστικά 2023· κενό = καμία προκαταβολή).

* Νομικά πρόσωπα / οντότητες — δήλωση Ν (Φ.Ε.Ν.Π.) χρήσης Y-1, μεταφορά του
  configs/aade-fenp.js (Easy_Aade.LoginFENP + Download_FENP). Το PDF είναι
  πρότυπο-εικόνα με τις ΤΙΜΕΣ μόνο ως κείμενο, οπότε ο κωδικός 051 «Προκαταβολή
  τρέχοντος φορολογικού έτους» διαβάζεται από τη θέση του (σελ. 5, ενότητα II,
  βαθμονομημένο σε πραγματικό Ν 2024) και ελέγχεται 051 = 049 − 050.

Ποτέ δεν πατάει κουμπιά υποβολής/τροποποίησης — μόνο ανάγνωση/εκτύπωση.
"""
from __future__ import annotations

import html
import io
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urljoin

from e3.checks.aade_profile import AADE, aade_login

log = logging.getLogger(__name__)

_AMOUNT = r"\d{1,3}(?:\.\d{3})*,\d{2}"


def _amount(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    s = s.strip()
    if not re.fullmatch(_AMOUNT, s):
        return None
    return float(s.replace(".", "").replace(",", "."))


def _is_pdf(res) -> bool:
    body = res.content or b""
    return body[:5] == b"%PDF-" or "pdf" in (res.headers.get("Content-Type") or "").lower()


def _pdf_text(pdf_bytes: bytes) -> str:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


# ------------------------------------------------------------------ parsing --

def parse_e0_advance(text: str) -> Optional[Dict[str, Any]]:
    """Εκκαθαριστικό φυσικού προσώπου -> {fiscal_year, afm, advance}.
    None όταν το κείμενο δεν μοιάζει με Πράξη Διοικητικού Προσδιορισμού."""
    if "ΠΡΑΞΗ ΔΙΟΙΚΗΤΙΚΟΥ ΠΡΟΣΔΙΟΡΙΣΜΟΥ" not in text and "ΕΚΚΑΘΑΡΙΣΗ ΦΟΡΟΥ" not in text:
        return None
    fy = re.search(r"ΦΟΡΟΛΟΓΙΚΟ ΕΤΟΣ\s+(\d{4})", text)
    # The Πράξη's own taxpayer is section Α («ΑΦΜ - ΟΝΟΜΑΤΕΠΩΝΥΜΟ ΦΟΡ/ΝΟΥ») —
    # section Β always shows the joint return's υπόχρεος, even on the
    # spouse's εκκαθαριστικό (verified on a real spouse copy).
    afm = (re.search(r"ΑΦΜ - ΟΝΟΜΑΤΕΠΩΝΥΜΟ ΦΟΡ/ΝΟΥ\s*(\d{9})", text)
           or re.search(r"ΑΡΙΘΜΟΣ ΦΟΡΟΛΟΓΙΚΟΥ ΜΗΤΡΩΟΥ \(ΑΦΜ\)\s*(\d{9})", text))
    # Same line only: when there's no advance the label is followed by a
    # line break and the NEXT line's numbers must not be picked up.
    box = re.search(r"Προκαταβολ[ήη]\s*\(\+\)[ \t]*(" + _AMOUNT + r")?[ \t]*$", text, re.M)
    nxt = re.search(r"ΠΡΟΚΑΤΑΒΟΛΗ ΕΠΟΜΕΝΟΥ ΕΤΟΥΣ[ \t]+(" + _AMOUNT + ")", text)
    if not box and not nxt:
        return None
    advance = _amount(box.group(1)) if box and box.group(1) else None
    if advance is None:
        advance = _amount(nxt.group(1)) if nxt else 0.0
    return {
        "fiscal_year": int(fy.group(1)) if fy else None,
        "afm": afm.group(1) if afm else None,
        "advance": round(advance, 2),
    }


# Ν (Φ.Ε.Ν.Π.), σελίδα 5, ενότητα «II. ΥΠΟΛΟΓΙΣΜΟΣ ΠΡΟΚΑΤΑΒΟΛΗΣ»: κέντρα
# γραμμών (pt από πάνω) και στήλη τιμών — βαθμονομημένα σε πραγματικό PDF.
_FENP_ROWS = {"049": 55.5, "050": 67.5, "051": 79.4}
_FENP_VALUE_X = (470.0, 590.0)
_FENP_TAX_INLINE = ((140.0, 300.0), (45.0, 63.0))  # «Φόρος κερδών ____ x 80%»


def parse_fenp_advance(pdf_bytes: bytes) -> Optional[Dict[str, Any]]:
    """Δήλωση Ν -> {fiscal_year, tax_049, withheld_050, advance, consistent}."""
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = pdf.pages
        first_text = pages[0].extract_text() or "" if pages else ""
        fy = re.search(r"\d{1,2}/\d{1,2}/(\d{4})\s*ε[ώω]ς\s*\d{1,2}/\d{1,2}/(\d{4})", first_text)
        order = [4] + [i for i in range(len(pages)) if i != 4]
        for idx in order:
            if idx >= len(pages):
                continue
            words = pages[idx].extract_words()
            (ax0, ax1), (ay0, ay1) = _FENP_TAX_INLINE
            anchor = [w for w in words if ax0 <= w["x0"] <= ax1 and ay0 <= w["top"] <= ay1]
            if not anchor:
                continue
            vals: Dict[str, Optional[float]] = {}
            for code, yc in _FENP_ROWS.items():
                hit = [w for w in words
                       if _FENP_VALUE_X[0] <= w["x0"] and w["x1"] <= _FENP_VALUE_X[1]
                       and abs((w["top"] + w["bottom"]) / 2 - yc) <= 5.5]
                vals[code] = _amount(hit[0]["text"]) if hit else None
            advance = vals["051"] if vals["051"] is not None else 0.0
            consistent = True
            if vals["049"] is not None:
                expected = max(0.0, vals["049"] - (vals["050"] or 0.0))
                consistent = abs(expected - advance) <= 1.0
            return {
                "fiscal_year": int(fy.group(2)) if fy else None,
                "tax_049": vals["049"],
                "withheld_050": vals["050"],
                "advance": round(advance, 2),
                "consistent": consistent,
                "page": idx + 1,
            }
    return None


# ------------------------------------------------------------------ fetching --

def _natural_e0_pdfs(http, year: int) -> Tuple[List[Tuple[str, bytes]], str]:
    """(pdfs, status) — εκκαθαριστικό υπόχρεου + συζύγου της δήλωσης που
    υποβλήθηκε το `year` (δηλ. φορολογικό έτος year-1)."""
    H = lambda rel: urljoin(AADE, "/webtax/incomefp/" + rel)
    menu = http.follow("GET", H(f"year{year}-income-menu.do"))["text"]
    code = lambda name: (re.search(r'name="' + name + r'"[^>]*value="([^"]*)"', menu, re.I) or [None, ""])[1]
    out: List[Tuple[str, bytes]] = []
    for flag, button, code_name in (("print_e0_ypo", "PB_EKKATH_PDF", "PRINT_CODE"),
                                    ("print_e0_syz", "PB_EKKATH_PDF_SYZ", "PRINT_CODE_SYZ")):
        btn = re.search(r"<(?:button|input)[^>]*name=\"" + button + r"\"[^>]*>", menu, re.I)
        if year >= 2024:
            if not btn or re.search("disabled", btn.group(0), re.I):
                continue
            form = {"YEAR": str(year), "e1_print": "", "e3_print": "", "print_e2_ypo": "", "print_e2_syz": "", flag: flag}
            _u, res = http.follow_raw("POST", H(f"year{year}-income-menuPrint.do"), form)
        else:
            pc = code(code_name)
            if not pc:
                continue
            report = f"E0Form{str(year)[-2:]}.rdf"
            _u, res = http.follow_raw("POST", urljoin(AADE, "/reports/rwservlet"), {
                "cmdkey": "INC00S", "p_afm": pc, "report": report, "desname": report,
                "desformat": "pdf", "destype": "cache",
            })
        if _is_pdf(res):
            out.append((flag, res.content))
    return out, ("ok" if out else "Δεν βρέθηκε εκκαθαριστικό (η δήλωση δεν έχει εκκαθαριστεί ή δεν υπάρχει)")


def _rows(page: str) -> List[List[str]]:
    rows = re.findall(r"<tr\b[^>]*class=\"tblRow[12]\"[^>]*>([\s\S]*?)</tr>", page, re.I)
    return [re.findall(r"<td\b[^>]*>([\s\S]*?)</td>", r, re.I) for r in rows]


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _call_args(page: str, fn: str) -> Optional[List[str]]:
    m = re.search(re.escape(fn) + r"\s*\(\s*document\.[A-Za-z0-9_]+([^;]*?)\)\s*;", page)
    if not m:
        return None
    return [a.replace('"', " ").replace("'", " ").strip() for a in m.group(1).lstrip(" ,").split(",") if a.strip()]


def _fenp_pdf(http, year: int) -> Tuple[Optional[bytes], str]:
    """Δήλωση Ν χρήσης year-1 (έτος αναφοράς `year`)."""
    P = lambda rel: urljoin(AADE, "/taxisnet/income/protected/" + rel)
    http.follow("GET", P("displayActorRoles.htm"))
    types = http.follow("POST", P("displayDeclarationTypes.htm"), {"actorRole": "SELF_SERVICE"})["text"]
    g = (re.search(r'<form[^>]*name="gotoincomeN"[^>]*action="([^"]*)"', types, re.I)
         or re.search(r'<form[^>]*action="([^"]*)"[^>]*name="gotoincomeN"', types, re.I))
    if not g:
        return None, "Δεν βρέθηκε η ενότητα δηλώσεων Ν για αυτόν τον λογαριασμό"
    dec_type = (re.search(r'name="declarationType"[^>]*value="([^"]*)"', types, re.I) or [None, "incomeN"])[1]
    g_year = (re.search(r'name="year"[^>]*value="([^"]*)"', types, re.I) or [None, str(year)])[1]
    landing_url = urljoin(P(""), html.unescape(g.group(1))) + "?" + urlencode({"declarationType": dec_type, "year": g_year})
    landing = http.follow("GET", landing_url)["text"]
    list_params = None
    for tds in _rows(landing):
        cell0 = _strip(tds[0] if tds else "")
        parts = cell0.split("-")
        y = re.search(r"(\d{4})", parts[0]) if len(parts) == 2 else None
        if not y or int(y.group(1)) != year - 1:
            continue
        if len(tds) < 3 or _strip(tds[2]).replace(" ", "") != "ΕπεξεργασίαΔηλώσεων":
            continue
        i = tds[2].find("doDisplayDeclarationsList(")
        if i < 0:
            continue
        j = tds[2].find(");", i)
        a = [x.replace('"', " ").replace("'", " ").strip() for x in tds[2][i + len("doDisplayDeclarationsList("):j].split(",")]
        if len(a) >= 8:
            list_params = {"declarationType": a[1], "year": a[2], "periodType": a[3], "periodStart": a[4],
                           "periodEnd": a[5], "effectivePeriodStart": a[6], "effectivePeriodEnd": a[7]}
            break
    if not list_params:
        return None, f"Δεν βρέθηκε υποβληθείσα δήλωση Ν για τη χρήση {year - 1}"
    lst = http.follow("GET", P("displayDeclarationsList.htm") + "?" + urlencode(list_params))["text"]
    a_net = _call_args(lst, "doViewPdfTaxisnet")
    a_tax = _call_args(lst, "doViewPdfTaxis")
    if a_net and len(a_net) >= 2:
        params = {"declarationDatabaseId": a_net[0], "declarationType": a_net[1]}
    elif a_tax and len(a_tax) >= 12 and re.search("income", a_tax[0], re.I):
        params = {"taxisPK.num": a_tax[3], "taxisPK.doy": a_tax[4], "taxisPK.year": a_tax[5],
                  "taxisPK.taxArea": a_tax[6], "taxisPK.docType": a_tax[7], "effectivePeriod.start": a_tax[8],
                  "effectivePeriod.end": a_tax[9], "submissionDate": a_tax[10], "submissionType": a_tax[1],
                  "declarationType": a_tax[0], "periodType": a_tax[2], "referenceYear": a_tax[11]}
    else:
        return None, "Δεν βρέθηκε «Προβολή» της δήλωσης Ν"
    _u, res = http.follow_raw("POST", P("viewPdf.htm"), params)
    if not _is_pdf(res):
        return None, "Η ΑΑΔΕ δεν επέστρεψε PDF της δήλωσης Ν"
    return res.content, "ok"


def fetch_prev_year_advance(username: str, password: str, afm: str, year: int,
                            company_type: str) -> Dict[str, Any]:
    """Προκαταβολή που εκπίπτει στο φορολογικό έτος `year` (βεβαιώθηκε με τη
    δήλωση του year-1). company_type: "natural" | "legal" (άγνωστο -> natural
    και μετά Ν). -> {ok, amount, form, fiscal_year, ...} ή {ok: False, error}."""
    L = aade_login(username, password)
    if not L.get("ok"):
        return {"ok": False, "error": f"Αποτυχία σύνδεσης TAXISnet ({L.get('reason')})"}
    http = L["http"]
    # Fresh budget for the PDF part (το Ν είναι ~10MB), kept well under the
    # gunicorn/gateway timeout of the compute request this runs inside.
    http.deadline = time.monotonic() + 75
    order = ["legal", "natural"] if company_type == "legal" else ["natural", "legal"]
    errors: List[str] = []
    for kind in order:
        try:
            if kind == "natural":
                pdfs, status = _natural_e0_pdfs(http, year)
                if not pdfs:
                    errors.append("Εκκαθαριστικό: " + status)
                    continue
                parsed_any = None
                for flag, body in pdfs:
                    p = parse_e0_advance(_pdf_text(body))
                    if not p:
                        continue
                    parsed_any = parsed_any or p
                    if p.get("afm") == afm:
                        parsed_any = p
                        break
                if not parsed_any:
                    errors.append("Εκκαθαριστικό: άγνωστη μορφή PDF")
                    continue
                if parsed_any.get("fiscal_year") and parsed_any["fiscal_year"] != year - 1:
                    errors.append(f"Εκκαθαριστικό: βρέθηκε έτος {parsed_any['fiscal_year']} αντί {year - 1}")
                    continue
                return {"ok": True, "amount": parsed_any["advance"], "form": "E0",
                        "fiscal_year": parsed_any.get("fiscal_year") or year - 1,
                        "afm_matched": parsed_any.get("afm") == afm}
            body, status = _fenp_pdf(http, year)
            if not body:
                errors.append("Δήλωση Ν: " + status)
                continue
            p = parse_fenp_advance(body)
            if not p:
                errors.append("Δήλωση Ν: άγνωστη μορφή PDF")
                continue
            return {"ok": True, "amount": p["advance"], "form": "N",
                    "fiscal_year": p.get("fiscal_year") or year - 1,
                    "consistent": p.get("consistent", True), "tax_049": p.get("tax_049")}
        except Exception as e:  # network / unexpected markup -> try the other kind
            log.exception("prev-year advance fetch (%s) failed for afm=%s", kind, afm)
            errors.append(f"{'Εκκαθαριστικό' if kind == 'natural' else 'Δήλωση Ν'}: {e}")
    return {"ok": False, "error": " · ".join(errors) or "Δεν βρέθηκε προκαταβολή στην ΑΑΔΕ"}
