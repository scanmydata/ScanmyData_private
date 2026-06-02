"""
Comprehensive Playwright test for all fixes.
Verifies: partial navigation buttons, AMKA overlay, credentials loading, Load button.
"""
import asyncio
import json
import sys
from pathlib import Path
from playwright.async_api import async_playwright

APP_URL = 'http://127.0.0.1:5001'
EMAIL = 'douradonis@hotmail.com'
PASSWORD = '12345678'
DEBUG_DIR = Path('test_screenshots')
DEBUG_DIR.mkdir(exist_ok=True)

RESULTS = {}


async def save_debug(page, name):
    try:
        (DEBUG_DIR / f"{name}.html").write_text(await page.content(), encoding='utf-8')
    except Exception:
        pass
    try:
        await page.screenshot(path=str(DEBUG_DIR / f"{name}.png"), full_page=True)
    except Exception:
        pass


def report(test_name, passed, detail=''):
    RESULTS[test_name] = {'passed': passed, 'detail': detail}
    status = '[PASS]' if passed else '[FAIL]'
    msg = f'  {status} {test_name}'
    if detail:
        msg += f': {detail}'
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('ascii', 'replace').decode('ascii'))


async def do_login(page):
    """Login and navigate to /groups, select tony, set ΛΟΥΓΑΡΗΣ as active"""
    # First logout to clear any locked session from previous test runs
    try:
        await page.goto(f'{APP_URL}/auth/api/logout')
        await page.wait_for_timeout(500)
    except Exception:
        pass
    try:
        await page.goto(f'{APP_URL}/logout')
        await page.wait_for_timeout(500)
    except Exception:
        pass

    await page.goto(f'{APP_URL}/firebase-auth/login')
    await page.wait_for_selector('input[name="email"]', timeout=10000)
    await page.fill('input[name="email"]', EMAIL)
    await page.fill('input[name="password"]', PASSWORD)

    await page.click('button[type="submit"]')
    await page.wait_for_load_state('domcontentloaded')
    await page.wait_for_timeout(3000)
    url = page.url
    try:
        print(f'    Login URL: {url}')
    except Exception:
        pass

    if '/groups' in url or 'groups' in url:
        # Select tony group
        await page.evaluate("""() => {
            const rows = Array.from(document.querySelectorAll('tr, li, [data-group], .group-item'));
            for (const row of rows) {
                const txt = row.textContent || '';
                if (txt.toLowerCase().includes('tony')) {
                    const link = row.querySelector('a') || (row.tagName === 'A' ? row : null);
                    if (link) { link.click(); return; }
                }
            }
            // Try all links
            const links = Array.from(document.querySelectorAll('a'));
            for (const l of links) {
                if ((l.textContent||'').trim().toLowerCase() === 'tony') { l.click(); return; }
            }
        }""")
        await page.wait_for_timeout(2000)

    # Set active client ΛΟΥΓΑΡΗΣ 036209456
    await page.goto(f'{APP_URL}/credentials')
    await page.wait_for_load_state('domcontentloaded')
    await page.wait_for_timeout(1000)
    activated = await page.evaluate("""() => {
        const afm = '036209456';
        const rows = Array.from(document.querySelectorAll('tr'));
        for (const row of rows) {
            if (row.textContent.includes(afm)) {
                const btn = row.querySelector('button');
                if (btn) { btn.click(); return true; }
            }
        }
        return false;
    }""")
    await page.wait_for_timeout(1500)
    try:
        print(f'    Client activated: {activated}, URL: {page.url}')
    except Exception:
        pass
    return '/login' not in page.url


