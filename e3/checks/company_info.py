import argparse
import asyncio
import json
import re
from pathlib import Path
from playwright.async_api import Playwright, async_playwright
import tempfile
import traceback
import shutil

AADE_ENTRY_URL = "https://www1.aade.gr/sgsisapps/plcs"
AADE_COMREG_URL = "https://www1.aade.gr/saadeapps3/comregistry/#!/arxiki"
AADE_REGISTRY_URL = "https://www1.aade.gr/taxisnet/info/protected/displayRegistryInfo.htm"
STORAGE_STATE_PATH = Path("misth_storage_state.json")
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

# When False, never navigate to the public AADE "misthotiria" landing page.
# This prevents accidental navigation to the leases flow during quick registry tests.
ALLOW_MISHT_NAVIGATION = False


def login_and_open_listing(page, username: str, password: str):
    return _login_and_open_listing(page, username, password)


async def _login_and_open_listing(page, username: str, password: str):
    # Prefer starting at the registry URL which redirects to login (faster flow).
    await page.goto(AADE_REGISTRY_URL)
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(2000)

    # If the registry content is already available, return the current page.
    if await page.locator('td.contenttd').count() > 0 and await page.locator('input[name="button2"]').count() > 0:
        return page

    # If an inline login form exists on this page, use it.
    if await page.locator('input[name="username"]').count() > 0 and await page.locator('input[name="password"]').count() > 0:
        await page.locator('input[name="username"]').fill(username)
        await page.locator('input[name="password"]').fill(password)
        login_button = page.locator('button[name="btn_login"]').first
        if await login_button.count() == 0:
            login_button = page.locator('button:has-text("Συνδεση"), button:has-text("ΣΥΝΔΕΣΗ"), input[type=submit]').first
        if await login_button.count() == 0:
            error_html = await dump_page_html(page, 'login_button_missing')
            raise RuntimeError(f"Could not find the GSIS login button on inline login. Debug: {error_html}")

        await login_button.click()
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(3000)
        await _raise_if_oam_error(page, 'login_error_registry_inline')

        # Some flows show an Επιλογή button after authentication
        entry_button = page.locator('input[name="button1"][value="Επιλογή"], input[type=button][value="Επιλογή"], input[type=button][name="button1"]')
        if await entry_button.count() > 0:
            await entry_button.first.wait_for(state="visible", timeout=20000)
            await entry_button.first.click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(3000)

        if await page.locator('input[name="button2"]').count() == 0 and await page.locator('td.contenttd').count() == 0:
            # Inline auth can fail due stale/invalid session; continue fallback login paths.
            await dump_page_html(page, 'lease_list_missing_after_inline')
        else:
            return page

    # If no inline form, try clicking the public entry link which may open a popup.
    entry_link = page.locator('a:has-text("Είσοδος στην Εφαρμογή")')
    if await entry_link.count() == 0:
        entry_link = page.locator(f'a[href="{AADE_ENTRY_URL}"]')

    # If the registry page already has the listing, return it.
    if await page.locator('input[name="button2"]').count() > 0 or await page.locator('td.contenttd').count() > 0:
        return page

    # Fallback: navigate to the misth landing page and click entry link (original flow)
    if await entry_link.count() == 0:
        if not ALLOW_MISHT_NAVIGATION:
            raise RuntimeError("Navigation to AADE misth landing page is disabled (ALLOW_MISHT_NAVIGATION=False)")
        await page.goto(AADE_MISHT_URL)
        await page.wait_for_load_state("networkidle")
        entry_link = page.locator('a:has-text("Είσοδος στην Εφαρμογή")')
        if await entry_link.count() == 0:
            entry_link = page.locator(f'a[href="{AADE_ENTRY_URL}"]')
        if await entry_link.count() == 0:
            # Newer page variants may omit the public entry link; try direct GSIS entry page.
            await page.goto(AADE_ENTRY_URL)
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)

            if await page.locator('input[name="username"]').count() > 0 and await page.locator('input[name="password"]').count() > 0:
                await page.locator('input[name="username"]').fill(username)
                await page.locator('input[name="password"]').fill(password)
                login_button = page.locator('button[name="btn_login"]').first
                if await login_button.count() == 0:
                    login_button = page.locator('button:has-text("Συνδεση"), button:has-text("ΣΥΝΔΕΣΗ"), input[type=submit], button[type=submit]').first
                if await login_button.count() == 0:
                    error_html = await dump_page_html(page, 'entry_link_missing_login_button_missing')
                    raise RuntimeError(f"Could not find GSIS login button on direct entry page. Debug: {error_html}")

                await login_button.click()
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(3000)
                await _raise_if_oam_error(page, 'login_error_direct_entry_inline')

                entry_button = page.locator('input[name="button1"][value="Επιλογή"], input[type=button][value="Επιλογή"], input[type=button][name="button1"]')
                if await entry_button.count() > 0:
                    await entry_button.first.wait_for(state="visible", timeout=20000)
                    await entry_button.first.click()
                    await page.wait_for_load_state("networkidle")
                    await page.wait_for_timeout(3000)

                if await page.locator('input[name="button2"]').count() > 0 or await page.locator('td.contenttd').count() > 0:
                    return page

            error_html = await dump_page_html(page, 'entry_link_missing')
            raise RuntimeError(f"Could not find public entry link on AADE misth page and direct GSIS entry did not reach listing. Debug: {error_html}")

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
            error_html = await dump_page_html(popup_page, 'login_button_missing_popup')
            raise RuntimeError(f"Could not find the GSIS login button in popup. Debug: {error_html}")

        await login_button.click()
        await popup_page.wait_for_load_state("networkidle")
        await popup_page.wait_for_timeout(3000)
        await _raise_if_oam_error(popup_page, 'login_error_popup')
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

    if await popup_page.locator('input[name="button2"]').count() == 0 and await popup_page.locator('td.contenttd').count() == 0:
        error_html = await dump_page_html(popup_page, 'lease_list_missing')
        raise RuntimeError(f"Lease list page did not load or no lease entries found. Debug: {error_html}")

    return popup_page


