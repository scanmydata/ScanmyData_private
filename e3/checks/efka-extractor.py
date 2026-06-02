import argparse
import json
import logging
import os
import re
from playwright.sync_api import Page, expect, sync_playwright, TimeoutError as PlaywrightTimeoutError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DEFAULT_TIMEOUT = 30000
NETWORK_TIMEOUT = 30000
NAVIGATION_TIMEOUT = 45000  # TAXISNET → IDIKA hop can be slow at peak hours
POPUP_TIMEOUT = 20000
ASSERT_TIMEOUT = 15000
SHORT_WAIT = 500


def clean_text(text: str) -> str:
    value = text.strip().replace("\n", " ")
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    return re.sub(r"\s+", " ", value).strip()


def collapse_adjacent_duplicates(values: list[str]) -> list[str]:
    collapsed = []
    for value in values:
        if not collapsed or value != collapsed[-1]:
            collapsed.append(value)
    return collapsed


def _safe_filename(token: str, fallback: str = "row") -> str:
    """Make a filesystem-safe filename component."""
    token = (token or "").strip()
    token = re.sub(r"[^\w\-Ͱ-Ͽἀ-῿.]+", "_", token, flags=re.U)
    return token or fallback


# Hard caps so a stuck row never blocks the whole brain run.
# - PDF_PER_ROW_TIMEOUT: per-click wait for the download dialog. Most
#   IDIKA downloads start in < 2s; a row that does not produce a
#   download in 8s is treated as failed (the row is skipped, not
#   retried — the table data is already harvested by extract_table_data).
# - PDF_TOTAL_TIMEOUT: absolute deadline for the whole download phase.
#   We *always* respect this, even at the cost of skipping rows, so the
#   amount/scrape step that already finished can still be reported.
# - PDF_MAX_CONSEC_FAILS: bail out after this many consecutive failures
#   so we do not waste timeouts on a broken grid state.
PDF_PER_ROW_TIMEOUT = 8000
PDF_TOTAL_TIMEOUT_SEC = 60.0
PDF_MAX_CONSEC_FAILS = 3
PDF_DEFAULT_MAX_ROWS = 60


def download_certificate_pdfs(page: Page, output_dir, max_rows: int = None,
                              prefix: str = "efka_cert") -> list:
    """Best-effort per-row PDF download. NEVER fails the calling script.

    Returns a list of dicts ``{"row": idx, "path": str, "label": str}``.
    Skips rows that misbehave, enforces a total deadline, and walks away
    if too many consecutive rows time out. The table scrape that produced
    the comparison amount has already happened before this function runs,
    so the brain run can still report the EFKA/TEKA amount even when no
    PDF is saved.
    """
    import time
    from pathlib import Path as _Path
    out_dir = _Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        page.wait_for_selector(
            "#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable",
            timeout=NETWORK_TIMEOUT,
        )
    except Exception as exc:
        logging.warning("PDF download skipped — table did not load (%s)", exc)
        return []

    rows = page.locator(
        "#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable tr[class*='dxgvDataRow']"
    )
    total = rows.count()
    cap = max_rows if max_rows is not None else PDF_DEFAULT_MAX_ROWS
    total = min(total, cap)
    logging.info("PDF download: targeting %d certificate rows (cap=%d)", total, cap)

    saved = []
    consecutive_fails = 0
    deadline = time.monotonic() + PDF_TOTAL_TIMEOUT_SEC
    for i in range(total):
        if time.monotonic() > deadline:
            logging.warning(
                "PDF download deadline reached after %d/%d rows; stopping cleanly",
                i, total,
            )
            break
        row = rows.nth(i)
        cells = row.locator("td")
        label_parts = []
        try:
            ncells = min(cells.count(), 6)
        except Exception:
            ncells = 0
        for c in range(ncells):
            try:
                txt = clean_text(cells.nth(c).inner_text(timeout=2000))
            except Exception:
                txt = ""
            if txt and txt not in label_parts:
                label_parts.append(txt[:30])
        label = " | ".join(label_parts) if label_parts else f"row{i}"
        safe_label = _safe_filename(label.replace(" | ", "_"), f"row{i}")[:80]
        out_path = out_dir / f"{prefix}_{i:02d}_{safe_label}.pdf"

        try:
            print_button = row.locator("a:has(img[title*='Εκτύπωση'])").first
            if not print_button.count():
                logging.info("Row %d: no print button found — skip", i)
                consecutive_fails += 1
                if consecutive_fails >= PDF_MAX_CONSEC_FAILS:
                    logging.warning("PDF download: too many consecutive misses, stopping")
                    break
                continue
            with page.expect_download(timeout=PDF_PER_ROW_TIMEOUT) as dl_info:
                print_button.click(timeout=PDF_PER_ROW_TIMEOUT)
            dl = dl_info.value
            dl.save_as(str(out_path))
            saved.append({"row": i, "path": str(out_path), "label": label})
            consecutive_fails = 0
            logging.info("Row %d saved: %s", i, out_path.name)
        except PlaywrightTimeoutError:
            consecutive_fails += 1
            logging.info("Row %d: print did not produce a download in %dms — skip", i, PDF_PER_ROW_TIMEOUT)
        except Exception as exc:
            consecutive_fails += 1
            logging.info("Row %d: error during download (%s) — skip", i, exc)
        if consecutive_fails >= PDF_MAX_CONSEC_FAILS:
            logging.warning("PDF download: %d consecutive failures, stopping early", consecutive_fails)
            break
    logging.info("PDF download done: %d/%d saved", len(saved), total)
    return saved


