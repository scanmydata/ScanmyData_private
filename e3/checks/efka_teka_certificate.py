"""Φορολογικές Βεβαιώσεις ΕΦΚΑ / ΤΕΚΑ (φορολογικής χρήσης).

Πύλη: services.e-efka.gov.gr (non-employee) -> «Δικαιώματα Ασφάλισης»
(insuranceRoyalties.xhtml) -> tabs «Φορολογικές Βεβαιώσεις» / «Φορολογικές
Βεβαιώσεις TEKA» (JSF/PrimeFaces accordion, form-id
form-id:accordion-insurance-royalties-citizen). Κατεβάζει το PDF βεβαίωσης
ανά έτος και εξάγει το συνολικό ποσό ασφαλιστικών εισφορών, για αντιπαραβολή
με τον πίνακα Ε3 (585.007) στο e3_brain.py.

Login: ΙΔΙΑ αποδεδειγμένη TAXISnet ροή με efka-extractor.py / teka-extractor.py
(«services.e-efka.gov.gr non-employee» — landing www.e-efka.gov.gr, popup,
«Συνέχεια στο TAXISNET», user/pass, προαιρετικό radio+ΑΜΚΑ βήμα).

Navigation: τα tabs αλλάζουν με ΠΡΑΓΜΑΤΙΚΟ Playwright κλικ — αφήνουμε το
JSF/PrimeFaces να χειριστεί μόνο του το ViewState/AJAX (πιο αξιόπιστο από το
να αναπαράγουμε χειροκίνητα το partial-request πρωτόκολλο). Το PDF κατεβαίνει
με το ΙΔΙΟ page.evaluate(fetch(...)) trick του kartela_ergodoti.py: POST της
φόρμας που περιέχει το κουμπί «Εκτύπωση» απευθείας μέσα στη σελίδα, λήψη των
PDF bytes ως base64 — αποφεύγει popup/νέα καρτέλα.

CLI:
    python efka_teka_certificate.py --username <taxisnet_user> --password <taxisnet_pass> \\
        --afm <afm> [--amka <amka>] [--year 2025] --pdf-dir <dir> --summary-out <path> [--headless]

ΣΗΜΕΙΩΣΗ (σημαντικό): η αναγνώριση του «συνολικού ποσού» μέσα στο PDF γίνεται
με αναζήτηση ετικετών (Σύνολο/Εισφορές/Ποσό) πάνω σε γραμμές κειμένου — ΔΕΝ
έχει επικυρωθεί ακόμη έναντι πραγματικού certificate PDF (χρειάζεται ζωντανό
τεστ με πραγματικούς κωδικούς, που ΔΕΝ τρέχουμε εμείς εκ μέρους του χρήστη).
Το summary JSON εκθέτει ΟΛΟΥΣ τους υποψήφιους αριθμούς + απόσπασμα κειμένου,
ώστε ο χρήστης να επαληθεύσει/διορθώσει το best_guess πριν εμπιστευτεί την
αντιπαραβολή.
"""

from __future__ import annotations

import argparse
import base64 as _b64
import io
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from playwright.sync_api import (
    Page,
    BrowserContext,
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

try:
    from e3.checks import chromium_launch_args, default_headless
except Exception:  # pragma: no cover — running standalone, not as a module
    def chromium_launch_args(extra=None):
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-software-rasterizer",
        ]
        if extra:
            args.extend(extra)
        return args

    def default_headless():
        val = os.getenv("E3_HEADLESS") or os.getenv("HEADLESS")
        if val is None:
            return False
        return val.strip() in {"1", "true", "yes", "on"}


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Realistic desktop UA — βλ. lesson του kartela_ergodoti.py: χωρίς αυτό, σε
# headless το gov site μπλοκάρει content ως bot.
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Direct Keycloak-hinted entry point (2026-09 site structure): ανοίγει
# ΚΑΤΕΥΘΕΙΑΝ (χωρίς popup) το login της GSIS OAuth2 (oauth2.gsis.gr,
# #j_username/#j_password/#btn-login-submit — ΙΔΙΑ φόρμα με άλλα e-EFKA/GSIS
# logins σε αυτόν τον κώδικα) και μετά την επιτυχή σύνδεση επιστρέφει
# κατευθείαν στο insuranceRoyalties.xhtml (Δικαιώματα Ασφάλισης) — άρα δεν
# χρειάζεται ξεχωριστό βήμα πλοήγησης μετά το login. Επιβεβαιώθηκε live
# (χωρίς credentials) στις 2026-09-10.
ROY_LOGIN_URL = (
    "https://services.e-efka.gov.gr/ssp.efka.non.employees/views/"
    "insuranceRoyalties.xhtml?kc_idp_hint=taxisnet-employer&login"
)
# Fallback αν το login URL αλλάξει πάλι: η ίδια σελίδα χωρίς query (σε
# περίπτωση που η session είναι ήδη ενεργή/cached).
ROY_PATH_FALLBACK = "/ssp.efka.non.employees/views/insuranceRoyalties.xhtml"
FORM_ID = "form-id:accordion-insurance-royalties-citizen"
TAB_LABEL = {"EFKA": "Φορολογικές Βεβαιώσεις", "TEKA": "Φορολογικές Βεβαιώσεις TEKA"}