async def test_partial_nav_buttons(page):
    """Test that brain tab buttons work after partial navigation"""
    try:
        print('\n[TEST] Partial navigation - buttons')
        await page.goto(f'{APP_URL}/e3_check')
        await page.wait_for_load_state('domcontentloaded')
        await page.wait_for_timeout(2000)

        # Check key elements exist
        brain_exists = await page.locator('#brainTabSingleBtn').count() > 0
        report('brain_tab_exists_full_load', brain_exists)
        if not brain_exists:
            await save_debug(page, '03_e3check_no_brain')
            return

        # Partial nav away then back
        await page.evaluate('window.__partialNavigateTo && window.__partialNavigateTo("/credentials")')
        await page.wait_for_timeout(1500)
        await page.evaluate('window.__partialNavigateTo && window.__partialNavigateTo("/e3_check")')
        await page.wait_for_timeout(2000)
        await save_debug(page, '03_e3check_partial_nav')

        # Click analyticTab button
        analytic_btn = page.locator('#analyticTabBtn')
        if await analytic_btn.count() > 0:
            await analytic_btn.click()
            await page.wait_for_timeout(500)
            analytic_visible = await page.evaluate('''() => {
                const el = document.getElementById("analyticTab");
                return el ? !el.classList.contains("hidden") : false;
            }''')
            report('partial_nav_analyticTab_click', analytic_visible)
        else:
            report('partial_nav_analyticTab_click', False, 'button not found')
            return

        # Click Μαζικός brain tab
        await page.locator('#brainTabBulkBtn').click()
        await page.wait_for_timeout(300)
        bulk_visible = await page.evaluate('''() => {
            const el = document.getElementById("brainTabBulk");
            return el ? !el.classList.contains("hidden") : false;
        }''')
        report('partial_nav_brainTabBulk_click', bulk_visible)
        await save_debug(page, '04_bulk_tab')

        # Click Αποθηκευμένα
        await page.locator('#brainTabSavedBtn').click()
        await page.wait_for_timeout(1500)
        saved_visible = await page.evaluate('''() => {
            const el = document.getElementById("brainTabSaved");
            return el ? !el.classList.contains("hidden") : false;
        }''')
        report('partial_nav_brainTabSaved_click', saved_visible)
        await save_debug(page, '05_saved_tab')
    except Exception as e:
        report('partial_nav_test', False, str(e)[:80])


async def test_overlay_and_functions(page):
    """Test wait overlay structure and AMKA function"""
    try:
        print('\n[TEST] Wait overlay + AMKA function')
        await page.goto(f'{APP_URL}/e3_check')
        await page.wait_for_load_state('domcontentloaded')
        await page.wait_for_timeout(1500)

        overlay_exists = await page.evaluate('() => Boolean(document.getElementById("waitOverlay"))')
        report('wait_overlay_exists', overlay_exists)

        overlay_fn = await page.evaluate('() => typeof showGlobalWaitOverlay === "function"')
        report('showGlobalWaitOverlay_fn_available', overlay_fn)

        # Verify fetchIndividualAmka uses showGlobalWaitOverlay
        amka_uses_overlay = await page.evaluate('''() => {
            if (typeof fetchIndividualAmka !== "function") return false;
            return fetchIndividualAmka.toString().includes("showGlobalWaitOverlay");
        }''')
        report('fetchIndividualAmka_shows_overlay', amka_uses_overlay)

        # Verify fetchMemberAmka uses showGlobalWaitOverlay
        member_amka_overlay = await page.evaluate('''() => {
            if (typeof fetchMemberAmka !== "function") return false;
            return fetchMemberAmka.toString().includes("showGlobalWaitOverlay");
        }''')
        report('fetchMemberAmka_shows_overlay', member_amka_overlay)

        await save_debug(page, '06_overlay_test')
    except Exception as e:
        report('overlay_test', False, str(e)[:80])


async def test_credentials_load(page):
    """Test that selecting a client from dropdown loads credentials"""
    try:
        print('\n[TEST] Client select loads credentials')
        await page.goto(f'{APP_URL}/e3_check')
        await page.wait_for_load_state('domcontentloaded')
        await page.wait_for_timeout(2000)

        # Go to analyticTab
        btn = page.locator('#analyticTabBtn')
        if await btn.count() > 0:
            await btn.click()
            await page.wait_for_timeout(500)

        # Wait for active client select
        await page.wait_for_timeout(2000)
        select_exists = await page.evaluate('() => Boolean(document.getElementById("brainActiveClientSelect"))')
        report('active_client_select_exists', select_exists)

        if select_exists:
            options_count = await page.evaluate('''() => {
                const s = document.getElementById("brainActiveClientSelect");
                return s ? s.options.length : 0;
            }''')
            report('active_client_select_has_options', options_count > 1, f'{options_count} options')

            if options_count > 1:
                first_afm = await page.evaluate('''() => {
                    const s = document.getElementById("brainActiveClientSelect");
                    return s && s.options.length > 1 ? s.options[1].value : "";
                }''')
                if first_afm:
                    await page.select_option('#brainActiveClientSelect', first_afm)
                    await page.wait_for_timeout(1500)
                    afm_val = await page.evaluate('() => { const e=document.getElementById("brainAfm"); return e?e.value:""; }')
                    report('select_fills_afm_field', bool(afm_val), f'afm={afm_val}')

        await save_debug(page, '07_cred_load_test')

        # Test fillBrainSingleClientFromSaved also loads from creds cache
        fills_from_cache = await page.evaluate('''() => {
            if (typeof fillBrainSingleClientFromSaved !== "function") return false;
            const src = fillBrainSingleClientFromSaved.toString();
            return src.includes("E3_BRAIN_CREDS_CACHE");
        }''')
        report('fillBrainSingleClientFromSaved_uses_cache', fills_from_cache)
    except Exception as e:
        report('credentials_load_test', False, str(e)[:80])


