import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import Playwright, async_playwright

AADE_ENTRY_URL = "https://www1.aade.gr/sgsisapps/plcs"
AADE_COMREG_URL = "https://www1.aade.gr/saadeapps3/comregistry/#!/arxiki"
AADE_ENFIA_URL = "https://www.aade.gr/dilosi-e9-enfia"
AADE_REGISTRY_URL = "https://www1.aade.gr/taxisnet/info/protected/displayRegistryInfo.htm"
AADE_ETAK_URL = "https://www1.aade.gr/etak/faces/main.jspx"
STORAGE_STATE_PATH = Path("etak_storage_state.json")
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'


def login_and_open_listing(page, username: str, password: str):
    return _login_and_open_listing(page, username, password)


async def _login_and_open_listing(page, username: str, password: str):
    await page.goto(AADE_ENFIA_URL)
    await page.wait_for_load_state("networkidle")

    entry_link = page.locator('a[href="https://www1.aade.gr/etak/"]')
    if await entry_link.count() == 0:
        entry_link = page.locator('a:has-text("Είσοδος στην εφαρμογή")')
    if await entry_link.count() == 0:
        entry_link = page.locator('a:has-text("Είσοδος")')
    if await entry_link.count() == 0:
        error_html = await dump_page_html(page, 'entry_link_missing')
        raise RuntimeError(f"Could not find ETΑΚ entry link on AADE E9/ENFIA page. Debug: {error_html}")

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

    if popup_page.url.startswith("https://www1.aade.gr/etak/") and "faces/main.jspx" not in popup_page.url:
        await popup_page.goto(AADE_ETAK_URL)
        await popup_page.wait_for_load_state("networkidle")
        await popup_page.wait_for_timeout(3000)
        return popup_page

    etak_entry_button = popup_page.locator('button#pt1\\:cbEnter, button:has-text("Είσοδος")')
    if await etak_entry_button.count() > 0:
        await etak_entry_button.first.wait_for(state="visible", timeout=20000)
        try:
            async with popup_page.expect_navigation(timeout=30000):
                await etak_entry_button.first.click()
        except Exception:
            await etak_entry_button.first.click()
            await popup_page.wait_for_load_state("networkidle")
        await popup_page.wait_for_timeout(3000)
        return popup_page

    entry_button = popup_page.locator('input[name="button1"][value="Επιλογή"], input[type=button][value="Επιλογή"], input[type=button][name="button1"]')
    if await entry_button.count() > 0:
        await entry_button.first.wait_for(state="visible", timeout=20000)
        await entry_button.first.click()
        await popup_page.wait_for_load_state("networkidle")
        await popup_page.wait_for_timeout(3000)
    else:
        if popup_page.url.startswith("https://www1.aade.gr/etak/"):
            await popup_page.goto(AADE_ETAK_URL)
            await popup_page.wait_for_load_state("networkidle")
            await popup_page.wait_for_timeout(3000)
            return popup_page
        error_html = await dump_page_html(popup_page, 'button1_missing')
        raise RuntimeError(f"Could not find the button1 Επιλογή on displayConsole.htm or the ETΑΚ Είσοδος entry button. Debug: {error_html}")

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


async def extract_registry_info(page):
    content_td = page.locator('td.contenttd')
    if await content_td.count() == 0:
        error_html = await dump_page_html(page, 'registry_contenttd_missing')
        raise RuntimeError(f"Could not find the TAXIS registry content container. Debug: {error_html}")

    tables = content_td.locator('table')
    if await tables.count() == 0:
        error_html = await dump_page_html(page, 'registry_tables_missing')
        raise RuntimeError(f"Could not find registry table(s) inside contenttd. Debug: {error_html}")

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
        'registryTables': extracted_tables,
    }


async def extract_etak_year_options(page):
    select_locator = page.locator('select[name="pt1:yearSelect"]')
    if await select_locator.count() == 0:
        select_locator = page.locator('select[id="pt1:yearSelect::content"]')
    if await select_locator.count() == 0:
        error_html = await dump_page_html(page, 'etak_year_select_missing')
        raise RuntimeError(f"Could not find the ETΑΚ year select element. Debug: {error_html}")

    options = select_locator.locator('option')
    year_options = []
    selected_year = None
    for i in range(await options.count()):
        option = options.nth(i)
        value = await option.get_attribute('value') or ''
        title = await option.get_attribute('title') or ''
        text = (await option.inner_text()).strip()
        selected = await option.get_attribute('selected') is not None
        if selected:
            selected_year = title or text
        year_options.append({
            'value': value,
            'title': title,
            'text': text,
            'selected': selected,
        })

    return {
        'selectId': await select_locator.get_attribute('id'),
        'selectName': await select_locator.get_attribute('name'),
        'selectedYear': selected_year,
        'yearOptions': year_options,
    }