NAV_TIMEOUT = 45000
NETWORK_TIMEOUT = 30000
POPUP_TIMEOUT = 20000

_AMOUNT_RE = re.compile(r"(?<!\d)\d{1,3}(?:\.\d{3})*,\d{2}(?!\d)")
_TOTAL_KEYWORDS = ("συνολ", "εισφορ", "ποσο", "ποσό")


def _safe_name(token: str, fallback: str = "row") -> str:
    token = (token or "").strip()
    token = re.sub(r"[^\w\-Ͱ-Ͽἀ-῿.]+", "_", token, flags=re.U)
    return token or fallback


def _to_float_amount(s: str) -> float:
    try:
        return float(s.replace(".", "").replace(",", "."))
    except Exception:
        return 0.0


# --------------------------------------------------------------------- login
def _login_efka_non_employee(context: BrowserContext, username: str, password: str,
                              amka: str = "") -> Page:
    """TAXISnet login (GSIS OAuth2, Keycloak-brokered) απευθείας στη σελίδα
    «Δικαιώματα Ασφάλισης». ΧΩΡΙΣ popup — απλή πλοήγηση στην ΙΔΙΑ καρτέλα:

        goto(ROY_LOGIN_URL) -> redirect σε oauth2.gsis.gr/oauth2server/login.jsp
        (#j_username, #j_password, #btn-login-submit — ίδια GSIS φόρμα με τα
        υπόλοιπα e-EFKA logins) -> μετά την επιτυχή σύνδεση, redirect ΠΙΣΩ στο
        insuranceRoyalties.xhtml (δεν χρειάζεται ξεχωριστό βήμα πλοήγησης).

    Επιβεβαιώθηκε live (headless, χωρίς credentials) ότι η ροή φτάνει μέχρι
    τη GSIS login φόρμα. Το προαιρετικό radio+ΑΜΚΑ βήμα (πολλαπλοί ρόλοι
    TAXISnet) κρατιέται ως fallback — ΔΕΝ έχει επιβεβαιωθεί αν εμφανίζεται
    σε αυτή τη ροή."""
    page = context.new_page()
    page.set_default_timeout(NAV_TIMEOUT)
    logging.info("Opening ΕΦΚΑ non-employee login %s", ROY_LOGIN_URL)
    page.goto(ROY_LOGIN_URL, timeout=NAV_TIMEOUT)
    try:
        page.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
    except PlaywrightTimeoutError:
        pass

    user_field = page.locator("#j_username")
    if user_field.count() == 0:
        user_field = page.get_by_role("textbox", name="Χρήστης:")
    pass_field = page.locator("#j_password")
    if pass_field.count() == 0:
        pass_field = page.locator("input[type='password']")

    if user_field.count() and pass_field.count():
        user_field.first.fill(username, timeout=NETWORK_TIMEOUT)
        pass_field.first.fill(password, timeout=NETWORK_TIMEOUT)
        login_btn = page.locator("#btn-login-submit")
        if login_btn.count() == 0:
            login_btn = page.get_by_role("button", name="Σύνδεση")
        try:
            with page.expect_navigation(timeout=NAV_TIMEOUT):
                login_btn.first.click()
        except PlaywrightTimeoutError:
            try:
                login_btn.first.click()
            except Exception:
                pass
        try:
            page.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
        except PlaywrightTimeoutError:
            pass
    else:
        logging.warning(
            "Δεν βρέθηκαν πεδία GSIS login (#j_username/#j_password) — "
            "πιθανόν ήδη συνδεδεμένος (cached session) ή η σελίδα άλλαξε."
        )

    cur = page.url or ""
    low = (page.locator("body").inner_text(timeout=5000) or "").lower()
    if "login.jsp" in cur or "authentication_error" in cur.lower() or "λάθος" in low:
        raise RuntimeError("Αποτυχία σύνδεσης TAXISnet — ελέγξτε τους κωδικούς.")

    # Προαιρετικό βήμα radio + ΑΜΚΑ (εμφανιζόταν στην ΠΑΛΙΑ ροή όταν ο
    # χρήστης έχει πάνω από έναν ρόλο/οντότητα στο TAXISnet). Ενεργοποιείται
    # μόνο αν όντως εμφανιστούν τα στοιχεία — ΔΕΝ έχει επιβεβαιωθεί αν
    # εξακολουθεί να υπάρχει στη ΝΕΑ Keycloak-brokered ροή.
    if page.locator("input[type='radio']").count() > 0 or page.get_by_role("button", name="Αποστολή").count() > 0:
        page.wait_for_selector("input[type='radio']", timeout=NETWORK_TIMEOUT)
        radios = page.locator("input[type='radio']")
        if radios.count() > 1:
            radios.nth(1).check()
        else:
            role_radios = page.get_by_role("radio")
            if role_radios.count() > 1:
                role_radios.nth(1).check()
            else:
                raise RuntimeError("Δεν βρέθηκε δεύτερη επιλογή radio στη σελίδα TAXISnet.")

        submit_btn = page.get_by_role("button", name="Αποστολή")
        if submit_btn.count() == 0:
            submit_btn = page.locator(
                "input[type='submit'][value*='Αποστολή'], button:has-text('Αποστολή')"
            ).first
        submit_btn.click()
        try:
            page.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
        except PlaywrightTimeoutError:
            pass

        if amka:
            amka_field = page.get_by_role("textbox", name="ΑΜΚΑ:")
            if amka_field.count() == 0:
                amka_field = page.locator("input[name*='AMKA'], input[id*='AMKA']")
            if amka_field.count():
                amka_field.first.fill(amka, timeout=NETWORK_TIMEOUT)
                enter_btn = page.get_by_role("button", name="Είσοδος")
                if enter_btn.count() == 0:
                    enter_btn = page.locator(
                        "input[type='submit'][value*='Εισοδος'], button:has-text('Είσοδος')"
                    ).first
                try:
                    with page.expect_navigation(timeout=NAV_TIMEOUT):
                        enter_btn.click()
                except PlaywrightTimeoutError:
                    enter_btn.click()
                try:
                    page.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
                except PlaywrightTimeoutError:
                    pass

    return page


