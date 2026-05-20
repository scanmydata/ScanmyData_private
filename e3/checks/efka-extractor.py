import argparse
import json
import logging
import os
import re
from playwright.sync_api import Page, expect, sync_playwright, TimeoutError as PlaywrightTimeoutError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def clean_text(text: str) -> str:
    value = text.strip().replace("\n", " ")
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    return re.sub(r"\s+", " ", value).strip()


def extract_table_data(page: Page) -> dict:
    page.wait_for_selector("#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable", timeout=30000)
    header_cells = page.locator("#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable tr[id*='DXHeadersRow'] td")
    headers = []
    for i in range(header_cells.count()):
        header_text = clean_text(header_cells.nth(i).inner_text())
        if header_text:
            headers.append(header_text)

    if not headers:
        header_cells = page.locator("#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable tr td")
        for i in range(header_cells.count()):
            header_text = clean_text(header_cells.nth(i).inner_text())
            if header_text:
                headers.append(header_text)

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

    page.goto(e_efka_url)
    page.wait_for_load_state("networkidle", timeout=60000)

    if "blocked" in page.title().lower() or "web page blocked" in page.content().lower():
        logging.warning("Initial e-EFKA page appears blocked; trying direct IDIKA URL.")
        page.goto(idika_url)
        page.wait_for_load_state("networkidle", timeout=60000)

    link = page.get_by_role("link", name="Βεβαιώσεις Εισφορών e-ΕΦΚΑ")
    if link.count() == 0:
        link = page.locator("a", has_text="Βεβαιώσεις Εισφορών e-ΕΦΚΑ")
    if link.count() == 0:
        link = page.locator("a", has_text="Βεβαιώσεις Εισφορών")

    if link.count() == 0:
        raise RuntimeError("Could not find the EFKA certificates link on the initial page.")

    with page.expect_popup(timeout=15000) as page1_info:
        link.first.scroll_into_view_if_needed()
        link.first.click()
    page1 = page1_info.value
    page1.wait_for_load_state("networkidle", timeout=30000)

    try:
        taxisnet_button = page1.get_by_role("button", name="Συνέχεια στο TAXISNET")
        with page1.expect_navigation(timeout=30000):
            taxisnet_button.click()
    except PlaywrightTimeoutError:
        logging.info("TAXISNET button not found by role; trying text fallback.")
        try:
            taxisnet_button = page1.get_by_text("Συνέχεια στο TAXISNET").first
            with page1.expect_navigation(timeout=30000):
                taxisnet_button.click()
        except PlaywrightTimeoutError:
            raise RuntimeError("Could not find or navigate from the TAXISNET login button on the page.")

    page1.wait_for_load_state("networkidle", timeout=30000)

    username_field = page1.get_by_role("textbox", name="Χρήστης:")
    if username_field.count() == 0:
        username_field = page1.locator("input[type='text']").first
    username_field.fill(username)

    password_field = page1.get_by_role("textbox", name="Κωδικός:")
    if password_field.count() == 0:
        password_field = page1.locator("input[type='password']").first
    password_field.fill(password)

    login_button = page1.locator("#btn-login-submit")
    if login_button.count() == 0:
        login_button = page1.get_by_role("button", name="Σύνδεση")
    with page1.expect_navigation(timeout=60000):
        login_button.click()
    page1.wait_for_load_state("networkidle", timeout=60000)

    page1.wait_for_selector("input[type='radio']", timeout=30000)
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

    page1.get_by_role("button", name="Αποστολή").click()

    amka_field = page1.get_by_role("textbox", name="ΑΜΚΑ:")
    if amka_field.count() == 0:
        amka_field = page1.locator("input[name*='AMKA']")
    amka_field.fill(amka)
    page1.get_by_role("button", name="Είσοδος").click()

    expect(page1.get_by_text("Βεβαιώσεις για φορολογική χρήση Φορολογικές Βεβαιώσεις e")).to_be_visible()
    page1.wait_for_timeout(2000)

    logging.info("Clicking on the section to scrape the table.")
    section_button = page1.locator("section div", has_text="Φορολογικές Βεβαιώσεις e").nth(4)
    section_button.wait_for(state="visible", timeout=30000)
    section_button.click()

    page1.wait_for_load_state("networkidle")
    page1.wait_for_selector("#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable", timeout=30000)

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
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=args.headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
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
