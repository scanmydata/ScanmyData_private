import argparse
import asyncio
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from playwright.async_api import Playwright, async_playwright

# Reconfigure stdio to utf-8 on Windows so Greek text inside print() calls
# (or exception strings re-printed via the brain's _run_script_and_read_json)
# doesn't crash with UnicodeEncodeError under the default cp1252 console.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

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


def _normalize_for_match(text: str) -> str:
    """Strip diacritics + lower + collapse whitespace, for address matching."""
    if not text:
        return ""
    s = unicodedata.normalize("NFD", text)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"[^\w\d ]+", " ", s, flags=re.UNICODE)
    return " ".join(s.split()).strip()


async def detail_matches_address(detail: dict, target_address: str) -> bool:
    """Return True when the lease detail tables match target_address.

    Two-stage match — kept in sync with `e3_brain._address_loose_match` so
    the brain's lease picker and this PDF-download gate agree:

    1. STRICT: every significant token of the target (>=3-char word or any
       digit token) is present in the body. Words are substring-matched so
       Greek case endings (ΒΑΡΗ ↔ ΒΑΡΗΣ, ΗΛΙΟΥΠΟΛΗ ↔ ΗΛΙΟΥΠΟΛΕΩΣ) still
       match; numbers must match as standalone tokens so "5" cannot
       stealth-match inside "16672".
    2. ΤΚ-ANCHORED FALLBACK: when the strict pass fails, but the target
       carries a 5-digit ΤΚ and that ΤΚ + every street number + at least
       one word token all hit the body, we accept anyway. This covers the
       user's «αν η διεύθυνση είναι σωστή και ο ΤΚ της περιοχής, το όνομα
       περιοχής δεν πειράζει» rule (e.g. typed ΑΘΗΝΑ vs lease's ΒΑΡΗΣ
       for ΤΚ 16672 — same property, different city/δήμος label).
    """
    if not target_address:
        return False
    norm_target = _normalize_for_match(target_address)
    if not norm_target:
        return False
    tables = detail.get("detailTables") if isinstance(detail.get("detailTables"), list) else []
    joined = []
    for t in tables:
        if not isinstance(t, dict):
            continue
        for row in (t.get("rows") if isinstance(t.get("rows"), list) else []):
            if isinstance(row, list):
                joined.extend(str(c) for c in row)
    body = _normalize_for_match(" ".join(joined))
    target_tokens = [t for t in norm_target.split() if len(t) >= 3 or t.isdigit()]
    if not target_tokens:
        return norm_target in body
    body_tokens = set(body.split())

    def _tok_matches(t: str) -> bool:
        if t.isdigit():
            return t in body_tokens
        return t in body

    if all(_tok_matches(tok) for tok in target_tokens):
        return True

    zips = [t for t in target_tokens if t.isdigit() and len(t) == 5]
    other_nums = [t for t in target_tokens if t.isdigit() and len(t) != 5]
    words = [t for t in target_tokens if not t.isdigit()]
    if zips and all(z in body_tokens for z in zips):
        if all(n in body_tokens for n in other_nums):
            if any(w in body for w in words):
                return True
    return False


