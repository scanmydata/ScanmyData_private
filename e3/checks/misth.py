import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import Playwright, async_playwright

AADE_ENTRY_URL = "https://www1.aade.gr/sgsisapps/plcs"
AADE_COMREG_URL = "https://www1.aade.gr/saadeapps3/comregistry/#!/arxiki"
AADE_MISHT_URL = "https://www.aade.gr/polites/eisodima/misthotiria-akiniton"
STORAGE_STATE_PATH = Path("misth_storage_state.json")
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'


def login_and_open_listing(page, username: str, password: str):
    return _login_and_open_listing(page, username, password)


async def _login_and_open_listing(page, username: str, password: str):
    await page.goto(AADE_MISHT_URL)
    await page.wait_for_load_state("networkidle")

    entry_link = page.locator('a:has-text("Είσοδος στην Εφαρμογή")')
    if await entry_link.count() == 0:
        entry_link = page.locator(f'a[href="{AADE_ENTRY_URL}"]')
    if await entry_link.count() == 0:
        error_html = await dump_page_html(page, 'entry_link_missing')
        raise RuntimeError(f"Could not find public entry link on AADE misth page. Debug: {error_html}")

    popup_page = None
    async with page.context.expect_page() as popup_info:
        await entry_link.first.click()
    popup_page = await popup_info.value
    await popup_page.wait_for_load_state("networkidle")
    await popup_page.wait_for_timeout(3000)

    if await popup_page.locator('input[name="username"]').count() > 0 and await popup_page.locator('input[name="password"]').count() > 0:
        await popup_page.locator('input[name="username"]').fill(username)
        await popup_page.locator('input[name="password"]').fill(password)
        login_button = popup_page.locator('button[name="btn_login"]').first
        if await login_button.count() == 0:
            login_button = popup_page.locator('button:has-text("Συνδεση"), button:has-text("ΣΥΝΔΕΣΗ")').first
        if await login_button.count() == 0:
            error_html = await dump_page_html(popup_page, 'login_button_missing')
            raise RuntimeError(f"Could not find the GSIS login button in popup. Debug: {error_html}")

        await login_button.click()
        await popup_page.wait_for_load_state("networkidle")
        await popup_page.wait_for_timeout(3000)
        if "Error.jsp" in popup_page.url or "p_error_code" in popup_page.url:
            page_content = await popup_page.content()
            error_html = await dump_page_html(popup_page, 'login_error')
            if "OAM-6" in popup_page.url or "Ο χρήστης χρησιμοποιεί ήδη το μέγιστο αριθμό περιόδων λειτουργίας" in page_content:
                raise RuntimeError(f"GSIS login failed with session limit OAM-6. Close an existing session or use different credentials. URL={popup_page.url}. Debug: {error_html}")
            raise RuntimeError(f"GSIS login failed. URL={popup_page.url}. Debug: {error_html}")

    await popup_page.wait_for_load_state("networkidle")
    await popup_page.wait_for_timeout(3000)

    entry_button = popup_page.locator('input[name="button1"][value="Επιλογή"], input[type=button][value="Επιλογή"], input[type=button][name="button1"]')
    if await entry_button.count() > 0:
        await entry_button.first.wait_for(state="visible", timeout=20000)
        await entry_button.first.click()
        await popup_page.wait_for_load_state("networkidle")
        await popup_page.wait_for_timeout(3000)
    else:
        error_html = await dump_page_html(popup_page, 'button1_missing')
        raise RuntimeError(f"Could not find the button1 Επιλογή on displayConsole.htm. Debug: {error_html}")

    if await popup_page.locator('input[name="button2"]').count() == 0:
        error_html = await dump_page_html(popup_page, 'lease_list_missing')
        raise RuntimeError(f"Lease list page did not load or no lease entries found. Debug: {error_html}")

    return popup_page


async def collect_listing_entries(page):
    forms = page.locator('form:has(input[name="button2"])')
    entries = []
    for i in range(await forms.count()):
        form = forms.nth(i)
        trans_id = await form.locator('input[name="transId"]').input_value()
        row = form.locator('xpath=ancestor::td[contains(@class, "displaySubmissionDetails1")]')
        summary = []
        if await row.count() > 0:
            summary = await row.nth(0).locator('td').all_inner_texts()
            summary = [text.strip() for text in summary if text.strip()]
        entries.append({
            'index': i,
            'transId': trans_id,
            'summary': summary,
        })
    return entries