def extract_table_data(page: Page) -> dict:
    page.wait_for_selector("#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable", timeout=NETWORK_TIMEOUT)
    header_cells = page.locator("#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable tr[id*='DXHeadersRow'] td")
    headers = []
    for i in range(header_cells.count()):
        header_text = clean_text(header_cells.nth(i).inner_text())
        if header_text:
            headers.append(header_text)

    headers = collapse_adjacent_duplicates(headers)
    if not headers:
        header_cells = page.locator("#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable tr td")
        for i in range(header_cells.count()):
            header_text = clean_text(header_cells.nth(i).inner_text())
            if header_text:
                headers.append(header_text)
        headers = collapse_adjacent_duplicates(headers)

    header_map = {}
    for index, header in enumerate(headers):
        header_map.setdefault(header, []).append(index)

    row_locator = page.locator("#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable tr[class*='dxgvDataRow']")
    rows = []
    for row_index in range(row_locator.count()):
        row = row_locator.nth(row_index)
        cells = row.locator("td")
        values = [clean_text(cells.nth(j).inner_text()) for j in range(cells.count())]

        # DevExpress grids almost always render an EXTRA trailing empty
        # cell for adaptive (mobile) expansion. Strip such empties from
        # the tail so headers and values align. We keep stripping while
        # the tail is empty AND we still have more values than headers.
        if headers and len(values) > len(headers):
            while values and len(values) > len(headers) and not values[-1].strip():
                values = values[:-1]

        if headers and len(values) == len(headers):
            row_data = {headers[j]: values[j] for j in range(len(headers))}
        elif headers and len(values) > len(headers):
            # We have non-empty extras — keep headers-mapped fields AND
            # preserve the extras under col_X for debugging.
            row_data = {headers[j]: values[j] for j in range(len(headers))}
            for j in range(len(headers), len(values)):
                row_data[f"col_{j}"] = values[j]
        else:
            row_data = {f"col_{j}": values[j] for j in range(len(values))}

        rows.append(row_data)

    return {
        "headers": headers,
        "header_map": header_map,
        "rows": rows,
    }


