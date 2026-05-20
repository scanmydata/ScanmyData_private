import argparse
import os
import re
import json
import logging
from playwright.sync_api import Page, expect, sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def test_example(page: Page, username: str, password: str, amka: str) -> None:
    page.goto("https://www.e-efka.gov.gr/el/elektronikes-yperesies/bebaiose-eisphoron-gia-phorologike-chrese")
    with page.expect_popup() as page1_info:
        page.get_by_role("link", name="Βεβαιώσεις Εισφορών e-ΕΦΚΑ").click()
    page1 = page1_info.value
    page1.get_by_role("button", name="Συνέχεια στο TAXISNET").click()
    page1.get_by_role("textbox", name="Χρήστης:").click()
    page1.get_by_role("textbox", name="Χρήστης:").fill(username)
    page1.get_by_role("textbox", name="Κωδικός:").click()
    page1.get_by_role("textbox", name="Κωδικός:").fill(password)
    page1.get_by_role("textbox", name="Κωδικός:").press("Enter")
    page1.get_by_role("radio").nth(1).check()
    page1.get_by_role("button", name="Αποστολή").click()
    page1.get_by_role("textbox", name="ΑΜΚΑ:").click()
    page1.get_by_role("textbox", name="ΑΜΚΑ:").fill(amka)
    page1.get_by_role("button", name="Είσοδος").click()
    expect(page1.get_by_text("Βεβαιώσεις για φορολογική χρήση Φορολογικές Βεβαιώσεις e")).to_be_visible()
    page1.wait_for_timeout(2000)

    logging.info("Clicking on the section to scrape the table.")
    section_button = page1.locator("section div", has_text="Φορολογικές Βεβαιώσεις e").nth(4)
    section_button.wait_for(state="visible", timeout=30000)
    section_button.click()
    logging.info("Section clicked. Proceeding to scrape the table.")
    page1.wait_for_load_state("networkidle")
    page1.wait_for_selector("#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable", timeout=20000)

    def clean_text(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().replace("\n", " ")).strip()

    def collapse_adjacent_duplicates(values: list[str]) -> list[str]:
        collapsed = []
        for value in values:
            if not collapsed or value != collapsed[-1]:
                collapsed.append(value)
        return collapsed

    logging.info("Locating table rows and headers.")
    header_rows = page1.locator("#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable tr[id*='DXHeadersRow']")
    headers = []
    if header_rows.count() > 0:
        first_header_row = header_rows.nth(0)
        header_cells = first_header_row.locator("td")
        for h in range(header_cells.count()):
            header_text = clean_text(header_cells.nth(h).inner_text())
            if header_text:
                headers.append(header_text)
        headers = collapse_adjacent_duplicates(headers)
    logging.info(f"Captured {len(headers)} header columns: {headers}")

    table_rows = page1.locator("#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable tr[class*='dxgvDataRow']")
    row_count = table_rows.count()
    logging.info(f"Found {row_count} data rows in the table.")

    extracted_data = []
    for i in range(row_count):
        row = table_rows.nth(i)
        cells = row.locator("td")
        cell_count = cells.count()
        logging.info(f"Processing row {i} with {cell_count} cells.")

        row_values = []
        for j in range(cell_count):
            cell_text = clean_text(cells.nth(j).inner_text())
            cell_text = re.sub(r"<!--.*?-->", "", cell_text, flags=re.S).strip()
            cell_text = re.sub(r"\s+", " ", cell_text)
            row_values.append(cell_text)

        # Align headers with row values, falling back to generic keys when needed.
        mapped_headers = headers[:cell_count]
        if len(mapped_headers) < cell_count:
            mapped_headers += [f"col_{j}" for j in range(len(mapped_headers), cell_count)]

        row_data = {mapped_headers[j]: row_values[j] for j in range(cell_count)}
        logging.info(f"Row {i} data: {row_data}")
        extracted_data.append(row_data)

    logging.info("Table data extraction complete.")

    # Save the extracted data to a file
    with open("extracted_table_data.json", "w", encoding="utf-8") as f:
        json.dump(extracted_data, f, ensure_ascii=False, indent=4)

    logging.info("Table data saved to extracted_table_data.json.")


def main():
    parser = argparse.ArgumentParser(description="Scrape the EFKA certificates table.")
    parser.add_argument("--username", default=os.getenv("EFKA_USER", "159712098"), help="TAXISNET username")
    parser.add_argument("--password", default=os.getenv("EFKA_PASSWORD", "159712"), help="TAXISNET password")
    parser.add_argument("--amka", default=os.getenv("EFKA_AMKA", "12019400675"), help="AMKA value")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    args = parser.parse_args()

    logging.info("Launching browser in %s mode.", "headless" if args.headless else "headed")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context()
        page = context.new_page()

        try:
            test_example(page, args.username, args.password, args.amka)
        finally:
            browser.close()


if __name__ == "__main__":
    main()

