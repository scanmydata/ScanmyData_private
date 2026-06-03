import argparse
import json
import logging
import os
import re
from playwright.sync_api import Page, expect, sync_playwright, TimeoutError as PlaywrightTimeoutError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DEFAULT_TIMEOUT = 30000
NETWORK_TIMEOUT = 30000
NAVIGATION_TIMEOUT = 45000
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
    token = (token or "").strip()
    token = re.sub(r"[^\w\-Ͱ-Ͽἀ-῿.]+", "_", token, flags=re.U)
    return token or fallback


# Hard caps so a stuck row never blocks the whole brain run. See the
# matching constants + comment in efka-extractor.py for the rationale.
PDF_PER_ROW_TIMEOUT = 15000
PDF_TOTAL_TIMEOUT_SEC = 240.0
PDF_MAX_CONSEC_FAILS = 3
PDF_DEFAULT_MAX_ROWS = 60


def download_certificate_pdfs(page: Page, output_dir, max_rows: int = None,
                              prefix: str = "teka_cert",
                              year_filter: int = None) -> list:
    """Best-effort per-row PDF download for TEKA. NEVER fails the caller.

    When ``year_filter`` is supplied, only the row whose first cell starts
    with that year is downloaded.
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
        logging.warning("TEKA PDF download skipped — table did not load (%s)", exc)
        return []

    rows = page.locator(
        "#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable tr[class*='dxgvDataRow']"
    )
    total = rows.count()
    cap = max_rows if max_rows is not None else PDF_DEFAULT_MAX_ROWS
    total = min(total, cap)
    logging.info("TEKA PDF download: targeting %d certificate rows (cap=%d)", total, cap)

    saved = []
    consecutive_fails = 0
    deadline = time.monotonic() + PDF_TOTAL_TIMEOUT_SEC
    for i in range(total):
        if time.monotonic() > deadline:
            logging.warning(
                "TEKA PDF deadline reached after %d/%d rows; stopping cleanly",
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

        if year_filter is not None:
            first_cell = label_parts[0] if label_parts else ""
            if not first_cell.strip().startswith(str(year_filter)):
                logging.info("Row %d: year filter %s skips (first cell=%r)",
                             i, year_filter, first_cell)
                continue

        safe_label = _safe_filename(label.replace(" | ", "_"), f"row{i}")[:80]
        out_path = out_dir / f"{prefix}_{i:02d}_{safe_label}.pdf"

        try:
            print_button = row.locator("a:has(img[title*='Εκτύπωση'])").first
            if not print_button.count():
                print_img = row.locator("img[title*='Εκτύπωση']").first
                if print_img.count():
                    print_button = print_img.locator("xpath=ancestor::a[1]")
            if not print_button or not print_button.count():
                logging.info("Row %d: no print button found — skip", i)
                consecutive_fails += 1
                if consecutive_fails >= PDF_MAX_CONSEC_FAILS:
                    break
                continue

            # The DevExpress print button fires the PDF download on the MAIN
            # page (within ~200ms) and ALSO opens a blank popup. We wait for
            # the download on `page`, then close any spurious blank popups.
            # See efka-extractor.py for the same logic.
            download_obj = None
            try:
                with page.expect_download(timeout=PDF_PER_ROW_TIMEOUT) as dl_info:
                    print_button.first.click(timeout=PDF_PER_ROW_TIMEOUT)
                download_obj = dl_info.value
            except PlaywrightTimeoutError:
                download_obj = None

            for p in list(page.context.pages):
                if p is page:
                    continue
                try:
                    url = p.url or ""
                except Exception:
                    url = ""
                if not url or url == "about:blank":
                    try:
                        p.close()
                    except Exception:
                        pass

            if download_obj is None:
                consecutive_fails += 1
                logging.info("Row %d: print did not produce a download in %dms — skip",
                             i, PDF_PER_ROW_TIMEOUT)
            else:
                download_obj.save_as(str(out_path))
                saved.append({"row": i, "path": str(out_path), "label": label})
                consecutive_fails = 0
                logging.info("Row %d saved: %s", i, out_path.name)
        except Exception as exc:
            consecutive_fails += 1
            logging.info("Row %d: error during download (%s) — skip", i, exc)
        if consecutive_fails >= PDF_MAX_CONSEC_FAILS:
            logging.warning("TEKA PDF download: %d consecutive failures, stopping early", consecutive_fails)
            break
    logging.info("TEKA PDF download done: %d/%d saved", len(saved), total)
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

        # Strip the trailing empty DevExpress adaptive cell — see
        # efka-extractor.py for the rationale.
        if headers and len(values) > len(headers):
            while values and len(values) > len(headers) and not values[-1].strip():
                values = values[:-1]

        if headers and len(values) == len(headers):
            row_data = {headers[j]: values[j] for j in range(len(headers))}
        elif headers and len(values) > len(headers):
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
    idika_url = "https://www.idika.org.gr/EfkaServices/Application/TekaCertificates.aspx"

    page.goto(e_efka_url, timeout=DEFAULT_TIMEOUT)
    try:
        page.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
    except PlaywrightTimeoutError:
        logging.info("Networkidle never reached on e-EFKA landing; continuing.")

    if "blocked" in page.title().lower() or "web page blocked" in page.content().lower():
        logging.warning("Initial e-EFKA page appears blocked; trying direct IDIKA URL.")
        page.goto(idika_url, timeout=DEFAULT_TIMEOUT)
        try:
            page.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
        except PlaywrightTimeoutError:
            logging.info("Networkidle never reached on IDIKA fallback; continuing.")

    link = page.get_by_role("link", name="Βεβαιώσεις Εισφορών ΤΕΚΑ")
    if link.count() == 0:
        link = page.locator("a", has_text="Βεβαιώσεις Εισφορών ΤΕΚΑ")
    if link.count() == 0:
        link = page.locator("a", has_text="Βεβαιώσεις Εισφορών")

    if link.count() == 0:
        raise RuntimeError("Could not find the ΤΕΚΑ certificates link on the initial page.")

    with page.expect_popup(timeout=POPUP_TIMEOUT) as page1_info:
        link.first.scroll_into_view_if_needed()
        link.first.click()
    page1 = page1_info.value
    try:
        page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
    except PlaywrightTimeoutError:
        logging.info("Networkidle never reached on TEKA popup; continuing.")

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

    try:
        page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
    except PlaywrightTimeoutError:
        logging.info("Networkidle never reached after TAXISnet on TEKA; continuing.")

    # Robust username/password fallback (see efka-extractor.py for context).
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
    creds_filled = False
    if username_field.count() and password_field.count():
        username_field.first.fill(username, timeout=NETWORK_TIMEOUT)
        password_field.first.fill(password, timeout=NETWORK_TIMEOUT)
        creds_filled = True
    else:
        # The previous fallback filled the TAXISnet `username` argument into
        # an AFM input — but when this script is launched by the brain,
        # `username` is the TAXISnet username (e.g. "spiroslougaris"), NOT
        # the company AFM. Misrouting it rejects the next step. Skip the
        # credential step instead and trust that downstream (radio + AMKA)
        # can still proceed if we are already past TAXISnet.
        logging.warning(
            "TAXISnet username/password fields not found on this page — "
            "assuming we are past TAXISnet (or on a consent page) and "
            "proceeding without filling credentials."
        )

    if creds_filled:
        login_button = page1.locator("#btn-login-submit")
        if login_button.count() == 0:
            login_button = page1.get_by_role("button", name="Σύνδεση")
        if login_button.count() == 0:
            login_button = page1.locator("button:has-text('Είσοδος'), input[type='submit'][value*='Εισοδος']").first
        if login_button.count() == 0:
            raise RuntimeError("Could not find the login button on the TEKA page.")
        try:
            with page1.expect_navigation(timeout=NAVIGATION_TIMEOUT):
                login_button.click()
        except PlaywrightTimeoutError:
            login_button.click()
        try:
            page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
        except PlaywrightTimeoutError:
            logging.info("Networkidle never reached after TEKA login; continuing.")

    if page1.locator("button:has-text('Συνέχεια')").count() > 0:
        continue_button = page1.locator("button:has-text('Συνέχεια')").first
        try:
            with page1.expect_navigation(timeout=NAVIGATION_TIMEOUT):
                continue_button.click()
        except PlaywrightTimeoutError:
            continue_button.click()
        try:
            page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
        except PlaywrightTimeoutError:
            logging.info("Networkidle never reached after TEKA Συνέχεια; continuing.")

    if page1.locator("button:has-text('Αποστολή')").count() > 0:
        submit_button = page1.locator("button:has-text('Αποστολή')").first
        try:
            with page1.expect_navigation(timeout=NAVIGATION_TIMEOUT):
                submit_button.click()
        except PlaywrightTimeoutError:
            submit_button.click()
        try:
            page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
        except PlaywrightTimeoutError:
            logging.info("Networkidle never reached after TEKA Αποστολή; continuing.")

    if page1.locator("input[name*='amka'], input[id*='amka']").count() > 0 and page1.locator("button:has-text('Είσοδος')").count() > 0:
        afm_field = page1.locator("input[name*='afm'], input[id*='afm']")
        amka_field = page1.locator("input[name*='amka'], input[id*='amka']")
        if afm_field.count() > 0 and amka_field.count() > 0:
            # Only fill AFM here if the supplied `username` actually LOOKS like
            # a 9-digit AFM. The brain passes the TAXISnet username (e.g.
            # "spiroslougaris") which must NEVER be filled into the AFM
            # input — that triggers a validation error and the script hangs.
            uname_digits = "".join(ch for ch in str(username or "") if ch.isdigit())
            if len(uname_digits) == 9 and uname_digits == str(username or "").strip():
                afm_field.first.fill(username)
            else:
                logging.info(
                    "Skipping AFM auto-fill on AMKA login page — supplied "
                    "username does not look like a 9-digit AFM."
                )
            amka_field.first.fill(amka)
            enter_button = page1.locator("button:has-text('Είσοδος')").first
            try:
                with page1.expect_navigation(timeout=NAVIGATION_TIMEOUT):
                    enter_button.click()
            except PlaywrightTimeoutError:
                enter_button.click()
            try:
                page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
            except PlaywrightTimeoutError:
                logging.info("Networkidle never reached after TEKA AMKA enter; continuing.")

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
        try:
            page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
        except PlaywrightTimeoutError:
            logging.info("Networkidle never reached after TEKA radio submit; continuing.")

        amka_field = page1.get_by_role("textbox", name="ΑΜΚΑ:")
        if amka_field.count() == 0:
            amka_field = page1.locator("input[name*='AMKA'], input[id*='AMKA']")
        if amka_field.count() == 0:
            logging.info("No second AMKA field found after the radio selection; continuing.")
        else:
            amka_field.first.fill(amka, timeout=NETWORK_TIMEOUT)
            enter_button = page1.get_by_role("button", name="Είσοδος")
            if enter_button.count() == 0:
                enter_button = page1.locator("input[type='submit'][value*='Εισοδος'], button:has-text('Είσοδος')").first
            if enter_button.count() > 0:
                try:
                    with page1.expect_navigation(timeout=NAVIGATION_TIMEOUT):
                        enter_button.click()
                except PlaywrightTimeoutError:
                    enter_button.click()
                try:
                    page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
                except PlaywrightTimeoutError:
                    logging.info("Networkidle never reached after TEKA AMKA enter (2nd); continuing.")

    page1.wait_for_timeout(SHORT_WAIT)

    logging.info("Clicking on the section to scrape the table.")
    # Section finder: a TAXISNET user that is NOT enrolled in TEKA simply
    # does not see the "Φορολογικές Βεβαιώσεις ΤΕΚΑ" section. In that
    # case, emit an empty JSON so the brain reports "no TEKA amount" but
    # exits cleanly (returncode 0) — otherwise the brain would treat the
    # subprocess as a hard failure even though missing TEKA is expected.
    section_locator = page1.locator("section div", has_text="Φορολογικές Βεβαιώσεις ΤΕΚΑ")
    section_count = section_locator.count()
    if section_count == 0:
        logging.info("No TEKA section visible — user not enrolled in TEKA. Emitting empty result.")
        with open("extracted_table_data_teka.json", "w", encoding="utf-8") as output_file:
            json.dump({"headers": [], "header_map": {}, "rows": [], "no_teka": True},
                      output_file, ensure_ascii=False, indent=4)
        return
    # Pick the most specific clickable item — prefer .nth(4) but fall
    # back through 3,2,1,0 when fewer matches exist (different layout).
    section_button = None
    for idx in [4, 3, 2, 1, 0]:
        if idx < section_count:
            section_button = section_locator.nth(idx)
            try:
                section_button.wait_for(state="visible", timeout=5000)
                break
            except PlaywrightTimeoutError:
                section_button = None
                continue
    if section_button is None:
        logging.info("TEKA section located but no nth() entry was visible — emitting empty result.")
        with open("extracted_table_data_teka.json", "w", encoding="utf-8") as output_file:
            json.dump({"headers": [], "header_map": {}, "rows": [], "no_teka": True},
                      output_file, ensure_ascii=False, indent=4)
        return
    section_button.click()

    try:
        page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
    except PlaywrightTimeoutError:
        logging.info("Networkidle never reached after TEKA section click; continuing anyway.")

    try:
        page1.wait_for_selector(
            "#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable", timeout=NETWORK_TIMEOUT
        )
    except PlaywrightTimeoutError:
        logging.info("TEKA table did not appear — emitting empty result.")
        with open("extracted_table_data_teka.json", "w", encoding="utf-8") as output_file:
            json.dump({"headers": [], "header_map": {}, "rows": [], "no_teka": True},
                      output_file, ensure_ascii=False, indent=4)
        return

    output = extract_table_data(page1)
    with open("extracted_table_data_teka.json", "w", encoding="utf-8") as output_file:
        json.dump(output, output_file, ensure_ascii=False, indent=4)

    logging.info("Saved extracted_table_data_teka.json with headers and row mappings.")

    pdf_dir = os.getenv("TEKA_PDF_DIR")
    if pdf_dir:
        try:
            year_env = os.getenv("TEKA_PDF_YEAR")
            year_filter = int(year_env) if year_env and year_env.isdigit() else None
            saved = download_certificate_pdfs(page1, pdf_dir, prefix="teka_cert",
                                              year_filter=year_filter)
            output["pdfs"] = saved
            with open("extracted_table_data_teka.json", "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=4)
            logging.info("Downloaded %d TEKA PDFs to %s", len(saved), pdf_dir)
        except Exception as exc:
            logging.warning("TEKA PDF download skipped: %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="ΤΕΚΑ extractor for table data.")
    parser.add_argument("--username", default=os.getenv("TEKA_USER", "159712098"), help="TAXISNET username")
    parser.add_argument("--password", default=os.getenv("TEKA_PASSWORD", "159712"), help="TAXISNET password")
    parser.add_argument("--amka", default=os.getenv("TEKA_AMKA", "12019400675"), help="AMKA to fill")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--pdf-dir", default=os.getenv("TEKA_PDF_DIR"),
                        help="If set, download each certificate PDF into this directory")
    parser.add_argument("--pdf-year", default=os.getenv("TEKA_PDF_YEAR"), type=str,
                        help="If set with --pdf-dir, only download the row for this year (e.g. 2025).")
    args = parser.parse_args()
    if args.pdf_dir:
        os.environ["TEKA_PDF_DIR"] = args.pdf_dir
    if args.pdf_year:
        os.environ["TEKA_PDF_YEAR"] = str(args.pdf_year)

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