async def click_etak_entry_button(page):
    entry_button = page.locator('button#pt1\\:cbEnter, button:has-text("Είσοδος")')
    if await entry_button.count() == 0:
        return False

    try:
        async with page.expect_navigation(timeout=30000):
            await entry_button.first.click()
    except Exception:
        await entry_button.first.click()
        await page.wait_for_load_state('networkidle')
    await page.wait_for_timeout(2000)
    return True


async def click_etak_property_status_tab(page):
    tab = page.locator('a:has-text("Περιουσιακή Κατάσταση")')
    if await tab.count() == 0:
        error_html = await dump_page_html(page, 'etak_property_tab_missing')
        raise RuntimeError(f"Could not find the ETΑΚ Περιουσιακή Κατάσταση tab. Debug: {error_html}")

    await tab.first.click()
    await page.wait_for_load_state('networkidle')
    await page.wait_for_timeout(2000)


async def extract_etak_grids(page):
    grid_locators = page.locator('div[role="grid"]')
    if await grid_locators.count() == 0:
        error_html = await dump_page_html(page, 'etak_grids_missing')
        raise RuntimeError(f"Could not find any ETΑΚ grid elements after clicking the property status tab. Debug: {error_html}")

    def _clean_text(text: str) -> str:
        return ' '.join(text.strip().split())

    extracted_grids = []
    for idx in range(await grid_locators.count()):
        grid = grid_locators.nth(idx)
        grid_id = await grid.get_attribute('id') or ''
        header_cells = grid.locator('[role="columnheader"], th')
        headers = [
            _clean_text(await header_cells.nth(i).inner_text())
            for i in range(await header_cells.count())
            if _clean_text(await header_cells.nth(i).inner_text())
        ]

        row_locators = grid.locator('[role="row"]')
        rows = []
        for r in range(await row_locators.count()):
            row = row_locators.nth(r)
            cell_locators = row.locator('[role="gridcell"], td')
            if await cell_locators.count() == 0:
                continue
            row_values = [
                _clean_text(await cell_locators.nth(j).inner_text())
                for j in range(await cell_locators.count())
            ]
            if row_values:
                rows.append(row_values)

        extracted_grids.append({
            'gridIndex': idx,
            'gridId': grid_id,
            'headers': headers,
            'rows': rows,
            'html': await grid.evaluate('(node) => node.outerHTML'),
        })

    return extracted_grids


async def extract_etak_property_status(page):
    year_info = await extract_etak_year_options(page)
    await click_etak_property_status_tab(page)
    grids = await extract_etak_grids(page)
    return {
        'pageTitle': await page.title(),
        'url': page.url,
        'yearSelect': year_info,
        'propertyStatusGrids': grids,
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
USER_DATA_DIR = Path('etak_user_data')


async def dump_page_html(page, suffix: str) -> Path:
    path = Path(f'etak_debug_{suffix}.html')
    html = await page.content()
    path.write_text(html, encoding='utf-8')
    return path


async def run(playwright: Playwright, username: str, password: str, output_path: Path, headed: bool) -> None:
    browser = await playwright.chromium.launch(
        headless=not headed,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--disable-gpu',
            '--disable-software-rasterizer',
            '--no-sandbox',
            '--disable-dev-shm-usage',
        ],
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

    # If the login flow lands on the initial ETΑΚ landing page, click the enter button first.
    clicked_enter = await click_etak_entry_button(page1)
    if not clicked_enter:
        await page1.goto(AADE_ETAK_URL)
        await page1.wait_for_load_state("networkidle")
        await page1.wait_for_timeout(2000)

    etak_info = await extract_etak_property_status(page1)

    etak_output_path = output_path.parent / 'extracted_etak_property_status.json'
    with etak_output_path.open('w', encoding='utf-8') as f:
        json.dump(etak_info, f, ensure_ascii=False, indent=2)

    print(f'Wrote ETΑΚ JSON to: {etak_output_path}')

    await context.close()
    await browser.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description='Extract AADE ETΑΚ property status data to JSON')
    parser.add_argument('--username', default='802576637', help='AADE username')
    parser.add_argument('--password', default='Tv802576!', help='AADE password')
    parser.add_argument('--output', default='extracted_etak_property_status.json', help='Base output file path for JSON exports')
    parser.add_argument('--headed', action='store_true', help='Run browser in headed mode')
    args = parser.parse_args()

    async with async_playwright() as playwright:
        await run(playwright, args.username, args.password, Path(args.output), args.headed)


if __name__ == '__main__':
    asyncio.run(main())