# ---------------------------------------------------------------- navigation
def _goto_insurance_royalties(portal: Page) -> str:
    """Το login (ROY_LOGIN_URL) επιστρέφει ΗΔΗ στο insuranceRoyalties.xhtml —
    αυτό είναι απλώς ένας ασφαλιστικός έλεγχος/fallback αν όχι."""
    if "insuranceRoyalties" in (portal.url or ""):
        return portal.url
    target = urljoin(portal.url, ROY_PATH_FALLBACK)
    logging.info("Δεν βρισκόμαστε ήδη στο insuranceRoyalties.xhtml — goto %s", target)
    portal.goto(target, timeout=NAV_TIMEOUT)
    try:
        portal.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
    except PlaywrightTimeoutError:
        pass
    return portal.url


def _select_tab(portal: Page, label: str) -> bool:
    """ΠΡΑΓΜΑΤΙΚΟ κλικ στο tab — αφήνει το JSF/PrimeFaces να χειριστεί μόνο
    του το ViewState/AJAX (πιο αξιόπιστο από manual replay του partial-request)."""
    tab = portal.get_by_role("tab", name=label, exact=False)
    if tab.count() == 0:
        tab = portal.locator("li.ui-tabs-header, li[role='tab']", has_text=label)
    if tab.count() == 0:
        tab = portal.locator("a", has_text=label)
    if tab.count() == 0:
        return False
    tab.first.click()
    try:
        portal.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
    except PlaywrightTimeoutError:
        pass
    portal.wait_for_timeout(700)  # JSF ajax settle
    return True


