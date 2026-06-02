"""
Diagnostic test: partial navigation on e3_check page.
Tests whether tab buttons work after partial navigation.
"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

APP_URL = 'http://127.0.0.1:5001'
EMAIL = 'douradonis@hotmail.com'
PASSWORD = '12345678'
DEBUG_DIR = Path('diag_screenshots')
DEBUG_DIR.mkdir(exist_ok=True)


async def save_debug(page, name):
    try:
        (DEBUG_DIR / f"{name}.html").write_text(await page.content(), encoding='utf-8')
    except Exception:
        pass
    try:
        await page.screenshot(path=str(DEBUG_DIR / f"{name}.png"), full_page=True)
    except Exception:
        pass


async def login(page):
    await page.goto(f'{APP_URL}/firebase-auth/login')
    await page.wait_for_load_state('domcontentloaded')
    await page.wait_for_timeout(800)
    await save_debug(page, '01_login_page')
    await page.fill('input[name="email"]', EMAIL)
    await page.fill('input[name="password"]', PASSWORD)
    await page.click('button[type="submit"]')
    await page.wait_for_load_state('domcontentloaded')
    await page.wait_for_timeout(3000)
    await save_debug(page, '02_after_login')
    print(f'After login URL: {page.url}')
    # Handle group selection if needed
    if '/groups' in page.url or 'group' in (await page.title()).lower():
        grp_link = page.locator('a[href*="select_group"], a[href*="/groups/"], .group-card a, tr td a').first
        if await grp_link.count() > 0:
            await grp_link.click()
            await page.wait_for_timeout(1500)
            await save_debug(page, '02b_after_group')


async def navigate_to_group(page):
    """Select a group if needed"""
    try:
        if '/groups' in page.url or 'group' in page.url.lower():
            return
        # look for group selection link
        grp_link = page.locator('a[href*="/groups"], a[href*="group"]').first
        if await grp_link.count() > 0:
            await grp_link.click()
            await page.wait_for_load_state('networkidle')
            await save_debug(page, '03_groups_page')
            # select first group
            group_btn = page.locator('.group-card, a[href*="/select_group"], button:has-text("Επιλογή")').first
            if await group_btn.count() > 0:
                await group_btn.click()
                await page.wait_for_load_state('networkidle')
    except Exception as e:
        print(f'Group nav warning: {e}')


async def check_buttons_work(page, step_name):
    """Check if brain tab buttons exist and have listeners (by clicking them)"""
    results = {}

    # Check if buttons exist
    for btn_id in ['brainTabSingleBtn', 'brainTabBulkBtn', 'brainTabSavedBtn', 'quickCheckTabBtn', 'analyticTabBtn']:
        count = await page.locator(f'#{btn_id}').count()
        results[f'{btn_id}_exists'] = count > 0

    # Navigate to analyticTab first
    analytic_btn = page.locator('#analyticTabBtn')
    if await analytic_btn.count() > 0:
        await analytic_btn.click()
        await page.wait_for_timeout(500)
        await save_debug(page, f'{step_name}_after_analytic_click')

        # Check if analyticTab is now visible (not hidden)
        analytic_tab_visible = await page.evaluate('''() => {
            const el = document.getElementById('analyticTab');
            return el ? !el.classList.contains('hidden') : false;
        }''')
        results['analyticTab_visible_after_click'] = analytic_tab_visible

        # Now click Μαζικός tab
        bulk_btn = page.locator('#brainTabBulkBtn')
        if await bulk_btn.count() > 0:
            await bulk_btn.click()
            await page.wait_for_timeout(300)
            await save_debug(page, f'{step_name}_after_bulk_click')

            # Check if brainTabBulk is now visible
            bulk_tab_visible = await page.evaluate('''() => {
                const el = document.getElementById('brainTabBulk');
                return el ? !el.classList.contains('hidden') : false;
            }''')
            results['brainTabBulk_visible_after_click'] = bulk_tab_visible

            # Check active class on button
            bulk_btn_active = await page.evaluate('''() => {
                const el = document.getElementById('brainTabBulkBtn');
                return el ? el.classList.contains('active') : false;
            }''')
            results['brainTabBulkBtn_active_after_click'] = bulk_btn_active
    else:
        results['analyticTabBtn_not_found'] = True

    print(f'\n=== {step_name} ===')
    for k, v in results.items():
        status = '✓' if v else '✗'
        print(f'  [{("OK" if v else "FAIL")}] {k}: {v}')

    return results


async def get_js_errors(page):
    """Collect JS console errors"""
    errors = []
    page.on('console', lambda msg: errors.append(f'[{msg.type}] {msg.text}') if msg.type in ('error', 'warning') else None)
    return errors


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, slow_mo=300)
        context = await browser.new_context(viewport={'width': 1400, 'height': 900})
        page = await context.new_page()

        js_errors = []
        page.on('console', lambda msg: js_errors.append(f'[{msg.type}] {msg.text}') if msg.type in ('error',) else None)
        page.on('pageerror', lambda err: js_errors.append(f'[PAGEERROR] {err}'))

        # Step 1: Login
        print('Logging in...')
        await login(page)
        await navigate_to_group(page)

        # Step 2: Full page load to e3_check
        print('\nNavigating to e3_check via FULL page load...')
        await page.goto(f'{APP_URL}/e3_check')
        await page.wait_for_load_state('domcontentloaded')
        await page.wait_for_timeout(1500)
        await save_debug(page, '04_e3check_full_load')

        # Step 3: Check buttons after full load
        await check_buttons_work(page, '05_full_load')

        # Reset tab back to quickCheckTab
        qbtn = page.locator('#quickCheckTabBtn')
        if await qbtn.count() > 0:
            await qbtn.click()
            await page.wait_for_timeout(300)

        # Step 4: Partial navigate away to home
        print('\nPartial navigating away...')
        # Force partial navigation away via JS
        await page.evaluate('window.__partialNavigateTo ? window.__partialNavigateTo("/credentials") : (window.location.href = "/credentials")')
        await page.wait_for_timeout(1500)
        await save_debug(page, '06_after_nav_away')
        print(f'After nav away URL: {page.url}')

        # Step 5: Partial navigate back to e3_check
        print('\nPartial navigating BACK to e3_check...')
        await page.evaluate('window.__partialNavigateTo ? window.__partialNavigateTo("/e3_check") : (window.location.href = "/e3_check")')
        await page.wait_for_timeout(2000)
        await save_debug(page, '07_e3check_partial_nav')
        print(f'After partial nav URL: {page.url}')

        # Step 6: Check buttons after partial nav
        results = await check_buttons_work(page, '08_partial_nav')

        # Print JS errors
        if js_errors:
            print('\n=== JS Errors ===')
            for e in js_errors:
                print(f'  {e}')

        # Summary
        print('\n=== SUMMARY ===')
        brain_bulk_works = results.get('brainTabBulk_visible_after_click', False)
        analytic_shows = results.get('analyticTab_visible_after_click', False)
        print(f'  analyticTab shows after click: {analytic_shows}')
        print(f'  brain bulk tab shows after click: {brain_bulk_works}')
        if not analytic_shows:
            print('  PROBLEM: analyticTabBtn click does not show analyticTab!')
        if not brain_bulk_works:
            print('  PROBLEM: brainTabBulkBtn click does not show brainTabBulk!')

        await page.wait_for_timeout(2000)
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