async def extract_registry_info(page):
    content_td = page.locator('td.contenttd')
    tables = None
    if await content_td.count() > 0:
        tables = content_td.locator('table')
        if await tables.count() == 0:
            tables = None

    # Fallback: if there is no 'td.contenttd' container (site variant), scan all tables
    if not tables:
        all_tables = page.locator('table')
        candidate_tables = []
        for idx in range(await all_tables.count()):
            t = all_tables.nth(idx)
            # check if table contains header tokens typical for registry/related-person tables
            texts = []
            try:
                cell_texts = await t.locator('th,td').all_inner_texts()
                texts = [x.strip().lower() for x in cell_texts if x and x.strip()]
            except Exception:
                texts = []
            joined = ' '.join(texts)
            if any(tok in joined for tok in ['αφμ', 'σχετι', 'επων', 'ειδος σχεσ', 'ειδος σχεση', 'ημ/νια', 'ημ/νια έναρξης']):
                candidate_tables.append({'table': t, 'index': idx})

        if not candidate_tables:
            error_html = await dump_page_html(page, 'registry_tables_missing_variant')
            raise RuntimeError(f"Could not find registry table(s) on page. Debug: {error_html}")

        # create a lightweight locator-like wrapper object by indexing into all_tables when extracting
        class _TableWrapper:
            def __init__(self, base, idx):
                self._base = base
                self._idx = idx
            async def get_attribute(self, name):
                return await self._base.nth(self._idx).get_attribute(name)
            async def evaluate(self, script):
                return await self._base.nth(self._idx).evaluate(script)
            def locator(self, sel):
                return self._base.nth(self._idx).locator(sel)

        extracted_tables_list = []
        for entry in candidate_tables:
            tw = _TableWrapper(all_tables, entry['index'])
            # collect rows below
            rows = []
            row_locator = tw.locator('tr')
            for j in range(await row_locator.count()):
                cell_texts = await row_locator.nth(j).locator('th,td').all_inner_texts()
                rows.append([text.strip() for text in cell_texts if text.strip()])
            table_html = await tw.evaluate('(node) => node.outerHTML')
            extracted_tables_list.append({
                'tableIndex': entry['index'],
                'tableClasses': await tw.get_attribute('class'),
                'tableId': await tw.get_attribute('id'),
                'rows': rows,
                'html': table_html,
            })

        return {
            'pageTitle': await page.title(),
            'url': page.url,
            'registryTables': extracted_tables_list,
        }

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


# Lease/μισθωτήρια navigation and detail extraction removed to streamline registry-only extraction


USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
DEFAULT_HEADERS = {
    'accept-language': 'el-GR,el;q=0.9,en-US;q=0.8,en;q=0.7',
    'referer': 'https://www1.aade.gr/',
}
USER_DATA_DIR = Path('misth_user_data')