def _read_year_rows(portal: Page) -> List[Dict[str, Any]]:
    """Διάβασε τις γραμμές του datatable (έτος + id κουμπιού «Εκτύπωση») ΜΕΣΩ
    DOM (Playwright locators) — όχι χειροκίνητο parsing HTML."""
    rows_locator = portal.locator("tr.ui-datatable-even, tr.ui-datatable-odd")
    n = rows_locator.count()
    out: List[Dict[str, Any]] = []
    for i in range(n):
        row = rows_locator.nth(i)
        cells = row.locator("td")
        if cells.count() == 0:
            continue
        year_text = (cells.nth(0).inner_text() or "").strip()
        if not re.fullmatch(r"\d{4}", year_text):
            continue
        btn = row.locator("button[id]")
        if btn.count() == 0:
            continue
        btn_id = btn.first.get_attribute("id")
        if not btn_id:
            continue
        out.append({"year": year_text, "btn_id": btn_id})
    return out


def _fetch_pdf_via_button(portal: Page, button_id: str, label: str) -> Dict[str, Any]:
    """Υπόβαλε τη φόρμα που περιέχει το κουμπί button_id μέσω fetch() μέσα
    στη σελίδα (ίδιο trick με kartela_ergodoti.py::_download_report_pdf) και
    πάρε τα PDF bytes απευθείας — αποφεύγει popup/νέα καρτέλα δυσκολίες."""
    try:
        result = portal.evaluate(
            """async (btnId) => {
                const btn = document.getElementById(btnId);
                if (!btn) return {ok:false, status:0, ct:'', text:'button not found: ' + btnId};
                const form = btn.closest('form');
                if (!form) return {ok:false, status:0, ct:'', text:'enclosing form not found'};
                const fd = new FormData(form);
                if (btn.name) fd.append(btn.name, btn.value || '');
                else fd.append(btnId, '');
                form.target = '_self';
                const body = new URLSearchParams();
                for (const [k, v] of fd.entries()) {
                    if (typeof v === 'string') body.append(k, v);
                }
                const resp = await fetch(form.action || window.location.href, {
                    method: 'POST', body: body, credentials: 'include',
                });
                const ct = resp.headers.get('content-type') || '';
                if (!resp.ok || !/pdf|octet-stream/i.test(ct)) {
                    return {ok:false, status:resp.status, ct, text:(await resp.text()).slice(0,400)};
                }
                const buf = await resp.arrayBuffer();
                const u8 = new Uint8Array(buf);
                let bin = '';
                const chunk = 0x8000;
                for (let i = 0; i < u8.length; i += chunk) {
                    bin += String.fromCharCode.apply(null, u8.subarray(i, i + chunk));
                }
                return {ok:true, status:resp.status, ct, b64: btoa(bin)};
            }""",
            button_id,
        )
    except Exception as exc:
        return {"ok": False, "pdf_bytes": None, "error": f"{label}: fetch() στη φόρμα απέτυχε: {exc}"}

    if not result or not result.get("ok"):
        return {"ok": False, "pdf_bytes": None,
                "error": f"{label}: το server response δεν ήταν PDF "
                         f"(status={result.get('status')!r}, ct={result.get('ct')!r}, "
                         f"text={str(result.get('text',''))[:200]!r})"}
    try:
        body_bytes = _b64.b64decode(result["b64"])
    except Exception as exc:
        return {"ok": False, "pdf_bytes": None, "error": f"{label}: decode του PDF body απέτυχε: {exc}"}
    if not body_bytes or body_bytes[:4] != b"%PDF":
        return {"ok": False, "pdf_bytes": None,
                "error": f"{label}: το payload δεν ξεκινάει με %PDF ({len(body_bytes)} bytes)"}
    return {"ok": True, "pdf_bytes": body_bytes, "error": None}


