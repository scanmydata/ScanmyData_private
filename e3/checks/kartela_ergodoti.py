"""Dynamic extractor for «Οικονομική Καρτέλα Εργοδότη» (e-EFKA + TEKA).

Unlike efka-extractor.py / teka-extractor.py (which use TAXISnet credentials
for the *insured-person* services), this script logs into e-EFKA with the
**employer-side IKA credentials** (``Κωδικός Χρήστη`` + ``Συνθηματικό``)
issued by IKA when the company was registered as an employer. Two separate
services are scraped from the same session:

* ``Οικονομική Καρτέλα Εργοδότη`` (https://apps.e-efka.gov.gr/eEmployerTransactions/)
* ``Οικονομική Καρτέλα Εργοδότη ΤΕΚΑ`` (https://apps.e-efka.gov.gr/eTekaEmployerTransactions/secure/index.xhtml)

Both pages expose an «Εκτύπωση» button that triggers a browser download
(the server-side PDF generation). We click each in turn inside
``page.expect_download()`` blocks and save the resulting files to disk so
the brain orchestrator can later confirm the PDFs landed where expected.

CLI:

    python kartela_ergodoti.py --username ika000XXX --password XXX \\
        [--afm 123456789] [--date-from 01/01/2025] \\
        [--pdf-dir /path/to/output] [--headless]

When ``--pdf-dir`` is omitted the PDFs land in ``./ergodoti_pdfs/`` next
to the script. The script *always* exits 0 if the login + navigation
succeeded; a non-zero exit means the IKA credentials were rejected or the
service is unreachable.

A JSON summary is written next to the PDFs (``kartela_ergodoti_summary.json``)
so the caller can verify which downloads actually completed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

from playwright.sync_api import (
    Page,
    BrowserContext,
    Download,
    expect,
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

LANDING_URL = "https://www.e-efka.gov.gr/el/elektronikes-yperesies/oikonomike-kartela-ergodote"
# e-Access ενοποιημένο login. Οι εργοδότες συνδέονται με «κωδικούς ΕΦΚΑ/ΚΕΑΟ»
# (Κωδικός Χρήστη + Συνθηματικό) — ΟΧΙ μέσω TAXISnet. Η παλιά ροή (landing page →
# «Είσοδος στην υπηρεσία» → popup) σπάει πλέον: ο σύνδεσμος πάει κατευθείαν στην
# app και το e-Access επιστρέφει secureError «Δεν έχετε δικαίωμα πρόσβασης».
EACCESS_LOGIN_URL = "https://apps.e-efka.gov.gr/eAccess/login.xhtml"
EFKA_APP_URL = "https://apps.e-efka.gov.gr/eEmployerTransactions/"
EFKA_REPORT_URL = "https://apps.e-efka.gov.gr/eEmployerTransactions/secure/transactionsReport.xhtml"
TEKA_APP_URL = "https://apps.e-efka.gov.gr/eTekaEmployerTransactions/secure/index.xhtml"
TEKA_REPORT_URL = "https://apps.e-efka.gov.gr/eTekaEmployerTransactions/secure/transactionsReport.xhtml?mode=default"

# Realistic desktop User-Agent. Χωρίς αυτό, το e-EFKA gov site φορτώνει
# διαφορετικά για το default «HeadlessChrome» UA (bot detection) και ο σύνδεσμος
# «Είσοδος στην υπηρεσία» δεν γίνεται ποτέ visible σε headless → timeout.
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

NAV_TIMEOUT = 45000
SHORT_WAIT = 800
# PrimeFaces «Εκτύπωση» fires a server-side PDF build that can take ~50s
# at peak hours (we observed ~45s on the test account). Give it plenty
# of room — the report will not stream until the build finishes.
DOWNLOAD_TIMEOUT = 120000


def _safe_name(token: str, fallback: str = "row") -> str:
    token = (token or "").strip()
    token = re.sub(r"[^\w\-Ͱ-Ͽἀ-῿.]+", "_", token, flags=re.U)
    return token or fallback


def _save_download(dl: Download, out_dir: Path, prefix: str, afm: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    suggested = dl.suggested_filename or "kartela.pdf"
    suffix = Path(suggested).suffix or ".pdf"
    afm_token = _safe_name(afm or "unknown")
    fname = f"{prefix}_{afm_token}{suffix}"
    target = out_dir / fname
    # If the same prefix has already saved a file, suffix with a counter.
    counter = 1
    while target.exists():
        target = out_dir / f"{prefix}_{afm_token}_{counter}{suffix}"
        counter += 1
    dl.save_as(str(target))
    logging.info("Saved download → %s (%d bytes)", target, target.stat().st_size)
    return target


def _login_employer(context: BrowserContext, username: str, password: str) -> Page:
    """Log into e-EFKA e-Access with **employer** credentials (κωδικοί ΕΦΚΑ/ΚΕΑΟ).

    Navigates straight to the e-Access login page, fills «Κωδικός Χρήστη» +
    «Συνθηματικό» and presses «Είσοδος» (NOT «Συνέχεια στο TAXISNET»). After a
    successful login the session cookies stay on the context, so the EFKA/TEKA
    employer-card report pages open in fresh pages without re-authenticating.
    Returns the logged-in ``Page``.
    """
    page = context.new_page()
    page.set_default_timeout(NAV_TIMEOUT)
    logging.info("Opening e-Access login %s", EACCESS_LOGIN_URL)
    page.goto(EACCESS_LOGIN_URL, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        pass

    # Employer login form (JSF): #j_username / #j_password, submit button «Είσοδος».
    user_field = page.locator("#j_username")
    if user_field.count() == 0:
        user_field = page.get_by_role("textbox", name="Κωδικός Χρήστη:")
    pass_field = page.locator("#j_password")
    if pass_field.count() == 0:
        pass_field = page.locator("input[type='password']")

    if user_field.count() == 0 or pass_field.count() == 0:
        body_text = (page.locator("body").inner_text(timeout=5000) or "")[:300]
        raise RuntimeError(
            "Δεν εντοπίστηκαν τα πεδία login ΕΦΚΑ/ΚΕΑΟ (Κωδικός Χρήστη / Συνθηματικό). "
            "Body: " + body_text.replace("\n", " ")
        )

    user_field.first.fill(username, timeout=NAV_TIMEOUT)
    pass_field.first.fill(password, timeout=NAV_TIMEOUT)

    # «Είσοδος» = employer submit. ΠΡΟΣΟΧΗ: να ΜΗΝ πατηθεί «Συνέχεια στο TAXISNET».
    submit = page.get_by_role("button", name="Είσοδος", exact=True)
    if submit.count() == 0:
        submit = page.locator("button:has-text('Είσοδος'):not(:has-text('TAXIS'))")
    try:
        with page.expect_navigation(timeout=NAV_TIMEOUT):
            submit.first.click()
    except PlaywrightTimeoutError:
        submit.first.click()
    try:
        page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT)
    except PlaywrightTimeoutError:
        pass

    # Login failure detection: still on the login page or an explicit error.
    cur = page.url or ""
    body_text = (page.locator("body").inner_text(timeout=5000) or "")
    low = body_text.lower()
    login_failed = (
        "login.xhtml" in cur
        or "j_security_check" in cur
        or "σφάλμα εισόδου" in low
        or "δεν είναι έγκυρα" in low
        or "λάθος" in low
        or "authentication_error" in cur.lower()
    )
    if login_failed:
        raise RuntimeError(
            "Αποτυχία σύνδεσης — ελέγξτε τους κωδικούς ΕΦΚΑ/ΚΕΑΟ (Εργοδότη). "
            "Τα στοιχεία που εισάγατε δεν είναι έγκυρα."
        )

    # Establish the employer-app session (and confirm access — no secureError).
    try:
        page.goto(EFKA_APP_URL, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeoutError:
            pass
    except Exception as exc:
        raise RuntimeError(f"Δεν άνοιξε η εφαρμογή Καρτέλας Εργοδότη: {exc}")

    if "secureError" in (page.url or ""):
        body_text = (page.locator("body").inner_text(timeout=5000) or "")[:300]
        raise RuntimeError(
            "Το e-Access απέρριψε την πρόσβαση στην Καρτέλα Εργοδότη (secureError). "
            "Πιθανόν λάθος τύπος κωδικών ή η υπηρεσία είναι προσωρινά μη διαθέσιμη. "
            + body_text.replace("\n", " ")
        )
    return page


def _extract_year(date_from: str) -> Optional[str]:
    """The «Εκτύπωση» report page expects a *year* (yyyy) not a date range."""
    if not date_from:
        return None
    m = re.search(r"(20\d{2})", date_from)
    return m.group(1) if m else None


def _download_report_pdf(page: Page, report_url: str, year: Optional[str],
                          out_dir: Path, prefix: str, afm: str,
                          label: str) -> dict:
    """Navigate to the PrimeFaces ``transactionsReport.xhtml`` view and click
    its «Εκτύπωση» button. The button does a form POST with ``target='_blank'``
    so the PDF is served into a new tab. Chromium typically auto-downloads
    ``application/pdf`` responses; we capture that download. If the PDF is
    rendered inline instead, we fall back to fetching the new tab's URL
    via the context's request handle and writing it ourselves.
    """
    logging.info("[%s] Navigating to report URL %s", label, report_url)
    try:
        page.goto(report_url, wait_until="domcontentloaded")
    except Exception as exc:
        return {"ok": False, "pdf": None, "error": f"{label}: navigation failed: {exc}"}

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        pass

    # The year selector is a PrimeFaces ``SelectOneMenu``: the visible
    # element is a div/span wrapper, the underlying form value lives in a
    # hidden ``<select id='year_input' name='year_input'>``. Setting it
    # via ``select_option`` updates the form value reliably even though
    # the native select is not visible.
    if year:
        try:
            # The year selector is a PrimeFaces ``SelectOneMenu`` whose
            # underlying ``<select>`` is ``aria-hidden`` (Playwright refuses
            # to ``select_option`` on it). PrimeFaces composite IDs differ
            # per page (``year_input`` on EFKA, ``transactionsReportForm:year_input``
            # on TEKA), so we match by name *suffix*. The set+dispatch dance
            # keeps the JSF lifecycle happy.
            changed = page.evaluate(
                """(yr) => {
                    const sel = document.querySelector(
                        "select[id$='year_input'], select[name$='year_input']"
                    );
                    if (!sel) return false;
                    const opt = Array.from(sel.options).find(o => o.value === String(yr));
                    if (!opt) return false;
                    sel.value = opt.value;
                    sel.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }""",
                str(year),
            )
            if changed:
                logging.info("[%s] Year set to %s.", label, year)
            else:
                logging.info("[%s] Year %s not found in dropdown — using default.",
                             label, year)
        except Exception as exc:
            logging.info("[%s] Could not set year (%s) — using default.", label, exc)

    # The original click-based flow opens the PDF in a new "_blank" tab and
    # Chromium displays it inline (built-in viewer), so no Playwright
    # ``download`` event fires and the response is hard to retrieve through
    # listeners. Instead we POST the same form ourselves via the page's
    # ``fetch`` (cookies + ViewState all come along automatically) and
    # receive the PDF bytes directly. Bypasses popups entirely.
    #
    # Returns ``{ok, status, ct, b64?, text?}``.
    try:
        result = page.evaluate(
            """async () => {
                // The «Εκτύπωση» submit can be inside different forms
                // depending on the page (EFKA: transactionsForm, TEKA:
                // transactionsReportForm). Locate the button and use its
                // enclosing form so we POST to whichever endpoint matches.
                const btn = document.querySelector(
                    "button[type='submit'][title='Εκτύπωση']"
                );
                if (!btn) return {ok: false, status: 0, ct: '',
                                  text: 'print button (title=Εκτύπωση) not found'};
                const form = btn.closest('form');
                if (!form) return {ok: false, status: 0, ct: '',
                                   text: 'enclosing <form> for print button not found'};
                const fd = new FormData(form);
                // PrimeFaces decides which action to fire from the button's
                // name being in the form data — explicitly add it.
                if (btn.name) fd.append(btn.name, btn.value || '');
                form.target = '_self';
                const body = new URLSearchParams();
                for (const [k, v] of fd.entries()) {
                    if (typeof v === 'string') body.append(k, v);
                }
                const resp = await fetch(form.action, {
                    method: 'POST',
                    body: body,
                    credentials: 'include',
                });
                const ct = resp.headers.get('content-type') || '';
                if (!resp.ok || !/pdf|octet-stream/i.test(ct)) {
                    return {ok: false, status: resp.status, ct,
                            text: (await resp.text()).slice(0, 400)};
                }
                const buf = await resp.arrayBuffer();
                const u8 = new Uint8Array(buf);
                let bin = '';
                const chunk = 0x8000;
                for (let i = 0; i < u8.length; i += chunk) {
                    bin += String.fromCharCode.apply(null, u8.subarray(i, i + chunk));
                }
                return {ok: true, status: resp.status, ct, b64: btoa(bin)};
            }"""
        )
    except Exception as exc:
        return {"ok": False, "pdf": None,
                "error": f"{label}: fetch() στη φόρμα απέτυχε: {exc}"}

    if not result or not result.get("ok"):
        return {"ok": False, "pdf": None,
                "error": f"{label}: το server response δεν ήταν PDF "
                         f"(status={result.get('status')!r}, ct={result.get('ct')!r}, "
                         f"text={result.get('text','')[:200]!r})"}

    import base64 as _b64
    try:
        body_bytes = _b64.b64decode(result["b64"])
    except Exception as exc:
        return {"ok": False, "pdf": None,
                "error": f"{label}: decode του PDF body απέτυχε: {exc}"}

    if not body_bytes or body_bytes[:4] != b"%PDF":
        return {"ok": False, "pdf": None,
                "error": f"{label}: το payload δεν ξεκινάει με %PDF "
                         f"({len(body_bytes)} bytes, first={body_bytes[:8]!r})"}

    # Filename ΠΕΡΙΛΑΜΒΑΝΕΙ το έτος αναφοράς. Χωρίς αυτό, κάθε ξανα-τρέξιμο (ίδιου
    # ή διαφορετικού έτους) έφτιαχνε ένα ΝΕΟ αρχείο με αύξοντα «_1», «_2» επίθημα
    # — δηλαδή η ΙΔΙΑ καρτέλα αποθηκευόταν πολλαπλές φορές αντί να ανανεώνεται.
    # Τώρα: ίδιο (kind, ΑΦΜ, έτος) => το νέο PDF ΑΝΤΙΚΑΘΙΣΤΑ το παλιό (fresh refresh,
    # ίδιο path -> write_bytes το αντικαθιστά αυτόματα). Διαφορετικά έτη ΔΕΝ
    # πειράζονται μεταξύ τους — ο σκοπός του έτους στο filename είναι να
    # συνυπάρχουν καρτέλες πολλών ετών χωρίς να συγκρούονται.
    afm_token = _safe_name(afm or "unknown")
    year_token = _safe_name(year or "unknown")
    target = out_dir / f"{prefix}_{afm_token}_{year_token}.pdf"

    # Καθάρισε ΜΟΝΟ τα «ορφανά» αρχεία του ΠΑΛΙΟΥ bug (πριν μπει το έτος στο
    # filename): είτε χωρίς κανένα suffix, είτε με τον παλιό αριθμητικό μετρητή
    # «_1», «_2», ... (1-3 ψηφία). Ένα 4ψήφιο suffix (π.χ. «_2024») θεωρείται
    # ΠΑΝΤΑ έγκυρο έτος και ΔΕΝ αγγίζεται — έτσι καρτέλες πολλών ετών συνυπάρχουν.
    try:
        old_plain = out_dir / f"{prefix}_{afm_token}.pdf"
        if old_plain.exists() and old_plain != target:
            old_plain.unlink()
        for stale in out_dir.glob(f"{prefix}_{afm_token}_[0-9]*.pdf"):
            if stale == target:
                continue
            suffix = stale.stem.rsplit("_", 1)[-1]
            if re.fullmatch(r"\d{1,3}", suffix):  # old counter, never a year
                stale.unlink()
    except Exception:
        pass

    target.write_bytes(body_bytes)
    logging.info("[%s] Saved PDF → %s (%d bytes)", label, target, len(body_bytes))
    return {"ok": True, "pdf": str(target), "error": None}


def _open_efka_kartela(context: BrowserContext, portal: Page,
                       date_from: str, out_dir: Path, afm: str) -> dict:
    """Download the EFKA Κίνηση Εργοδότη report PDF."""
    page = context.new_page()
    page.set_default_timeout(NAV_TIMEOUT)
    try:
        return _download_report_pdf(
            page, EFKA_REPORT_URL, _extract_year(date_from),
            out_dir, "kartela_ergodoti_efka", afm, "EFKA")
    finally:
        try:
            page.close()
        except Exception:
            pass


def _open_teka_kartela(context: BrowserContext, portal: Page,
                       date_from: str, out_dir: Path, afm: str) -> dict:
    """Download the TEKA Κίνηση Εργοδότη report PDF."""
    page = context.new_page()
    page.set_default_timeout(NAV_TIMEOUT)
    try:
        return _download_report_pdf(
            page, TEKA_REPORT_URL, _extract_year(date_from),
            out_dir, "kartela_ergodoti_teka", afm, "TEKA")
    finally:
        try:
            page.close()
        except Exception:
            pass


def run_extractor(username: str, password: str, afm: str, date_from: str,
                  pdf_dir: Path, headless: bool) -> dict:
    summary = {
        "ok": False,
        "afm": afm,
        "date_from": date_from,
        "pdf_dir": str(pdf_dir),
        "efka": {"ok": False, "pdf": None, "error": "not run"},
        "teka": {"ok": False, "pdf": None, "error": "not run"},
        "error": None,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=chromium_launch_args())
        context = browser.new_context(accept_downloads=True, user_agent=DESKTOP_UA,
                                      locale="el-GR")
        try:
            portal = _login_employer(context, username, password)
            summary["efka"] = _open_efka_kartela(context, portal, date_from, pdf_dir, afm)
            summary["teka"] = _open_teka_kartela(context, portal, date_from, pdf_dir, afm)
            summary["ok"] = bool(summary["efka"]["ok"] or summary["teka"]["ok"])
            # Best-effort logout
            try:
                logout = portal.get_by_role("link", name=re.compile(r"Αποσύνδεση"))
                if logout.count():
                    logout.first.click()
            except Exception:
                pass
        except Exception as exc:
            logging.exception("kartela_ergodoti run failed: %s", exc)
            summary["error"] = str(exc)
        finally:
            try:
                context.close()
            finally:
                browser.close()

    # Verify on-disk presence — this is the «επιβεβαίωση λήψης» the user asked for.
    for key in ("efka", "teka"):
        info = summary[key]
        path_str = info.get("pdf")
        if path_str:
            p = Path(path_str)
            info["exists"] = p.exists()
            info["size"] = p.stat().st_size if p.exists() else 0
        else:
            info["exists"] = False
            info["size"] = 0

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Εξαγωγή Οικονομικής Καρτέλας Εργοδότη (EFKA + TEKA) με PDF download verification."
    )
    parser.add_argument("--username", default=os.getenv("IKA_EMP_USER"),
                        help="Όνομα χρήστη (Εργοδότη) Ι.Κ.Α.")
    parser.add_argument("--password", default=os.getenv("IKA_EMP_PASS"),
                        help="Συνθηματικό (Εργοδότη) Ι.Κ.Α.")
    parser.add_argument("--afm", default=os.getenv("IKA_EMP_AFM", ""),
                        help="ΑΦΜ εργοδότη (μόνο για το naming των PDFs).")
    parser.add_argument("--date-from", default=os.getenv("IKA_EMP_DATE_FROM", "01/01/2025"),
                        help="Ημερομηνία 'Από' για το grid (dd/mm/yyyy).")
    parser.add_argument("--pdf-dir", default=os.getenv("IKA_EMP_PDF_DIR"),
                        help="Φάκελος εξόδου για τα PDFs. Default: ./ergodoti_pdfs/")
    parser.add_argument("--summary-out", default=os.getenv("IKA_EMP_SUMMARY_OUT"),
                        help="Αν δοθεί, γράφει JSON summary σε αυτό το αρχείο.")
    parser.add_argument("--headless", action="store_true",
                        help="Run browser in headless mode (default: headed locally).")
    parser.add_argument("--headed", dest="headless", action="store_false",
                        help="Force headed mode.")
    parser.set_defaults(headless=default_headless())
    args = parser.parse_args()

    if not args.username or not args.password:
        print("ERROR: --username και --password είναι υποχρεωτικά (ή θέσε IKA_EMP_USER/IKA_EMP_PASS).",
              file=sys.stderr)
        return 2

    pdf_dir = Path(args.pdf_dir).resolve() if args.pdf_dir else (
        Path(__file__).resolve().parent / "ergodoti_pdfs"
    )
    pdf_dir.mkdir(parents=True, exist_ok=True)

    summary = run_extractor(
        username=args.username,
        password=args.password,
        afm=args.afm,
        date_from=args.date_from,
        pdf_dir=pdf_dir,
        headless=bool(args.headless),
    )

    summary_target = Path(args.summary_out) if args.summary_out else (pdf_dir / "kartela_ergodoti_summary.json")
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