async def dump_page_html(page, suffix: str) -> Path:
    """Write page HTML for post-mortem debugging — opt-in only.

    Mirrors the gate in misth.py / e9.py: a third script was still
    littering misth_debug_*.html into the project root because its own
    dump function wasn't tied to the same env var.
    """
    import os as _os
    if not _os.getenv('E3_DEBUG_HTML'):
        return Path('(debug-html-disabled)')
    path = Path(f'misth_debug_{suffix}.html')
    html = await page.content()
    path.write_text(html, encoding='utf-8')
    return path


def _oam_message_for_code(code: str) -> str:
    if code == 'OAM-6':
        return 'GSIS login failed due to session limit (too many active sessions).'
    if code == 'OAM-5':
        return 'GSIS login failed because the account appears locked or disabled.'
    if code == 'OAM-2':
        return 'GSIS login failed due to invalid credentials.'
    return 'GSIS login failed with an Oracle Access Manager error.'


async def _detect_oam_error(page):
    url = page.url or ''
    content = ''
    try:
        content = await page.content()
    except Exception:
        content = ''

    match = re.search(r'OAM-\d+', f'{url}\n{content}', flags=re.IGNORECASE)
    if not match:
        return None

    code = match.group(0).upper()
    return {
        'code': code,
        'flag': f'OAM_FLAG:{code.replace("-", "_")}',
        'message': _oam_message_for_code(code),
        'url': url,
    }


async def _raise_if_oam_error(page, debug_suffix: str):
    oam = await _detect_oam_error(page)
    if not oam:
        return
    error_html = await dump_page_html(page, debug_suffix)
    raise RuntimeError(
        f"{oam['message']} [{oam['flag']}] code={oam['code']} url={oam['url']}. Debug: {error_html}"
    )