# ------------------------------------------------------------ PDF amount(s)
def extract_amount_from_pdf(pdf_bytes: bytes) -> Dict[str, Any]:
    """Εξαγωγή υποψήφιων χρηματικών ποσών από το PDF βεβαίωσης.

    ΔΕΝ έχει επικυρωθεί ακόμη έναντι πραγματικού certificate — επιστρέφει
    ΟΛΟΥΣ τους υποψήφιους αριθμούς + απόσπασμα κειμένου ώστε να ελεγχθεί
    χειροκίνητα. ``best_guess``: προτιμά ποσό σε γραμμή με λέξη-κλειδί
    (Σύνολο/Εισφορές/Ποσό) — την ΤΕΛΕΥΤΑΙΑ τέτοια εμφάνιση (συνήθως ο
    τελικός/γενικός τίτλος έρχεται στο τέλος)· αλλιώς fallback στο
    ΜΕΓΑΛΥΤΕΡΟ ποσό της σελίδας."""
    import pdfplumber
    all_text = ""
    candidates: List[Dict[str, Any]] = []
    keyword_candidates: List[Dict[str, Any]] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pg in pdf.pages:
            text = pg.extract_text() or ""
            all_text += text + "\n"
            for line in text.split("\n"):
                low = line.lower()
                for a in _AMOUNT_RE.findall(line):
                    val = round(_to_float_amount(a), 2)
                    if val <= 0:
                        continue
                    entry = {"value": val, "line": line.strip()}
                    candidates.append(entry)
                    if any(kw in low for kw in _TOTAL_KEYWORDS):
                        keyword_candidates.append(entry)
    best_guess = None
    if keyword_candidates:
        best_guess = keyword_candidates[-1]["value"]
    elif candidates:
        best_guess = max(c["value"] for c in candidates)
    return {
        "candidates": candidates,
        "keyword_candidates": keyword_candidates,
        "best_guess": best_guess,
        "text_excerpt": all_text[:2000],
    }


def _process_kind(portal: Page, kind: str, out_dir: Path, afm: str,
                  year_filter: Optional[str]) -> Dict[str, Any]:
    label = TAB_LABEL[kind]
    result: Dict[str, Any] = {"ok": False, "years_available": [], "year": None,
                              "pdf": None, "amount": None, "error": None}
    if not _select_tab(portal, label):
        result["error"] = f"Δεν βρέθηκε το tab «{label}»."
        return result
    rows = _read_year_rows(portal)
    result["years_available"] = [r["year"] for r in rows]
    if not rows:
        result["error"] = "Δεν βρέθηκαν γραμμές (έτη) στον πίνακα — πιθανόν καμία βεβαίωση διαθέσιμη."
        return result

    target = None
    if year_filter:
        target = next((r for r in rows if r["year"] == year_filter), None)
        if not target:
            result["error"] = (
                f"Το έτος {year_filter} δεν βρέθηκε — διαθέσιμα: {', '.join(result['years_available'])}"
            )
            return result
    else:
        target = rows[-1]  # πιο πρόσφατο διαθέσιμο (default όταν δεν δόθηκε έτος)

    fetch_res = _fetch_pdf_via_button(portal, target["btn_id"], kind)
    if not fetch_res.get("ok"):
        result["error"] = fetch_res.get("error")
        return result

    pdf_bytes = fetch_res["pdf_bytes"]
    afm_token = _safe_name(afm or "unknown")
    year_token = _safe_name(target["year"])
    dest = out_dir / f"efka_teka_cert_{kind.lower()}_{afm_token}_{year_token}.pdf"
    # Ίδιο (kind, ΑΦΜ, έτος) => refresh (αντικατάσταση), όχι duplicate —
    # ίδιο μάθημα με το kartela_ergodoti.py bug.
    try:
        if dest.exists():
            dest.unlink()
    except Exception:
        pass
    dest.write_bytes(pdf_bytes)

    result["pdf"] = str(dest)
    result["year"] = target["year"]
    try:
        result["amount"] = extract_amount_from_pdf(pdf_bytes)
    except Exception as exc:
        result["amount"] = {"error": f"Αποτυχία εξαγωγής ποσού: {exc}"}
    result["ok"] = True
    return result


