import argparse
import json
import logging
import os
import re
from playwright.sync_api import Page, expect, sync_playwright, TimeoutError as PlaywrightTimeoutError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DEFAULT_TIMEOUT = 20000
NETWORK_TIMEOUT = 20000
NAVIGATION_TIMEOUT = 20000
POPUP_TIMEOUT = 15000
ASSERT_TIMEOUT = 10000
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

        if headers and len(values) == len(headers):
            row_data = {headers[j]: values[j] for j in range(len(headers))}
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

    username_field = page1.get_by_role("textbox", name="Χρήστης:")
    password_field = page1.get_by_role("textbox", name="Κωδικός:")
    if username_field.count() and password_field.count():
        username_field.fill(username)
        password_field.fill(password)
        login_button = page1.locator("#btn-login-submit")
        if login_button.count() == 0:
            login_button = page1.get_by_role("button", name="Σύνδεση")
        try:
            with page1.expect_navigation(timeout=NAVIGATION_TIMEOUT):
                login_button.click()
        except PlaywrightTimeoutError:
            login_button.click()
        page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)
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
    if page1.locator("section div", has_text="Φορολογικές Βεβαιώσεις e").count() > 0:
        section_button = page1.locator("section div", has_text="Φορολογικές Βεβαιώσεις e").nth(4)
        section_button.wait_for(state="visible", timeout=NETWORK_TIMEOUT)
        section_button.click()
        page1.wait_for_load_state("networkidle", timeout=NETWORK_TIMEOUT)

    page1.wait_for_selector("#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable", timeout=NETWORK_TIMEOUT)

    output = extract_table_data(page1)
    with open("extracted_table_data.json", "w", encoding="utf-8") as output_file:
        json.dump(output, output_file, ensure_ascii=False, indent=4)

    logging.info("Saved extracted_table_data.json with headers and row mappings.")


def main() -> None:
    parser = argparse.ArgumentParser(description="EFKA extractor for table data.")
    parser.add_argument("--username", default=os.getenv("EFKA_USER", "159712098"), help="TAXISNET username")
    parser.add_argument("--password", default=os.getenv("EFKA_PASSWORD", "159712"), help="TAXISNET password")
    parser.add_argument("--amka", default=os.getenv("EFKA_AMKA", "12019400675"), help="AMKA to fill")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    args = parser.parse_args()

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
        )
        page = context.new_page()
        try:
            test_example(page, args.username, args.password, args.amka)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