async def ensure_logged_in_from_registry(page, username: str, password: str):
    """Ensure we are logged in starting from the registry URL.

    Handles inline login on the registry page or the popup-based GSIS login.
    Returns the page object that contains the registry/listing (may be a popup page).
    """
    await page.goto(AADE_REGISTRY_URL)
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(2000)

    # If registry/listing content already present, return current page
    if await page.locator('td.contenttd').count() > 0 and await page.locator('input[name="button2"]').count() > 0:
        return page

    # Inline login on the same page
    if await page.locator('input[name="username"]').count() > 0 and await page.locator('input[name="password"]').count() > 0:
        await page.locator('input[name="username"]').fill(username)
        await page.locator('input[name="password"]').fill(password)
        login_button = page.locator('button[name="btn_login"]').first
        if await login_button.count() == 0:
            login_button = page.locator('button:has-text("Συνδεση"), button:has-text("ΣΥΝΔΕΣΗ"), input[type=submit], button[type=submit]').first
        if await login_button.count() == 0:
            error_html = await dump_page_html(page, 'login_button_missing_registry')
            raise RuntimeError(f"Could not find GSIS login button on registry login page. Debug: {error_html}")

        await login_button.click()
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(3000)

        # Some flows ask for an Επιλογή button after auth
        entry_button = page.locator('input[name="button1"][value="Επιλογή"], input[type=button][value="Επιλογή"], input[type=button][name="button1"]')
        if await entry_button.count() > 0:
            await entry_button.first.wait_for(state="visible", timeout=20000)
            await entry_button.first.click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)

        if await page.locator('td.contenttd').count() > 0 or await page.locator('input[name="button2"]').count() > 0:
            return page

        if await page.locator('input[name="username"]').count() > 0 and await page.locator('input[name="password"]').count() > 0:
            # Inline auth may fail due stale cookies/session; continue to fallback paths.
            await dump_page_html(page, 'login_failed_registry')

        # If a popup was opened by the login, try to find it
        pages = page.context.pages
        for p in pages:
            try:
                if p != page and p.url and 'about:blank' not in p.url:
                    await p.wait_for_load_state('networkidle')
                    if await p.locator('td.contenttd').count() > 0 or await p.locator('input[name="button2"]').count() > 0:
                        return p
            except Exception:
                continue

    # Try to open the public entry link which may spawn a login popup
    entry_link = page.locator('a:has-text("Είσοδος στην Εφαρμογή")')
    if await entry_link.count() == 0:
        entry_link = page.locator(f'a[href="{AADE_ENTRY_URL}"]')

    if await entry_link.count() > 0:
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
                error_html = await dump_page_html(popup_page, 'login_button_missing_popup')
                raise RuntimeError(f"Could not find the GSIS login button in popup. Debug: {error_html}")
            await login_button.click()
            await popup_page.wait_for_load_state("networkidle")
            await popup_page.wait_for_timeout(3000)
            await _raise_if_oam_error(popup_page, 'login_error_registry_popup')

        entry_button = popup_page.locator('input[name="button1"][value="Επιλογή"], input[type=button][value="Επιλογή"], input[type=button][name="button1"]')
        if await entry_button.count() > 0:
            await entry_button.first.wait_for(state="visible", timeout=20000)
            await entry_button.first.click()
            await popup_page.wait_for_load_state("networkidle")
            await popup_page.wait_for_timeout(3000)

        if await popup_page.locator('td.contenttd').count() == 0 and await popup_page.locator('input[name="button2"]').count() == 0:
            error_html = await dump_page_html(popup_page, 'lease_list_missing_popup')
            raise RuntimeError(f"Lease list page did not load or no lease entries found in popup. Debug: {error_html}")

        return popup_page

    # Fallback: try direct navigation to the AADE entry portal before using the misth landing page
    await page.goto(AADE_ENTRY_URL)
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(2000)

    # If this page exposes an inline login form, use it first (avoids popup/misth flow)
    if await page.locator('input[name="username"]').count() > 0 and await page.locator('input[name="password"]').count() > 0:
        await page.locator('input[name="username"]').fill(username)
        await page.locator('input[name="password"]').fill(password)
        login_button = page.locator('button[name="btn_login"]').first
        if await login_button.count() == 0:
            login_button = page.locator('button:has-text("Συνδεση"), button:has-text("ΣΥΝΔΕΣΗ"), input[type=submit], button[type=submit]').first
        if await login_button.count() == 0:
            error_html = await dump_page_html(page, 'login_button_missing_entry_portal')
            raise RuntimeError(f"Could not find the GSIS login button on AADE entry portal. Debug: {error_html}")

        await login_button.click()
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(3000)
        await _raise_if_oam_error(page, 'login_error_entry_portal_inline')

        # Some flows show an Επιλογή button after authentication
        entry_button = page.locator('input[name="button1"][value="Επιλογή"], input[type=button][value="Επιλογή"], input[type=button][name="button1"]')
        if await entry_button.count() > 0:
            await entry_button.first.wait_for(state="visible", timeout=20000)
            await entry_button.first.click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)

        if await page.locator('td.contenttd').count() > 0 or await page.locator('input[name="button2"]').count() > 0:
            return page

        # If a popup was opened by the login, try to find it
        pages = page.context.pages
        for p in pages:
            try:
                if p != page and p.url and 'about:blank' not in p.url:
                    await p.wait_for_load_state('networkidle')
                    if await p.locator('td.contenttd').count() > 0 or await p.locator('input[name="button2"]').count() > 0:
                        return p
            except Exception:
                continue

    # If this page exposes a public entry link that spawns the GSIS popup, use it
    entry_link = page.locator('a:has-text("Είσοδος στην Εφαρμογή")')
    if await entry_link.count() == 0:
        entry_link = page.locator(f'a[href="{AADE_ENTRY_URL}"]')

    if await entry_link.count() > 0:
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
                error_html = await dump_page_html(popup_page, 'login_button_missing_popup_fallback')
                raise RuntimeError(f"Could not find the GSIS login button in popup. Debug: {error_html}")

            await login_button.click()
            await popup_page.wait_for_load_state("networkidle")
            await popup_page.wait_for_timeout(3000)
            await _raise_if_oam_error(popup_page, 'login_error_entry_portal_popup')

        entry_button = popup_page.locator('input[name="button1"][value="Επιλογή"], input[type=button][value="Επιλογή"], input[type=button][name="button1"]')
        if await entry_button.count() > 0:
            await entry_button.first.wait_for(state="visible", timeout=20000)
            await entry_button.first.click()
            await popup_page.wait_for_load_state("networkidle")
            await popup_page.wait_for_timeout(3000)

        if await popup_page.locator('td.contenttd').count() == 0 and await popup_page.locator('input[name="button2"]').count() == 0:
            error_html = await dump_page_html(popup_page, 'lease_list_missing_popup_fallback')
            raise RuntimeError(f"Lease list page did not load or no lease entries found in popup. Debug: {error_html}")

        return popup_page

    # If portal entry provided no link and misth navigation disabled, fail fast
    if not ALLOW_MISHT_NAVIGATION:
        error_html = await dump_page_html(page, 'entry_link_missing_direct_and_misth_disabled')
        raise RuntimeError(f"Could not find public entry link on AADE entry portal and misth navigation is disabled. Debug: {error_html}")

    # Otherwise fallback to the original misth landing page flow
    await page.goto(AADE_MISHT_URL)
    await page.wait_for_load_state("networkidle")
    return await _login_and_open_listing(page, username, password)


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
    storage_exists = STORAGE_STATE_PATH.exists()
    attempts = [True, False] if storage_exists else [False]
    last_error = None

    try:
        for use_storage_state in attempts:
            context_args = {
                'user_agent': USER_AGENT,
                'locale': 'el-GR',
                'viewport': {'width': 1280, 'height': 1024},
                'extra_http_headers': DEFAULT_HEADERS,
            }
            if use_storage_state and STORAGE_STATE_PATH.exists():
                context_args['storage_state'] = str(STORAGE_STATE_PATH)

            context = await browser.new_context(**context_args)
            try:
                page = await context.new_page()

                # Registry-only extraction: login and capture the TAXIS registry tables
                page1 = await ensure_logged_in_from_registry(page, username, password)
                await context.storage_state(path=str(STORAGE_STATE_PATH))

                await page1.goto(AADE_REGISTRY_URL)
                await page1.wait_for_load_state("networkidle")
                await page1.wait_for_timeout(2000)
                registry_info = await extract_registry_info(page1)

                registry_output_path = output_path.parent / 'extracted_registry.json'
                with registry_output_path.open('w', encoding='utf-8') as f:
                    json.dump(registry_info, f, ensure_ascii=False, indent=2)

                print(f'Wrote registry JSON to: {registry_output_path}')
                return
            except Exception as e:
                last_error = e
                if use_storage_state and STORAGE_STATE_PATH.exists():
                    print('Stored session appears stale; retrying once with a clean browser session...')
                    try:
                        STORAGE_STATE_PATH.unlink()
                    except Exception:
                        pass
                else:
                    raise
            finally:
                await context.close()
    finally:
        await browser.close()

    if last_error is not None:
        raise last_error


