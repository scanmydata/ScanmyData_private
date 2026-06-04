import argparse
import asyncio
import json
import re
from pathlib import Path

import pdfplumber
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
    # The aade.gr landing page is occasionally slow to fire `load` (third-party
    # scripts, government CDN). Use a longer timeout + `domcontentloaded` as
    # the gate so the navigation doesn't fail when the slow tail scripts time
    # out — networkidle below still gives the page time to settle for the
    # entry-link locator.
    last_exc = None
    for attempt in range(3):
        try:
            await page.goto(AADE_ENFIA_URL, wait_until="domcontentloaded", timeout=90000)
            break
        except Exception as exc:
            last_exc = exc
            # Brief backoff before retrying. Each retry gives the slow CDN
            # another chance and avoids tearing the whole bulk run down on a
            # single transient timeout.
            await page.wait_for_timeout(2000 * (attempt + 1))
    else:
        raise last_exc if last_exc else RuntimeError("Failed to open AADE E9/ENFIA page")
    try:
        await page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        # networkidle can hang behind a long-poll widget on aade.gr; the
        # entry-link locator below is the actual readiness signal.
        pass

    entry_link = page.locator('a[href="https://www1.aade.gr/etak/"]')
    if await entry_link.count() == 0:
        entry_link = page.locator('a:has-text("Είσοδος στην εφαρμογή")')
    if await entry_link.count() == 0:
        entry_link = page.locator('a:has-text("Είσοδος")')
    if await entry_link.count() == 0:
        error_html = await dump_page_html(page, 'entry_link_missing')
        raise RuntimeError(f"Could not find ETΑΚ entry link on AADE E9/ENFIA page. Debug: {error_html}")

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

    etak_entry_button = popup_page.locator(r'button#pt1\:cbEnter, button:has-text("Είσοδος")')
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


async def dump_page_html(page, suffix: str) -> Path:
    path = Path(f'etak_debug_{suffix}.html')
    html = await page.content()
    path.write_text(html, encoding='utf-8')
    return path


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


def _strip_greek_diacritics(text: str) -> str:
    """Strip combining marks so e.g. ΗΛΙΌΥΠΟΛΗ -> ΗΛΙΟΥΠΟΛΗ."""
    import unicodedata
    if not text:
        return ''
    nf = unicodedata.normalize('NFD', text)
    return ''.join(c for c in nf if unicodedata.category(c) != 'Mn')


def normalize_search_text(text: str) -> str:
    if not text:
        return ''
    s = _strip_greek_diacritics(text).lower()
    s = s.replace(' ', ' ')
    s = re.sub(r'[^\w\d\s]', ' ', s, flags=re.UNICODE)
    return ' '.join(s.split()).strip()


def _address_stems(address: str) -> list:
    """Return stems worth matching from an address.

    The original substring matcher failed when the ETAK grid spelled the
    city in a different declension (e.g. ΗΛΙΟΥΠΟΛΗ vs ΗΛΙΟΥΠΟΛΕΩΣ) or with a missing
    accent. We normalise + de-accent + lower, then for each word with >= 3
    chars we keep a 5-char stem so both forms collapse to e.g. "ηλιου".
    Numbers (street numbers, postal codes) are kept verbatim because they
    are the most discriminating token.
    """
    norm = normalize_search_text(address)
    if not norm:
        return []
    stems = []
    for tok in norm.split():
        if len(tok) < 3:
            continue
        stems.append(tok if tok.isdigit() else tok[:5])
    return stems


def find_ataks_by_address(grids: list[dict], address: str) -> tuple[list[str], list[dict]]:
    needle_stems = _address_stems(address)
    if not needle_stems:
        return [], []

    matched_ataks = []
    matched_rows = []
    for grid in grids:
        for row in grid.get('rows', []):
            if not row:
                continue
            cell_stems = set()
            for cell in row:
                norm_cell = normalize_search_text(str(cell))
                for tok in norm_cell.split():
                    if len(tok) >= 3:
                        cell_stems.add(tok if tok.isdigit() else tok[:5])
            if all(stem in cell_stems for stem in needle_stems):
                atak = str(row[0]).strip()
                if atak and atak not in matched_ataks:
                    matched_ataks.append(atak)
                matched_rows.append({
                    'gridIndex': grid.get('gridIndex'),
                    'gridId': grid.get('gridId'),
                    'headers': grid.get('headers', []),
                    'row': row,
                })
    return matched_ataks, matched_rows