async def try_download_receipt_pdf(page, pdf_dir: Path, trans_id: str, label: str) -> str:
    """Click the lease's receipt button and save the PDF. Returns path or ''.

    Best-effort: never raises. The user can also bail out of this from the
    UI (the misth check still works without the PDF).

    Tries the documented `receiptButton` first then falls back to a set of
    anchors/buttons commonly used for the «Εκτύπωση Αποδεικτικού» action so
    a UI rename on the AADE side doesn't silently drop every PDF.
    """
    # Ordered selector list — first hit wins. The original AADE form used
    # input[name='receiptButton']; recent revamps have introduced anchor +
    # button variants. We accept all of them so the brain doesn't silently
    # lose every PDF after a UI tweak.
    selectors = [
        "input[name='receiptButton']",
        "input[type=button][value*='ποδει' i]",   # «Εκτύπωση Αποδεικτικού»
        "input[type=submit][value*='ποδει' i]",
        "a:has-text('Εκτύπωση Αποδεικτικού')",
        "button:has-text('Εκτύπωση Αποδεικτικού')",
        "a:has-text('Αποδεικτικό')",
        "input[type=button][value*='Εκτύπωση' i]",
    ]
    btn = None
    for sel in selectors:
        loc = page.locator(sel)
        try:
            if await loc.count() > 0:
                btn = loc.first
                break
        except Exception:
            continue
    if btn is None:
        print(f"  [misth-pdf] receipt button NOT FOUND for transId={trans_id}; tried {len(selectors)} selectors")
        return ""
    pdf_dir.mkdir(parents=True, exist_ok=True)
    # Preserve Greek letters + digits + ` -._`; collapse everything else to
    # a single underscore. Limit overall length so the path stays under
    # filesystem limits even with long addresses.
    safe = re.sub(r"[^\w\-.]+", "_", str(label or trans_id or "lease"), flags=re.UNICODE)
    safe = re.sub(r"_+", "_", safe).strip("_")[:120]
    if not safe:
        safe = str(trans_id or "lease")
    suffix = f"_{trans_id}" if (trans_id and trans_id not in safe) else ""
    out = pdf_dir / f"misth_{safe}{suffix}.pdf"
    try:
        async with page.expect_download(timeout=30000) as dl_info:
            await btn.click(timeout=15000)
        dl = await dl_info.value
        await dl.save_as(str(out))
        print(f"  [misth-pdf] saved {out.name} for transId={trans_id}")
        return str(out)
    except Exception as exc:
        # Surface the actual failure reason so we can diagnose why the PDF
        # didn't arrive (timeout vs. nav-without-download vs. permission).
        print(f"  [misth-pdf] download FAILED for transId={trans_id}: {type(exc).__name__}: {exc}")
        return ""


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
    """Write page HTML for post-mortem debugging — opt-in only.

    Off by default (the script was littering `misth_debug_*.html` into
    the project root on every run). Set `E3_DEBUG_HTML=1` to re-enable
    when you actually need to inspect a failure.
    """
    if not os.getenv('E3_DEBUG_HTML'):
        return Path('(debug-html-disabled)')
    path = Path(f'misth_debug_{suffix}.html')
    html = await page.content()
    path.write_text(html, encoding='utf-8')
    return path


async def run(playwright: Playwright, username: str, password: str, output_path: Path, headed: bool,
              pdf_dir: Path = None, target_address: str = "") -> None:
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
        'accept_downloads': True,
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
        entry_record = {
            'transId': entry['transId'],
            'summary': entry['summary'],
            'detail': detail,
        }

        # When pdf_dir + target_address are provided, download the receipt
        # PDF for EVERY lease that matches the address. The brain
        # post-process picks the latest one for the period — but having
        # all matching PDFs lets the UI present them and lets the user
        # double-check if the brain picked the right one.
        if pdf_dir is not None and target_address:
            try:
                if await detail_matches_address(detail, target_address):
                    # Use the property address as the human-readable file
                    # label so directory listings stay self-explanatory
                    # ("misth_ΠΑΡΑΔΕΙΣΟΥ_16_ΑΘΗΝΑ_16672_<transId>.pdf").
                    # Fall back to the summary text only when the address
                    # is empty (which the brain always provides today, but
                    # keep the fallback for direct CLI usage).
                    label = (target_address or "").strip()
                    if not label:
                        label = " ".join((entry.get('summary') or [])[:3])
                    saved_pdf_path = await try_download_receipt_pdf(
                        page1, pdf_dir, entry['transId'], label
                    )
                    if saved_pdf_path:
                        entry_record['receiptPdf'] = saved_pdf_path
            except Exception as exc:
                print(f"  receipt PDF skipped for transId={entry['transId']}: {exc}")

        results.append(entry_record)

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
    parser.add_argument('--pdf-dir', default=os.getenv('MISTH_PDF_DIR'),
                        help='If set with --target-address, save the lease receipt PDF here.')
    parser.add_argument('--target-address', default=os.getenv('MISTH_TARGET_ADDRESS', ''),
                        help='Address used to pick the lease whose receipt PDF to download.')
    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir) if args.pdf_dir else None
    async with async_playwright() as playwright:
        await run(playwright, args.username, args.password, Path(args.output), args.headed,
                  pdf_dir=pdf_dir, target_address=args.target_address)


if __name__ == '__main__':
    asyncio.run(main())