def test_example(page: Page, username: str, password: str, amka: str) -> None:
    e_efka_url = "https://www.e-efka.gov.gr/el/elektronikes-yperesies/bebaiose-eisphoron-gia-phorologike-chrese"
    idika_url = "https://www.idika.org.gr/EfkaServices/Application/EfkaCertificates.aspx"

    page.goto(e_efka_url, timeout=DEFAULT_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)

    if "blocked" in page.title().lower() or "web page blocked" in page.content().lower():
        logging.warning("Initial e-EFKA page appears blocked; trying direct IDIKA URL.")
        page.goto(idika_url, timeout=DEFAULT_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)

    link = page.get_by_role("link", name="Βεβαιώσεις Εισφορών e-ΕΦΚΑ")
    if link.count() == 0:
        link = page.locator("a", has_text="Βεβαιώσεις Εισφορών e-ΕΦΚΑ")
    if link.count() == 0:
        link = page.locator("a", has_text="Βεβαιώσεις Εισφορών")

    if link.count() == 0:
        raise RuntimeError("Could not find the EFKA certificates link on the initial page.")

    with page.expect_popup(timeout=POPUP_TIMEOUT) as page1_info:
        link.first.scroll_into_view_if_needed()
        link.first.click()
    page1 = page1_info.value
    page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)

    try:
        taxisnet_button = page1.get_by_role("button", name="Συνέχεια στο TAXISNET")
        with page1.expect_navigation(timeout=NAVIGATION_TIMEOUT):
            taxisnet_button.click()
    except PlaywrightTimeoutError:
        logging.info("TAXISNET button not found by role; trying text fallback.")
        try:
            taxisnet_button = page1.get_by_text("Συνέχεια στο TAXISNET").first
            with page1.expect_navigation(timeout=NAVIGATION_TIMEOUT):
                taxisnet_button.click()
        except PlaywrightTimeoutError:
            raise RuntimeError("Could not find or navigate from the TAXISNET login button on the page.")

    page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)

    # The TAXISNET portal has shifted labels a few times — "Χρήστης",
    # "Όνομα Χρήστη", placeholder "Username", input name="j_username".
    # Try the canonical role-based locator first and fall back to robust
    # selectors. Same trick for the password.
    username_field = page1.get_by_role("textbox", name="Χρήστης:")
    if username_field.count() == 0:
        username_field = page1.get_by_role("textbox", name="Όνομα Χρήστη")
    if username_field.count() == 0:
        username_field = page1.locator(
            "input[name='j_username'], input[id*='j_username'], input[id*='username' i], input[name*='user' i]"
        )
    password_field = page1.get_by_role("textbox", name="Κωδικός:")
    if password_field.count() == 0:
        password_field = page1.locator("input[type='password']")
    if username_field.count() and password_field.count():
        username_field.first.fill(username, timeout=NETWORK_TIMEOUT)
        password_field.first.fill(password, timeout=NETWORK_TIMEOUT)
        login_button = page1.locator(
            "#btn-login-submit, button:has-text('Σύνδεση'), button:has-text('Είσοδος'), input[type='submit']"
        )
        if login_button.count() == 0:
            login_button = page1.get_by_role("button", name="Σύνδεση")
        try:
            with page1.expect_navigation(timeout=NAVIGATION_TIMEOUT):
                login_button.first.click()
        except PlaywrightTimeoutError:
            try:
                login_button.first.click()
            except Exception:
                pass
        try:
            page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
        except PlaywrightTimeoutError:
            # IDIKA pages keep keepalive sockets open and never reach
            # "networkidle". Don't fail the whole script — proceed.
            logging.info("Networkidle never reached after login; continuing anyway.")
    else:
        afm_field = page1.get_by_role("textbox", name="ΑΦΜ:")
        if afm_field.count() == 0:
            afm_field = page1.locator("input[name*='afm'], input[id*='afm']")
        amka_login_field = page1.get_by_role("textbox", name="ΑΜΚΑ:")
        if amka_login_field.count() == 0:
            amka_login_field = page1.locator("input[name*='amka'], input[id*='amka']")
        if afm_field.count() == 0 or amka_login_field.count() == 0:
            raise RuntimeError("Could not find AFM/AMKA login fields on the TAXISNET page.")
        afm_field.first.fill(username)
        amka_login_field.first.fill(amka)
        login_button = page1.locator("#j_idt38, button:has-text('Είσοδος'), input[type='submit'][value*='Είσοδος']").first
        if login_button.count() == 0:
            raise RuntimeError("Could not find the TAXISNET Είσοδος button.")
        try:
            with page1.expect_navigation(timeout=NAVIGATION_TIMEOUT):
                login_button.click()
        except PlaywrightTimeoutError:
            login_button.click()
        page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)

    if page1.locator("input[type='radio']").count() > 0 or page1.get_by_role("button", name="Αποστολή").count() > 0:
        page1.wait_for_selector("input[type='radio']", timeout=NETWORK_TIMEOUT)
        radio_buttons = page1.locator("input[type='radio']")
        if radio_buttons.count() > 1:
            radio_buttons.nth(1).check()
        else:
            logging.info("No radio inputs found; trying role-based radio locator.")
            radios = page1.get_by_role("radio")
            if radios.count() > 1:
                radios.nth(1).check()
            else:
                raise RuntimeError("Could not find a second radio option on the TAXISNET page.")

        submit_button = page1.get_by_role("button", name="Αποστολή")
        if submit_button.count() == 0:
            submit_button = page1.locator("input[type='submit'][value*='Αποστολή'], button:has-text('Αποστολή')").first
        submit_button.click()
        page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)

        amka_field = page1.get_by_role("textbox", name="ΑΜΚΑ:")
        if amka_field.count() == 0:
            amka_field = page1.locator("input[name*='AMKA'], input[id*='AMKA']")
        if amka_field.count() == 0:
            raise RuntimeError("Could not find the AMKA input field on the TAXISNET auth page.")
        amka_field.first.fill(amka, timeout=NETWORK_TIMEOUT)
        enter_button = page1.get_by_role("button", name="Είσοδος")
        if enter_button.count() == 0:
            enter_button = page1.locator("input[type='submit'][value*='Εισοδος'], button:has-text('Είσοδος')").first
        if enter_button.count() == 0:
            raise RuntimeError("Could not find the Enter button after AMKA.")
        try:
            with page1.expect_navigation(timeout=NAVIGATION_TIMEOUT):
                enter_button.click()
        except PlaywrightTimeoutError:
            enter_button.click()
        page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)

    expect(page1.get_by_text("Βεβαιώσεις για φορολογική χρήση Φορολογικές Βεβαιώσεις e")).to_be_visible(timeout=ASSERT_TIMEOUT)
    page1.wait_for_timeout(SHORT_WAIT)

    logging.info("Clicking on the section to scrape the table.")
    section_locator = page1.locator("section div", has_text="Φορολογικές Βεβαιώσεις e")
    section_count = section_locator.count()
    if section_count > 0:
        # Prefer .nth(4) (matches the historical IDIKA layout) but fall
        # back to lower indices when the page only renders fewer matches
        # (e.g. user has no EFKA enrolment but a generic landing page is
        # shown).
        section_button = None
        for idx in [4, 3, 2, 1, 0]:
            if idx < section_count:
                cand = section_locator.nth(idx)
                try:
                    cand.wait_for(state="visible", timeout=5000)
                    section_button = cand
                    break
                except PlaywrightTimeoutError:
                    continue
        if section_button is not None:
            section_button.click()
            try:
                page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
            except PlaywrightTimeoutError:
                logging.info("Networkidle never reached after EFKA section click; continuing anyway.")

    try:
        page1.wait_for_selector(
            "#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable", timeout=NETWORK_TIMEOUT
        )
    except PlaywrightTimeoutError:
        logging.info("EFKA table did not appear — emitting empty result.")
        with open("extracted_table_data.json", "w", encoding="utf-8") as output_file:
            json.dump({"headers": [], "header_map": {}, "rows": [], "no_efka": True},
                      output_file, ensure_ascii=False, indent=4)
        return

    output = extract_table_data(page1)
    with open("extracted_table_data.json", "w", encoding="utf-8") as output_file:
        json.dump(output, output_file, ensure_ascii=False, indent=4)

    logging.info("Saved extracted_table_data.json with headers and row mappings.")

    # Optional: download per-row PDF certificates when EFKA_PDF_DIR is set
    pdf_dir = os.getenv("EFKA_PDF_DIR")
    if pdf_dir:
        try:
            saved = download_certificate_pdfs(page1, pdf_dir, prefix="efka_cert")
            output["pdfs"] = saved
            with open("extracted_table_data.json", "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=4)
            logging.info("Downloaded %d PDFs to %s", len(saved), pdf_dir)
        except Exception as exc:
            logging.warning("PDF download skipped: %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="EFKA extractor for table data.")
    parser.add_argument("--username", default=os.getenv("EFKA_USER", "159712098"), help="TAXISNET username")
    parser.add_argument("--password", default=os.getenv("EFKA_PASSWORD", "159712"), help="TAXISNET password")
    parser.add_argument("--amka", default=os.getenv("EFKA_AMKA", "12019400675"), help="AMKA to fill")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--pdf-dir", default=os.getenv("EFKA_PDF_DIR"),
                        help="If set, download each certificate PDF into this directory")
    args = parser.parse_args()
    if args.pdf_dir:
        os.environ["EFKA_PDF_DIR"] = args.pdf_dir

    logging.info("Launching browser in %s mode.", "headless" if args.headless else "headed")
    try:
        from e3.checks import chromium_launch_args as _svfb_args
    except Exception:
        def _svfb_args():
            return [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
            ]
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=args.headless,
            args=_svfb_args(),
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="el-GR",
            viewport={"width": 1920, "height": 1200},
            accept_downloads=True,
        )
        page = context.new_page()
        try:
            test_example(page, args.username, args.password, args.amka)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
