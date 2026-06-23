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
EFKA_APP_URL = "https://apps.e-efka.gov.gr/eEmployerTransactions/"
EFKA_REPORT_URL = "https://apps.e-efka.gov.gr/eEmployerTransactions/secure/transactionsReport.xhtml"
TEKA_APP_URL = "https://apps.e-efka.gov.gr/eTekaEmployerTransactions/secure/index.xhtml"
TEKA_REPORT_URL = "https://apps.e-efka.gov.gr/eTekaEmployerTransactions/secure/transactionsReport.xhtml?mode=default"

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
    """Open the landing page, click «Είσοδος στην υπηρεσία», fill IKA creds.

    Returns the popup ``Page`` after successful authentication. After login
    the IKA portal redirects the popup directly into the ``eEmployerTransactions``
    app (i.e. the EFKA Καρτέλα Εργοδότη grid is already open on the popup).
    Auth cookies remain attached to the context, so the TEKA app can be
    opened in a fresh page without re-authenticating.
    """
    page = context.new_page()
    page.set_default_timeout(NAV_TIMEOUT)
    logging.info("Opening landing page %s", LANDING_URL)
    page.goto(LANDING_URL, wait_until="domcontentloaded")

    entry_link = page.get_by_role("link", name="Είσοδος στην υπηρεσία").first
    entry_link.wait_for(state="visible", timeout=NAV_TIMEOUT)

    with page.expect_popup(timeout=NAV_TIMEOUT) as popup_info:
        entry_link.click()
    portal = popup_info.value
    portal.set_default_timeout(NAV_TIMEOUT)
    try:
        portal.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
    except PlaywrightTimeoutError:
        logging.info("Popup domcontentloaded not reached — continuing.")

    # The IKA employer login form. Field labels: «Κωδικός Χρήστη:» and
    # «Συνθηματικό:» — NOT TAXISnet.
    user_field = portal.get_by_role("textbox", name="Κωδικός Χρήστη:")
    if user_field.count() == 0:
        user_field = portal.locator(
            "input[name*='user' i], input[id*='user' i], input[name*='username' i]"
        )
    pass_field = portal.get_by_role("textbox", name="Συνθηματικό:")
    if pass_field.count() == 0:
        pass_field = portal.locator("input[type='password']")

    if user_field.count() == 0 or pass_field.count() == 0:
        raise RuntimeError(
            "Δεν εντοπίστηκαν τα πεδία ΙΚΑ login (Κωδικός Χρήστη / Συνθηματικό)."
        )

    user_field.first.fill(username, timeout=NAV_TIMEOUT)
    pass_field.first.fill(password, timeout=NAV_TIMEOUT)

    submit = portal.get_by_role("button", name="Είσοδος")
    if submit.count() == 0:
        submit = portal.locator(
            "button:has-text('Είσοδος'), input[type='submit'][value*='Είσοδος']"
        )

    try:
        with portal.expect_navigation(timeout=NAV_TIMEOUT):
            submit.first.click()
    except PlaywrightTimeoutError:
        submit.first.click()
    try:
        portal.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
    except PlaywrightTimeoutError:
        pass

    # Smoke check: after login the popup should expose the EFKA Καρτέλα grid
    # (heading «Οικονομική Καρτέλα Εργοδότη» + Α.Μ.Ε. field). Either the heading
    # appears, OR an explicit link to the service exists on a hub page.
    landed = False
    for needle in [
        "text=Α.Μ.Ε",            # the employer card grid header
        "text=Επωνυμία",        # company name row on the card grid
        "role=link[name=/Οικονομική Καρτέλα Εργοδότη/]",
        "role=heading[name=/Οικονομική Καρτέλα Εργοδότη/]",
    ]:
        try:
            portal.locator(needle).first.wait_for(state="visible", timeout=8000)
            landed = True
            break
        except PlaywrightTimeoutError:
            continue
    if not landed:
        body_text = (portal.locator("body").inner_text(timeout=5000) or "")[:500]
        raise RuntimeError(
            "Login φαίνεται να απέτυχε — δεν εμφανίστηκαν τα services. Body: "
            + body_text.replace("\n", " ")
        )
    return portal


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

    afm_token = _safe_name(afm or "unknown")
    target = out_dir / f"{prefix}_{afm_token}.pdf"
    c = 1
    while target.exists():
        target = out_dir / f"{prefix}_{afm_token}_{c}.pdf"
        c += 1
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
        context = browser.new_context(accept_downloads=True)
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