async def extract_detail_page(page):
    tables = page.locator('table.kadtable2, table.textbluelec5, table#propertyTable0')
    if await tables.count() == 0:
        all_tables = await page.locator('table').count()
        error_html = await dump_page_html(page, 'detail_tables_missing')
        raise RuntimeError(f"No detail target tables found on detail page. Found {all_tables} table(s). Debug: {error_html}")

    extracted_tables = []
    for idx in range(await tables.count()):
        table = tables.nth(idx)
        rows = []
        row_locator = table.locator('tr')
        for j in range(await row_locator.count()):
            cell_texts = await row_locator.nth(j).locator('th,td').all_inner_texts()
            rows.append([text.strip() for text in cell_texts if text.strip()])
        table_html = await table.evaluate('(node) => node.outerHTML')
        extracted_tables.append({
            'tableIndex': idx,
            'tableClasses': await table.get_attribute('class'),
            'tableId': await table.get_attribute('id'),
            'rows': rows,
            'html': table_html,
        })

    return {
        'pageTitle': await page.title(),
        'url': page.url,
        'detailTables': extracted_tables,
    }


async def back_to_list(page):
    back_link = page.locator('a:has-text("Επιστροφή στη διαχείριση των δηλώσεων")')
    if await back_link.count() > 0:
        await back_link.first.click()
    else:
        back_btn = page.locator('input[value="Επιστροφή"], button:has-text("Επιστροφή"), a:has-text("Επιστροφή")')
        if await back_btn.count() > 0:
            await back_btn.first.click()
        else:
            await page.go_back()
    await page.wait_for_load_state("networkidle")
    await page.wait_for_selector('input[type=image][name="button2"], input[name="button2"]', timeout=20000)
    await page.wait_for_timeout(2000)


USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
DEFAULT_HEADERS = {
    'accept-language': 'el-GR,el;q=0.9,en-US;q=0.8,en;q=0.7',
    'referer': 'https://www1.aade.gr/',
}
USER_DATA_DIR = Path('misth_user_data')


async def dump_page_html(page, suffix: str) -> Path:
    path = Path(f'misth_debug_{suffix}.html')
    html = await page.content()
    path.write_text(html, encoding='utf-8')
    return path


async def run(playwright: Playwright, username: str, password: str, output_path: Path, headed: bool) -> None:
    try:
        from e3.checks import chromium_launch_args as _svfb_args
    except Exception:
        def _svfb_args():
            return [
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-software-rasterizer',
            ]
    browser = await playwright.chromium.launch(
        headless=not headed,
        args=_svfb_args(),
    )
    context_args = {
        'user_agent': USER_AGENT,
        'locale': 'el-GR',
        'viewport': {'width': 1280, 'height': 1024},
        'extra_http_headers': DEFAULT_HEADERS,
    }
    if STORAGE_STATE_PATH.exists():
        context_args['storage_state'] = str(STORAGE_STATE_PATH)

    context = await browser.new_context(**context_args)
    page = await context.new_page()

    page1 = await _login_and_open_listing(page, username, password)
    await context.storage_state(path=str(STORAGE_STATE_PATH))
    entries = await collect_listing_entries(page1)

    results = []
    for entry in entries:
        print(f'Processing lease entry index={entry["index"]} transId={entry["transId"]}')
        forms = page1.locator('form:has(input[name="button2"])')
        match_index = None
        for i in range(await forms.count()):
            if await forms.nth(i).locator('input[name="transId"]').input_value() == entry['transId']:
                match_index = i
                break
        if match_index is None:
            raise RuntimeError(f'Could not find form for transId {entry["transId"]}')

        await forms.nth(match_index).locator('input[name="button2"]').click()
        await page1.wait_for_load_state("networkidle")
        await page1.wait_for_timeout(2000)

        detail = await extract_detail_page(page1)
        results.append({
            'transId': entry['transId'],
            'summary': entry['summary'],
            'detail': detail,
        })

        await back_to_list(page1)

    with output_path.open('w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    await context.close()
    await browser.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description='Extract AADE misth data to JSON')
    parser.add_argument('--username', default='user3407382057')
    parser.add_argument('--password', default='aggeliki92')
    parser.add_argument('--output', default='extracted_misth.json')
    parser.add_argument('--headed', action='store_true', help='Run browser in headed mode')
    args = parser.parse_args()

    async with async_playwright() as playwright:
        await run(playwright, args.username, args.password, Path(args.output), args.headed)


if __name__ == '__main__':
    asyncio.run(main())