async def test_load_button_in_saved_tab(page):
    """Test Φόρτωση (Load) button in saved credentials tab"""
    try:
        print('\n[TEST] Load button in saved credentials tab')
        await page.goto(f'{APP_URL}/e3_check')
        await page.wait_for_load_state('domcontentloaded')
        await page.wait_for_timeout(2000)

        # Go to analyticTab
        btn = page.locator('#analyticTabBtn')
        if await btn.count() > 0:
            await btn.click()
            await page.wait_for_timeout(500)

        # Click Αποθηκευμένα tab
        saved_btn = page.locator('#brainTabSavedBtn')
        if await saved_btn.count() > 0:
            await saved_btn.click()
            await page.wait_for_timeout(2000)
            await save_debug(page, '08_saved_tab_creds')

            # Check for Load buttons
            load_count = await page.locator('button:has-text("Φόρτωση")').count()
            report('saved_tab_has_load_buttons', load_count > 0, f'{load_count} load buttons')

            # Check that loadBrainCredToForm function exists
            fn_exists = await page.evaluate('() => typeof loadBrainCredToForm === "function"')
            report('loadBrainCredToForm_fn_exists', fn_exists)

            if load_count > 0:
                await page.locator('button:has-text("Φόρτωση")').first.click()
                await page.wait_for_timeout(1000)
                await save_debug(page, '09_after_load_click')
                afm_val = await page.evaluate('() => { const e=document.getElementById("brainAfm"); return e?e.value:""; }')
                report('load_btn_fills_afm', bool(afm_val), f'afm={afm_val}')
                single_visible = await page.evaluate('''() => {
                    const el = document.getElementById("brainTabSingle");
                    return el ? !el.classList.contains("hidden") : false;
                }''')
                report('load_btn_switches_to_single_tab', single_visible)
            else:
                report('load_btn_fills_afm', None, 'no load buttons in saved tab (no saved credentials?)')
        else:
            report('saved_tab_btn_exists', False)
    except Exception as e:
        report('load_button_test', False, str(e)[:80])


async def main():
    print('=== Comprehensive E3 Check Test ===\n')

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, slow_mo=100)
        context = await browser.new_context(viewport={'width': 1400, 'height': 900})
        page = await context.new_page()

        js_errors = []
        page.on('pageerror', lambda err: js_errors.append(str(err)[:100]))

        try:
            print('Setting up (login)...')
            logged_in = await do_login(page)
            report('login_and_setup', logged_in)

            if logged_in:
                await test_partial_nav_buttons(page)
                await test_overlay_and_functions(page)
                await test_credentials_load(page)
                await test_load_button_in_saved_tab(page)
            else:
                print('  Login/setup failed - skipping tests')

        except Exception as e:
            print(f'\nFATAL ERROR: {e}')
            import traceback
            traceback.print_exc()
            await save_debug(page, '99_fatal_error')
        finally:
            await page.wait_for_timeout(500)
            await browser.close()

    print('\n=== RESULTS ===')
    passed = sum(1 for v in RESULTS.values() if v['passed'] is True)
    failed = sum(1 for v in RESULTS.values() if v['passed'] is False)
    total = len(RESULTS)
    for name, result in RESULTS.items():
        p = result['passed']
        status = '[PASS]' if p is True else ('[SKIP]' if p is None else '[FAIL]')
        detail = f": {result['detail']}" if result['detail'] else ''
        line = f'  {status} {name}{detail}'
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode('ascii', 'replace').decode('ascii'))

    if js_errors:
        print(f'\n=== JS Errors ({len(js_errors)}) ===')
        for e in js_errors[:5]:
            print(f'  {e}')

    print(f'\nTotal: {passed}/{total} passed, {failed} failed')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