async def select_etak_year(page, year: str) -> str:
    select_locator = page.locator('select[name="pt1:yearSelect"], select[id="pt1:yearSelect::content"]')
    if await select_locator.count() == 0:
        error_html = await dump_page_html(page, 'etak_year_select_missing')
        raise RuntimeError(f"Could not find the ETΑΚ year select element. Debug: {error_html}")

    options = select_locator.locator('option')
    match_value = None
    match_label = None
    match_by = 'value'
    for i in range(await options.count()):
        option = options.nth(i)
        value = await option.get_attribute('value') or ''
        text = (await option.inner_text()).strip()
        if text == year or value == year or year in text or year in value:
            if value:
                match_value = value
                match_by = 'value'
            else:
                match_value = text
                match_by = 'label'
            match_label = text
            break

    if not match_value:
        valid_years = [((await options.nth(i).inner_text()).strip()) for i in range(await options.count())]
        raise RuntimeError(f"Requested year '{year}' not found in ETΑΚ year selector. Available: {valid_years}")

    await select_locator.click()
    if match_by == 'value':
        await select_locator.select_option(value=match_value)
    else:
        await select_locator.select_option(label=match_value)

    await page.evaluate(
        '''(payload) => {
            const { value, label } = payload;
            const select = document.querySelector('select[name="pt1:yearSelect"], select[id="pt1:yearSelect::content"]');
            if (!select) return false;
            const option = Array.from(select.options).find(o => o.value === value || o.textContent.trim() === label);
            if (option) {
                select.value = option.value;
            }
            let event;
            if (typeof Event === 'function') {
                event = new Event('change', { bubbles: true, cancelable: true });
            } else {
                event = document.createEvent('HTMLEvents');
                event.initEvent('change', true, true);
            }
            select.dispatchEvent(event);
            return select.value;
        }''',
        {
            'value': match_value if match_by == 'value' else '',
            'label': match_label if match_by != 'value' else '',
        },
    )

    await page.wait_for_timeout(2000)
    await page.wait_for_function(
        '''(payload) => {
            const { value, label } = payload;
            const select = document.querySelector('select[name="pt1:yearSelect"], select[id="pt1:yearSelect::content"]');
            if (!select) return false;
            if (value) return select.value === value;
            return select.options[select.selectedIndex]?.text.trim() === label;
        }''',
        arg={
            'value': match_value if match_by == 'value' else '',
            'label': match_label if match_by != 'value' else '',
        },
        timeout=10000,
    )
    return match_label or year


async def download_etak_pdf(page, year: str, download_dir: Path) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    await page.wait_for_timeout(1500)

    exact_button = page.locator(f'a[id="pt1:clPrintEkk{year}"]')
    print_button = None
    if await exact_button.count() > 0:
        exact_text = (await exact_button.first.inner_text()).strip().replace(' ', ' ')
        if 'εκτύπωση εκκαθαριστικού' in exact_text.lower():
            print_button = exact_button.first

    if print_button is None:
        candidates = []
        anchors = page.locator('a')
        for i in range(await anchors.count()):
            anchor = anchors.nth(i)
            text = (await anchor.inner_text()).strip()
            aid = await anchor.get_attribute('id') or ''
            if text:
                normalized = text.replace(' ', ' ')
                lower_text = normalized.lower()
                if 'εκτύπωση εκκαθαριστικού' in lower_text:
                    candidates.append({
                        'index': i,
                        'id': aid,
                        'text': normalized,
                    })

        for candidate in candidates:
            lower_text = candidate['text'].lower()
            if year in lower_text and 'εκτύπωση εκκαθαριστικού' in lower_text:
                loc = page.locator(f'a[id="{candidate["id"]}"]') if candidate['id'] else page.locator(f'a:has-text("{candidate["text"]}")')
                if await loc.count() > 0:
                    print_button = loc
                    break

    if print_button is None:
        for candidate in candidates:
            lower_text = candidate['text'].lower()
            if 'εκτύπωση εκκαθαριστικού' in lower_text:
                loc = page.locator(f'a[id="{candidate["id"]}"]') if candidate['id'] else page.locator(f'a:has-text("{candidate["text"]}")')
                if await loc.count() > 0:
                    print_button = loc
                    break

    if print_button is None:
        error_html = await dump_page_html(page, 'etak_print_pdf_button_missing')
        raise RuntimeError(f"Could not find the ETΑΚ εκκαθαριστικού print PDF button for year {year}. Debug: {error_html}")

    async with page.expect_download(timeout=120000) as download_info:
        await print_button.click()
    download = await download_info.value
    suggested = download.suggested_filename or f"etak_{year}.pdf"
    pdf_path = download_dir / suggested
    await download.save_as(str(pdf_path))
    return pdf_path