async def main() -> None:
    parser = argparse.ArgumentParser(description='Extract AADE misth and TAXIS registry data to JSON')
    parser.add_argument('--username', default='802576637', help='AADE username')
    parser.add_argument('--password', default='Tv802576637!', help='AADE password')
    parser.add_argument('--output', default='extracted_misth.json', help='Base output file path for JSON exports')
    parser.add_argument('--headed', action='store_true', help='Run browser in headed mode')
    args = parser.parse_args()

    async with async_playwright() as playwright:
        await run(playwright, args.username, args.password, Path(args.output), args.headed)


if __name__ == '__main__':
    asyncio.run(main())


def fetch_registry(username: str, password: str, headed: bool = False, keep_tmpdir: bool = False) -> dict:
    """Synchronous helper to run the Playwright registry extraction and return the parsed registry JSON.

    Returns a dict: {ok: bool, registry: dict|None, error: str|None, tmpdir: str}
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="company_info_"))
    output_path = tmpdir / "extracted_misth.json"
    try:
        async def _inner():
            async with async_playwright() as playwright:
                await run(playwright, username, password, output_path, headed)

        asyncio.run(_inner())

        registry_file = tmpdir / "extracted_registry.json"
        if registry_file.exists():
            try:
                with registry_file.open("r", encoding="utf-8") as f:
                    registry = json.load(f)
            except Exception as e:
                return {"ok": False, "error": f"Failed to read registry JSON: {e}", "tmpdir": str(tmpdir)}

            return {"ok": True, "registry": registry, "tmpdir": str(tmpdir)}

        # If registry file wasn't created, attempt to read misth output as fallback
        if output_path.exists():
            try:
                with output_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                return {"ok": True, "registry": data, "tmpdir": str(tmpdir)}
            except Exception as e:
                return {"ok": False, "error": f"No registry output and failed reading misth output: {e}", "tmpdir": str(tmpdir)}

        return {"ok": False, "error": "No output produced by Playwright run", "tmpdir": str(tmpdir)}

    except Exception as e:
        return {"ok": False, "error": str(e), "traceback": traceback.format_exc(), "tmpdir": str(tmpdir)}
    finally:
        # Keep the tmpdir for debugging unless explicitly requested to remove it
        if not keep_tmpdir:
            try:
                shutil.rmtree(tmpdir)
            except Exception:
                pass