def run_extractor(username: str, password: str, afm: str, amka: str,
                  year: Optional[str], pdf_dir: Path, headless: bool) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "ok": False, "afm": afm, "year": year, "pdf_dir": str(pdf_dir),
        "efka": {"ok": False, "error": "not run"},
        "teka": {"ok": False, "error": "not run"},
        "error": None,
    }
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=chromium_launch_args())
        context = browser.new_context(accept_downloads=True, user_agent=DESKTOP_UA, locale="el-GR")
        try:
            portal = _login_efka_non_employee(context, username, password, amka)
            _goto_insurance_royalties(portal)
            summary["efka"] = _process_kind(portal, "EFKA", pdf_dir, afm, year)
            summary["teka"] = _process_kind(portal, "TEKA", pdf_dir, afm, year)
            summary["ok"] = bool(summary["efka"].get("ok") or summary["teka"].get("ok"))
        except Exception as exc:
            logging.exception("efka_teka_certificate run failed: %s", exc)
            summary["error"] = str(exc)
        finally:
            try:
                context.close()
            finally:
                browser.close()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Φορολογικές Βεβαιώσεις ΕΦΚΑ/ΤΕΚΑ (φορολογικής χρήσης) — PDF download + αναγνώριση ποσού."
    )
    parser.add_argument("--username", default=os.getenv("EFKA_CERT_USER"),
                        help="Όνομα χρήστη TAXISnet.")
    parser.add_argument("--password", default=os.getenv("EFKA_CERT_PASS"),
                        help="Κωδικός TAXISnet.")
    parser.add_argument("--afm", default=os.getenv("EFKA_CERT_AFM", ""),
                        help="ΑΦΜ (για naming των PDFs).")
    parser.add_argument("--amka", default=os.getenv("EFKA_CERT_AMKA", ""),
                        help="ΑΜΚΑ (χρειάζεται μόνο αν εμφανιστεί το προαιρετικό radio+ΑΜΚΑ βήμα).")
    parser.add_argument("--year", default=os.getenv("EFKA_CERT_YEAR", ""),
                        help="Έτος (κενό = το πιο πρόσφατο διαθέσιμο στον πίνακα).")
    parser.add_argument("--pdf-dir", default=os.getenv("EFKA_CERT_PDF_DIR"),
                        help="Φάκελος εξόδου. Default: ./efka_teka_cert_pdfs/")
    parser.add_argument("--summary-out", default=os.getenv("EFKA_CERT_SUMMARY_OUT"),
                        help="Αν δοθεί, γράφει JSON summary σε αυτό το αρχείο.")
    parser.add_argument("--headless", action="store_true",
                        help="Run browser in headless mode (default: headed locally).")
    parser.add_argument("--headed", dest="headless", action="store_false",
                        help="Force headed mode.")
    parser.set_defaults(headless=default_headless())
    args = parser.parse_args()

    if not args.username or not args.password:
        print("ERROR: --username και --password είναι υποχρεωτικά (ή θέσε EFKA_CERT_USER/EFKA_CERT_PASS).",
              file=sys.stderr)
        return 2

    pdf_dir = Path(args.pdf_dir).resolve() if args.pdf_dir else (
        Path(__file__).resolve().parent / "efka_teka_cert_pdfs"
    )
    pdf_dir.mkdir(parents=True, exist_ok=True)

    summary = run_extractor(
        username=args.username,
        password=args.password,
        afm=args.afm,
        amka=args.amka,
        year=(args.year.strip() or None),
        pdf_dir=pdf_dir,
        headless=bool(args.headless),
    )

    summary_target = Path(args.summary_out) if args.summary_out else (pdf_dir / "efka_teka_certificate_summary.json")
    try:
        summary_target.parent.mkdir(parents=True, exist_ok=True)
        summary_target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        logging.info("Summary → %s", summary_target)
    except Exception as exc:
        logging.warning("Could not write summary file (%s): %s", summary_target, exc)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