def extract_pdf_rows_for_ataks(pdf_path: Path, ataks: list[str]) -> list[dict]:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found at {pdf_path}")

    found_rows = []
    atak_digits = [re.sub(r'\D', '', atak) for atak in ataks]

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or '')
            tables = page.extract_tables() or []
            for table in tables:
                if not table or len(table) < 2:
                    continue
                for row in table[1:]:
                    row_text = ' '.join([str(cell or '') for cell in row]).strip()
                    digit_text = re.sub(r'\D', '', row_text)
                    for atak, atak_digits_value in zip(ataks, atak_digits):
                        if atak_digits_value and atak_digits_value in digit_text:
                            found_rows.append({
                                'page': page_num,
                                'row': row,
                                'text': row_text,
                                'matchedAtak': atak,
                            })
                            break
            if not found_rows:
                digit_text = re.sub(r'\D', '', text)
                for atak, atak_digits_value in zip(ataks, atak_digits):
                    if atak_digits_value and atak_digits_value in digit_text:
                        found_rows.append({
                            'page': page_num,
                            'row': [atak],
                            'text': text,
                            'matchedAtak': atak,
                        })
    return found_rows


async def click_etak_entry_button(page):
    entry_button = page.locator(r'button#pt1\:cbEnter, button:has-text("Είσοδος")')
    if await entry_button.count() == 0:
        return False

    try:
        async with page.expect_navigation(timeout=30000):
            await entry_button.first.click()
    except Exception:
        await entry_button.first.click()
        await page.wait_for_load_state("networkidle")
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


async def run(playwright: Playwright, username: str, password: str, year: str, address: str, output_path: Path, headed: bool, keep_pdf: bool) -> None:
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
        'extra_http_headers': {
            'accept-language': 'el-GR,el;q=0.9,en-US;q=0.8,en;q=0.7',
            'referer': 'https://www1.aade.gr/',
        },
        'accept_downloads': True,
    }
    if STORAGE_STATE_PATH.exists():
        context_args['storage_state'] = str(STORAGE_STATE_PATH)

    context = await browser.new_context(**context_args)
    page = await context.new_page()

    page1 = await _login_and_open_listing(page, username, password)
    await context.storage_state(path=str(STORAGE_STATE_PATH))

    clicked_enter = await click_etak_entry_button(page1)
    if not clicked_enter:
        await page1.goto(AADE_ETAK_URL)
        await page1.wait_for_load_state("networkidle")
        await page1.wait_for_timeout(2000)

    etak_info = await extract_etak_property_status(page1)
    matched_ataks, matched_rows = find_ataks_by_address(etak_info['propertyStatusGrids'], address)
    if not matched_ataks:
        raise RuntimeError(f"Could not find address '{address}' in ETΑΚ property status grids.")

    selected_year = await select_etak_year(page1, year)
    download_dir = output_path.parent / 'tmp_etak_download'
    pdf_path = await download_etak_pdf(page1, selected_year, download_dir)
    pdf_rows = extract_pdf_rows_for_ataks(pdf_path, matched_ataks)

    # Keep the ENFIA PDF when --keep-pdf was requested (the brain copies it
    # into the per-user/per-AFM folder for the UI). Previously this branch
    # had an OR that deleted the PDF whenever rows were matched — which is
    # exactly when we want to KEEP it for evidence.
    pdf_deleted = False
    if not keep_pdf:
        if pdf_path.exists():
            pdf_path.unlink()
        if download_dir.exists() and not any(download_dir.iterdir()):
            download_dir.rmdir()
        pdf_deleted = True

    result = {
        'username': username,
        'year': selected_year,
        'address': address,
        'matchedAtaks': matched_ataks,
        'matchedTableRows': matched_rows,
        'pdfPath': str(pdf_path),
        'pdfDeleted': pdf_deleted,
        'pdfMatchedRows': pdf_rows,
        'propertyStatus': etak_info,
    }

    with output_path.open('w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f'Wrote ETΑΚ JSON to: {output_path}')

    await context.close()
    await browser.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description='Extract AADE ETΑΚ property status data to JSON')
    parser.add_argument('--username', required=True, help='AADE username')
    parser.add_argument('--password', required=True, help='AADE password')
    parser.add_argument('--year', required=True, help='Year to select in ETΑΚ and print the PDF for')
    parser.add_argument('--address', required=True, help='Address to match in ETΑΚ Πίνακας 1 data')
    parser.add_argument('--output', default='extracted_etak_property_status.json', help='Output JSON file path')
    parser.add_argument('--headed', action='store_true', help='Run browser in headed mode')
    parser.add_argument('--keep-pdf', action='store_true', help='Keep downloaded PDF after extraction')
    args = parser.parse_args()

    async with async_playwright() as playwright:
        await run(playwright, args.username, args.password, args.year, args.address, Path(args.output), args.headed, args.keep_pdf)


if __name__ == '__main__':
    asyncio.run(main())
