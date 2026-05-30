import argparse
import asyncio
import base64
import json
import re
from itertools import product
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
from playwright.async_api import async_playwright

try:
    import pytesseract
    from pdf2image import convert_from_bytes
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False

AADE_ENTRY = 'https://www.aade.gr/dilosi-forologias-eisodimatos-fp-e1-e2-e3'
DEFAULT_OUTPUT = Path('downloads/e1_extracted.pdf')
AFM_RE = re.compile(r"\b(\d{9})\b")
AMKA_RE = re.compile(r"\b(\d{11})\b")

async def wait_for_navigation_or_load(page, timeout=10000):
    try:
        await page.wait_for_load_state('networkidle', timeout=timeout)
    except Exception:
        try:
            await page.wait_for_load_state('domcontentloaded', timeout=timeout)
        except Exception:
            pass

async def extract_from_text(text):
    if not text:
        return None, None
    afm = AFM_RE.search(text)
    amka = AMKA_RE.search(text)
    return (afm.group(1) if afm else None, amka.group(1) if amka else None)

async def extract_from_page(page):
    try:
        text = await page.evaluate('() => document.body.innerText')
        return await extract_from_text(text)
    except Exception:
        return None, None

def normalize_name(text):
    if not text:
        return ''
    return text.replace('\u00A0', ' ').casefold().strip()

def find_afm_amka(text):
    if not text:
        return None, None
    afm = AFM_RE.search(text)
    amka = AMKA_RE.search(text)
    return (afm.group(1) if afm else None, amka.group(1) if amka else None)

def line_tokens_after_label(line, label):
    norm_line = normalize_name(line)
    prefix = normalize_name(label)
    if not norm_line.startswith(prefix):
        return []
    remainder = line[len(label):].strip()
    return [normalize_name(tok) for tok in remainder.split() if tok.strip()]


def find_named_afm_amka_on_page(page_text, name):
    if not page_text or not name:
        return None, None
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    target_surname, target_first = None, None
    name_parts = [tok for tok in normalize_name(name).split() if tok]
    if len(name_parts) == 2:
        target_surname, target_first = name_parts[0], name_parts[1]
    else:
        target_surname = name_parts[0] if name_parts else None
    # Strategy A: look for Table 1 / header-based table (usually on page 2)
    try:
        header_idx = None
        for i, line in enumerate(lines):
            up = line.upper()
            if 'ΠΙΝΑΚΑΣ 1' in up or 'ΣΤΟΙΧΕΙΑ ΦΟΡΟΛΟΓΟΥΜΕΝΟΥ' in up:
                # search next few lines for header containing column labels
                for j in range(i + 1, min(i + 6, len(lines))):
                    h = lines[j].upper()
                    if 'ΕΠΩΝΥΜΟ' in h and 'ΟΝΟΜΑ' in h and ('ΑΜΚΑ' in h or 'ΑΡΙΘΜΟΣ' in h or 'ΦΟΡΟΛ' in h):
                        header_idx = j
                        break
                if header_idx is not None:
                    break
        if header_idx is None:
            for i, line in enumerate(lines):
                up = line.upper()
                if 'ΕΠΩΝΥΜΟ' in up and 'ΟΝΟΜΑ' in up and ('ΑΜΚΑ' in up or 'ΑΡΙΘΜΟΣ' in up or 'ΦΟΡΟΛ' in up):
                    header_idx = i
                    break

        if header_idx is not None:
            header_line = lines[header_idx]
            cols = re.split(r'\s{2,}', header_line)
            def idx_of(tokens):
                for k, cell in enumerate(cols):
                    cu = cell.upper()
                    for t in tokens:
                        if t in cu:
                            return k
                return None
            idx_surname = idx_of(['ΕΠΩΝΥΜΟ', 'ΕΠΙΘΕΤΟ'])
            idx_name = idx_of(['ΟΝΟΜΑ', 'ΟΝ'])
            idx_afm = idx_of(['ΑΦΜ', 'ΑΡΙΘΜΟΣ ΦΟΡΟΛ', 'ΑΡΙΘΜΟΣ ΦΟΡΟΛ. ΜΗΤΡΩΟΥ'])
            idx_amka = idx_of(['ΑΜΚΑ'])

            for r in range(header_idx + 1, min(header_idx + 300, len(lines))):
                row = lines[r]
                if re.search(r'ΠΙΝΑΚΑΣ\s+\d', row, flags=re.IGNORECASE):
                    break
                cells = re.split(r'\s{2,}', row)
                if idx_surname is None or idx_name is None or idx_afm is None or idx_amka is None:
                    # header incomplete; fallback to token search within the same row
                    normrow = normalize_name(row)
                    if target_surname and target_first:
                        if target_surname in normrow and target_first in normrow:
                            afm_m = AFM_RE.search(row)
                            amka_m = AMKA_RE.search(row)
                            return (afm_m.group(1) if afm_m else None, amka_m.group(1) if amka_m else None)
                    continue

                if max(idx_surname, idx_name, idx_afm, idx_amka) >= len(cells):
                    # sometimes rows wrap; try finding by tokens
                    normrow = normalize_name(row)
                    if target_surname and target_first and target_surname in normrow and target_first in normrow:
                        afm_m = AFM_RE.search(row)
                        amka_m = AMKA_RE.search(row)
                        return (afm_m.group(1) if afm_m else None, amka_m.group(1) if amka_m else None)
                    continue

                surname_cell = cells[idx_surname] if idx_surname < len(cells) else ''
                name_cell = cells[idx_name] if idx_name < len(cells) else ''
                afm_cell = cells[idx_afm] if idx_afm < len(cells) else ''
                amka_cell = cells[idx_amka] if idx_amka < len(cells) else ''

                if target_surname and target_first:
                    if target_surname in normalize_name(surname_cell) and target_first in normalize_name(name_cell):
                        afm_m = AFM_RE.search(afm_cell)
                        amka_m = AMKA_RE.search(amka_cell)
                        return (afm_m.group(1) if afm_m else None, amka_m.group(1) if amka_m else None)
                elif target_surname:
                    if target_surname in normalize_name(surname_cell):
                        afm_m = AFM_RE.search(afm_cell)
                        amka_m = AMKA_RE.search(amka_cell)
                        return (afm_m.group(1) if afm_m else None, amka_m.group(1) if amka_m else None)
    except Exception:
        pass

    # Strategy B: existing labeled-group parsing (keeps compatibility)
    groups = []
    for i, line in enumerate(lines):
        norm = normalize_name(line)
        if norm.startswith('επωνυμο'):
            group = {'start': i, 'surname': line_tokens_after_label(line, 'ΕΠΩΝΥΜΟ'), 'name': [], 'afm': [], 'amka': []}
            for j in range(i + 1, min(i + 10, len(lines))):
                next_norm = normalize_name(lines[j])
                if next_norm.startswith(normalize_name('ΟΝΟΜΑ')) and not group['name']:
                    group['name'] = line_tokens_after_label(lines[j], 'ΟΝΟΜΑ')
                elif next_norm.startswith(normalize_name('ΑΡΙΘΜΟΣ ΦΟΡΟΛ. ΜΗΤΡΩΟΥ')):
                    group['afm'] = line_tokens_after_label(lines[j], 'ΑΡΙΘΜΟΣ ΦΟΡΟΛ. ΜΗΤΡΩΟΥ')
                elif next_norm.startswith(normalize_name('ΑΜΚΑ')):
                    group['amka'] = line_tokens_after_label(lines[j], 'ΑΜΚΑ')
                elif next_norm.startswith(normalize_name('ΤΗΛΕΦΩΝΟ')):
                    break
            if group['surname'] and group['name'] and group['afm'] and group['amka']:
                groups.append(group)

    for group in groups:
        columns = min(len(group['surname']), len(group['name']), len(group['afm']), len(group['amka']))
        for idx in range(columns):
            surname = normalize_name(group['surname'][idx])
            first = normalize_name(group['name'][idx])
            if target_surname and target_first:
                if target_surname in surname and target_first in first:
                    return group['afm'][idx], group['amka'][idx]
            elif target_surname and target_surname in surname:
                return group['afm'][idx], group['amka'][idx]
            elif target_first and target_first in first:
                return group['afm'][idx], group['amka'][idx]

    return None, None

def find_named_afm_amka(pages, name):
    if not pages or not name:
        return None, None
    # Prefer page 2 (index 1) where Table 1 usually lives
    if len(pages) > 1:
        afm, amka = find_named_afm_amka_on_page(pages[1], name)
        if afm and amka:
            return afm, amka
    for page_text in pages:
        afm, amka = find_named_afm_amka_on_page(page_text, name)
        if afm and amka:
            return afm, amka
    all_text = '\n'.join(pages)
    return find_named_afm_amka_on_page(all_text, name)

async def save_pdf_bytes(output_path, pdf_bytes):
    output_path.write_bytes(pdf_bytes)

async def take_screenshot(page, output_dir, name):
    return

async def run(username, password, year, output_path, headless=False, name=None):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, args=['--no-sandbox'])
        context = await browser.new_context()
        page = await context.new_page()
        screenshot_dir = output_path.parent / 'screenshots'

        print('START: goto AADE entry', flush=True)
        await page.goto(AADE_ENTRY)
        await wait_for_navigation_or_load(page)
        await take_screenshot(page, screenshot_dir, 'start_entry')

        popup = page
        print('LOGIN: performing integrated UI flow', flush=True)
        if await page.locator('a:has-text("Είσοδος στην εφαρμογή")').count() > 0:
            try:
                async with context.expect_page() as popup_info:
                    await page.locator('a:has-text("Είσοδος στην εφαρμογή")').first.click()
                popup = await popup_info.value
            except Exception:
                await page.locator('a:has-text("Είσοδος στην εφαρμογή")').first.click()
                popup = page

        await wait_for_navigation_or_load(popup)
        print('LOGIN: entry opened', popup.url, flush=True)

        if await popup.locator('input[name="username"]').count() > 0 and await popup.locator('input[name="password"]').count() > 0:
            print('LOGIN: filling credentials', flush=True)
            await popup.locator('input[name="username"]').fill(username)
            await popup.locator('input[name="password"]').fill(password)
            btn = popup.locator('button[name="btn_login"], button:has-text("Συνδεση"), button:has-text("ΣΥΝΔΕΣΗ"), input[type=submit]')
            if await btn.count() > 0:
                await take_screenshot(popup, screenshot_dir, 'before_login')
                await btn.first.click()
                await wait_for_navigation_or_load(popup)
                await popup.wait_for_timeout(1500)
                print('LOGIN: submitted', flush=True)
                await take_screenshot(popup, screenshot_dir, 'after_login')

                # Detect GSIS/GSIS login failures (OAM-6 session limit or invalid credentials)
                try:
                    page_content = await popup.content()
                    if "Error.jsp" in popup.url or "p_error_code" in popup.url or "OAM-6" in popup.url or "OAM-6" in page_content or "Ο χρήστης χρησιμοποιεί ήδη το μέγιστο αριθμό περιόδων λειτουργίας" in page_content:
                        msg = "GSIS login failed with session limit OAM-6. Close an existing session or use different credentials."
                        res = {'pdf_path': None, 'afm': None, 'amka': None, 'error': 'oam-6', 'message': msg}
                        root_json = Path(__file__).resolve().parents[2] / 'e1_result.json'
                        try:
                            root_json.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding='utf-8')
                        except Exception:
                            pass
                        await browser.close()
                        return res
                    # Generic invalid credential detection by page content
                    if any(tok in page_content for tok in ("Λάθος", "συνθηματικό", "Λανθασμένο", "Δεν βρέθηκε", "invalid", "Incorrect")):
                        msg = "GSIS login failed: invalid username or password."
                        res = {'pdf_path': None, 'afm': None, 'amka': None, 'error': 'login_failed', 'message': msg}
                        root_json = Path(__file__).resolve().parents[2] / 'e1_result.json'
                        try:
                            root_json.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding='utf-8')
                        except Exception:
                            pass
                        await browser.close()
                        return res
                except Exception:
                    pass

        entry_button = popup.locator('button[name="PB_EKKATH_PDF_SYZ"], button:has-text("Είσοδος στην εφαρμογή"), a:has-text("Είσοδος στην εφαρμογή")')
        if await entry_button.count() > 0:
            try:
                print('CLICK: PB_EKKATH_PDF_SYZ/button', flush=True)
                await take_screenshot(popup, screenshot_dir, 'before_pb_ekkath')
                await entry_button.first.click()
                await wait_for_navigation_or_load(popup)
                await popup.wait_for_timeout(1000)
                await take_screenshot(popup, screenshot_dir, 'after_pb_ekkath')
            except Exception:
                pass

        menu_url = f'https://www1.aade.gr/webtax/incomefp/year{year}-income-menu.do'
        if menu_url not in popup.url:
            try:
                print('NAVIGATE: direct year menu', menu_url, flush=True)
                await popup.goto(menu_url)
                await wait_for_navigation_or_load(popup)
                await popup.wait_for_timeout(1500)
                await take_screenshot(popup, screenshot_dir, 'after_direct_menu_nav')
                print('URL after direct menu:', popup.url, flush=True)
            except Exception as e:
                print('NAVIGATE: direct menu failed', repr(e), flush=True)

        print('START YEAR SELECTION', flush=True)
        await popup.wait_for_timeout(2000)
        print('WAIT: 2000ms before selecting year', flush=True)
        year_select = popup.locator('select[name="pt1:yearSelect"], select[id="pt1:yearSelect::content"], select[name="year"], select[name="YEAR"]')
        year_select_count = await year_select.count()
        print('YEAR SELECTOR COUNT', year_select_count, flush=True)
        if year_select_count > 0:
            try:
                await take_screenshot(popup, screenshot_dir, 'before_year_select')
                opts = year_select.locator('option')
                for i in range(await opts.count()):
                    opt = opts.nth(i)
                    txt = (await opt.inner_text()).strip().replace('\u00A0', ' ')
                    val = (await opt.get_attribute('value')) or ''
                    if str(year) in txt or str(year) in val:
                        if val:
                            await year_select.select_option(value=val)
                        else:
                            await year_select.select_option(label=txt)
                        await popup.wait_for_timeout(1000)
                        print('SELECT: chose', await year_select.input_value(), flush=True)
                        break
                await take_screenshot(popup, screenshot_dir, 'after_year_select')
            except Exception as e:
                print('SELECT: error', repr(e), flush=True)

        try:
            if await year_select.count() > 0:
                current = await year_select.input_value()
                if str(current) != str(year):
                    print('FORCE: SelectMenu submit for year', year, flush=True)
                    await popup.evaluate('(y) => { document.forms[0]["YEAR"].value = y; document.forms[0].action = "./year"+y+"-income-menu.do"; document.forms[0].submit(); }', year)
                    await wait_for_navigation_or_load(popup)
                    await popup.wait_for_timeout(2000)
        except Exception as e:
            print('FORCE: SelectMenu submit error', repr(e), flush=True)

        print('AFTER YEAR URL:', popup.url, flush=True)
        await popup.wait_for_timeout(1000)

        e1_btn = None
        for sel in ['button[name*="PBE1"], input[name*="PBE1"], a:has-text("Ε1"), a:has-text("E1")']:
            try:
                loc = popup.locator(sel)
                if await loc.count() > 0:
                    e1_btn = loc.first
                    break
            except Exception:
                pass

        pdf_bytes = None
        new_page = None
        if e1_btn:
            print('CLICK: E1 button (attempt)', flush=True)
            pdf_requests = []
            pdf_responses = []
            print_candidates = []
            downloads = []
            new_pages = []
            def _on_page(page_obj):
                new_pages.append(page_obj)
            def _on_request(r):
                try:
                    url = r.url.lower()
                    if url.endswith('.pdf') or ('e1' in url and '.pdf' in url):
                        pdf_requests.append(r)
                except Exception:
                    pass
            def _on_response(r):
                try:
                    ctype = (r.headers.get('content-type') or '').lower()
                    url = r.url.lower()
                    if 'application/pdf' in ctype or 'application/octet-stream' in ctype or url.endswith('.pdf'):
                        pdf_responses.append(r)
                    if 'menup' in url or 'print' in url or url.endswith('.pdf'):
                        print_candidates.append((url, ctype, r.status))
                except Exception:
                    pass
            def _on_download(dl):
                downloads.append(dl)
            context.on('page', _on_page)
            context.on('request', _on_request)
            context.on('response', _on_response)
            context.on('download', _on_download)
            new_page = None
            try:
                try:
                    async with context.expect_page(timeout=5000) as popup_info:
                        await e1_btn.click()
                    new_page = await popup_info.value
                except Exception:
                    await e1_btn.click()
                await popup.wait_for_timeout(3000)
                print('AFTER E1 click pages count', len(context.pages), 'new_pages', len(new_pages), flush=True)
                for idx, page_obj in enumerate(context.pages):
                    print(f'PAGE[{idx}] url=', page_obj.url, flush=True)
                if not new_page and new_pages:
                    new_page = new_pages[-1]
                if new_page:
                    await take_screenshot(new_page, screenshot_dir, 'new_page_after_e1')
                    try:
                        await new_page.wait_for_load_state('load', timeout=10000)
                    except Exception as e:
                        print('NEWPAGE load state failed', repr(e), flush=True)
                    try:
                        async with new_page.expect_response(lambda r: r.url == new_page.url, timeout=10000) as resp_info:
                            resp = await resp_info.value
                        ctype = (resp.headers.get('content-type') or '').lower()
                        print('NEWPAGE response', resp.url, resp.status, ctype, flush=True)
                        if 'application/pdf' in ctype or 'application/octet-stream' in ctype:
                            pdf_bytes = await resp.body()
                            print('NEWPAGE response body captured', len(pdf_bytes) if isinstance(pdf_bytes, (bytes, bytearray)) else None, flush=True)
                            if not (isinstance(pdf_bytes, (bytes, bytearray)) and pdf_bytes[:4] == b'%PDF'):
                                print('NEWPAGE response body is not PDF; first bytes', pdf_bytes[:8] if isinstance(pdf_bytes, (bytes, bytearray)) else None, flush=True)
                    except Exception as e:
                        print('NEWPAGE expect_response failed', repr(e), flush=True)
                    try:
                        new_text = await new_page.evaluate('() => document.documentElement.innerText || ""')
                        print('NEWPAGE TEXT length', len(new_text), flush=True)
                        print('NEWPAGE TEXT preview', new_text[:1000], flush=True)
                    except Exception as e:
                        print('NEWPAGE text evaluate failed', repr(e), flush=True)
                print('PDF_REQUESTS', [r.url for r in pdf_requests], flush=True)
                print('PDF_RESPONSES', [(r.url, (r.headers.get('content-type') or '').lower(), r.status) for r in pdf_responses], flush=True)
                print('PRINT_CANDIDATES', print_candidates, flush=True)
                print('DOWNLOADS', len(downloads), flush=True)
                if print_candidates:
                    for idx, (url, ctype, status) in enumerate(print_candidates):
                        print(f'PRINT_CANDIDATE[{idx}]', url, ctype, status, flush=True)
                if pdf_responses:
                    for idx, resp in enumerate(pdf_responses):
                        try:
                            candidate = await resp.body()
                            print(f'pdf_responses[{idx}] len', len(candidate), 'first bytes', candidate[:8], flush=True)
                            if isinstance(candidate, (bytes, bytearray)) and candidate[:4] == b'%PDF':
                                pdf_bytes = candidate
                                print(f'selected pdf_responses[{idx}] as PDF', flush=True)
                                break
                            else:
                                print(f'pdf_responses[{idx}] not PDF', repr(candidate[:16]), flush=True)
                        except Exception as e:
                            print(f'pdf_responses[{idx}] body failed', repr(e), flush=True)
                    if not pdf_bytes:
                        print('no pdf_responses contained a valid PDF', flush=True)
                elif pdf_requests:
                    req = pdf_requests[0]
                    try:
                        print('TRYING pdf_requests URL', req.url, flush=True)
                        resp = await context.request.get(req.url)
                        print('pdf_requests raw status', resp.status, 'ctype', resp.headers.get('content-type'), flush=True)
                        if resp.ok:
                            pdf_bytes = await resp.body()
                            if not (isinstance(pdf_bytes, (bytes, bytearray)) and pdf_bytes[:4] == b'%PDF'):
                                pdf_bytes = None
                    except Exception as e:
                        print('Fetch request URL failed', repr(e), flush=True)
                elif downloads:
                    try:
                        dl = downloads[0]
                        await dl.save_as(str(output_path))
                        pdf_bytes = output_path.read_bytes()
                        print('Saved PDF from download event to', output_path, flush=True)
                    except Exception as e:
                        print('Download save failed', repr(e), flush=True)
                else:
                    try:
                        resp = await context.wait_for_event('response', timeout=5000)
                        ctype = (resp.headers.get('content-type') or '').lower()
                        if 'application/pdf' in ctype or 'application/octet-stream' in ctype or resp.url.lower().endswith('.pdf'):
                            print('PDF response found via context response event', flush=True)
                            pdf_bytes = await resp.body()
                            if not (isinstance(pdf_bytes, (bytes, bytearray)) and pdf_bytes[:4] == b'%PDF'):
                                print('WARNING: context response body is not actual PDF, falling back to new_page extraction', flush=True)
                                try:
                                    (output_path.parent / 'debug_response_body.bin').write_bytes(pdf_bytes)
                                except Exception:
                                    pass
                                pdf_bytes = None
                    except Exception:
                        pdf_bytes = None
            except Exception as e:
                print('E1 click failed', repr(e), flush=True)
            finally:
                try:
                    context.off('page', _on_page)
                except Exception:
                    pass
                try:
                    context.off('request', _on_request)
                except Exception:
                    pass
                try:
                    context.off('response', _on_response)
                except Exception:
                    pass
                try:
                    context.off('download', _on_download)
                except Exception:
                    pass

            if not pdf_bytes and new_page:
                try:
                    await wait_for_navigation_or_load(new_page)
                    await take_screenshot(new_page, screenshot_dir, 'new_page_after_e1')
                    print('NEWPAGE URL:', new_page.url, flush=True)
                    # use the opened target page for extraction
                    target_page = new_page
                    afm, amka = await extract_from_page(target_page)
                    if afm or amka:
                        await browser.close()
                        return {'pdf_path': None, 'afm': afm, 'amka': amka, 'method': 'newpage'}

                    # inspect embedded frames/objects for real PDF source
                    try:
                        src_candidates = await target_page.evaluate('''() => {
                            const sources = [];
                            for (const e of document.querySelectorAll('iframe, embed, object')) {
                                const src = e.src || e.data || e.getAttribute('data');
                                if (src) sources.push({tag: e.tagName.toLowerCase(), src});
                            }
                            return sources;
                        }''')
                        print('NEWPAGE sources:', src_candidates, flush=True)
                        for item in src_candidates or []:
                            if not item.get('src'):
                                continue
                            src = item['src']
                            if src == 'about:blank':
                                continue
                            if src.startswith('data:application/pdf'):
                                try:
                                    b64 = src.split(',', 1)[1]
                                    pdf_bytes = base64.b64decode(b64)
                                    break
                                except Exception:
                                    continue
                            if src.startswith('blob:'):
                                try:
                                    b64 = await target_page.evaluate('(url) => fetch(url).then(r=>r.arrayBuffer()).then(b=>btoa(String.fromCharCode.apply(null,new Uint8Array(b))))', src)
                                    pdf_bytes = base64.b64decode(b64)
                                    break
                                except Exception as e:
                                    print('Blob PDF fetch failed', repr(e), flush=True)
                                    continue
                            if src.startswith('http'):
                                try:
                                    resp = await target_page.request.get(src)
                                    if resp.ok:
                                        candidate = await resp.body()
                                        if isinstance(candidate, (bytes, bytearray)) and candidate[:4] == b'%PDF':
                                            pdf_bytes = candidate
                                            print('Fetched PDF from new page src', src, flush=True)
                                            break
                                except Exception as e:
                                    print('Fetch src URL failed', repr(e), flush=True)
                                    continue
                    except Exception as e:
                        print('NEWPAGE inspect failed', repr(e), flush=True)

                    if not pdf_bytes:
                        target_page = new_page if new_page else popup
                        if target_page and target_page.url:
                            try:
                                print('TRYING raw request to', target_page.url, flush=True)
                                resp = await target_page.request.get(target_page.url, headers={'accept': 'application/pdf, */*'})
                                print('RAW REQUEST status', resp.status, 'ctype', resp.headers.get('content-type'), flush=True)
                                if resp.ok:
                                    candidate = await resp.body()
                                    if isinstance(candidate, (bytes, bytearray)) and candidate[:4] == b'%PDF':
                                        pdf_bytes = candidate
                                        print('Fetched PDF from target page URL', target_page.url, flush=True)
                                    else:
                                        print('Raw request response not PDF; first bytes', candidate[:8], flush=True)
                            except Exception as e:
                                print('Request to target page URL failed', repr(e), flush=True)
                            if not pdf_bytes:
                                try:
                                    print('TRYING browser fetch to', target_page.url, flush=True)
                                    b64 = await target_page.evaluate('(url) => fetch(url, {credentials: "include", headers: {accept: "application/pdf, */*"}}).then(r => r.arrayBuffer()).then(b => btoa(String.fromCharCode.apply(null, new Uint8Array(b))))', target_page.url)
                                    pdf_bytes = base64.b64decode(b64)
                                    if isinstance(pdf_bytes, (bytes, bytearray)) and pdf_bytes[:4] == b'%PDF':
                                        print('Fetched PDF from browser fetch', flush=True)
                                    else:
                                        print('Browser fetch result not PDF; first bytes', pdf_bytes[:8], flush=True)
                                        pdf_bytes = None
                                except Exception as e:
                                    print('Browser fetch to target page failed', repr(e), flush=True)
                except Exception as e:
                    print('New page extraction failed', repr(e), flush=True)

            if not pdf_bytes:
                try:
                    embed_src = await popup.evaluate('''() => {
                        const e = document.querySelector('embed[type="application/pdf"], embed');
                        if (e && e.src) return e.src;
                        const obj = document.querySelector('object[type="application/pdf"]');
                        if (obj && obj.data) return obj.data;
                        const iframe = document.querySelector('iframe');
                        if (iframe && iframe.src) return iframe.src;
                        return null;
                    }''')
                    if embed_src and embed_src != 'about:blank':
                        if embed_src.startswith('data:application/pdf'):
                            b64 = embed_src.split(',', 1)[1]
                            pdf_bytes = base64.b64decode(b64)
                        else:
                            try:
                                b64 = await popup.evaluate('(url) => fetch(url).then(r=>r.arrayBuffer()).then(b=>btoa(String.fromCharCode.apply(null,new Uint8Array(b))))', embed_src)
                                pdf_bytes = base64.b64decode(b64)
                            except Exception as e:
                                print('PDF fetch via page.evaluate failed', repr(e), flush=True)
                except Exception as e:
                    print('Embed inspection failed', repr(e), flush=True)

        if not pdf_bytes:
            print('NO PDF yet, trying form submit fallback', flush=True)
            form_page = new_page if new_page else popup
            form = None
            for f in await form_page.query_selector_all('form'):
                try:
                    action = (await f.get_attribute('action') or '').lower()
                    if 'menuprint' in action or 'print' in action:
                        form = f
                        break
                except Exception:
                    pass
            if form:
                try:
                    action = await form.get_attribute('action') or form_page.url
                    action_url = action if action.startswith('http') else urljoin(form_page.url, action)
                    params = {}
                    for inp in await form.query_selector_all('input,select,textarea'):
                        name = await inp.get_attribute('name')
                        if not name:
                            continue
                        value = (await (await inp.get_property('value')).json_value()) or ''
                        params[name] = value
                    if 'PBE1_PRINT_PDF' not in ''.join(params.keys()):
                        params['PBE1_PRINT_PDF'] = ''
                    print('POST fallback to', action_url, flush=True)
                    resp = await form_page.request.post(action_url, data=params, headers={'accept': 'application/pdf, */*'})
                    pdf_bytes = await resp.body()
                except Exception as e:
                    print('Form fallback failed', repr(e), flush=True)

        if not pdf_bytes:
            try:
                async with popup.expect_download(timeout=10000) as dl_info:
                    if e1_btn:
                        await e1_btn.click()
                dl = await dl_info.value
                await dl.save_as(str(output_path))
                pdf_bytes = output_path.read_bytes()
            except Exception:
                pass

        if not pdf_bytes:
            await browser.close()
            raise RuntimeError('Could not obtain PDF from AADE print flow')

        is_pdf = isinstance(pdf_bytes, (bytes, bytearray)) and pdf_bytes[:4] == b'%PDF'
        if not is_pdf:
            try:
                html = pdf_bytes.decode('utf-8', 'ignore')
                afm, amka = await extract_from_text(re.sub(r'<[^>]+>', ' ', html))
                if afm or amka:
                    await browser.close()
                    return {'pdf_path': None, 'afm': afm, 'amka': amka, 'method': 'html'}
            except Exception:
                pass
            raise RuntimeError('Downloaded content was not a PDF and no AFM/AMKA could be extracted')

        await save_pdf_bytes(output_path, pdf_bytes)

        afm = None
        amka = None
        pages = []
        try:
            with pdfplumber.open(str(output_path)) as pdf:
                for page in pdf.pages:
                    pages.append(page.extract_text() or '')
        except Exception:
            pages = []

        if name and pages:
            afm, amka = find_named_afm_amka(pages, name)

        if (not afm or not amka) and pages:
            combined = '\n'.join(pages)
            afm, amka = await extract_from_text(combined)

        if (not afm or not amka) and OCR_AVAILABLE:
            try:
                pages = convert_from_bytes(pdf_bytes)
                img = pages[1] if len(pages) > 1 else pages[0]
                try:
                    ocr_text = pytesseract.image_to_string(img, lang='ell+eng')
                except Exception:
                    ocr_text = pytesseract.image_to_string(img)
                ocr_afm, ocr_amka = await extract_from_text(ocr_text)
                afm = afm or ocr_afm
                amka = amka or ocr_amka
            except Exception:
                pass

        await browser.close()
        return {'pdf_path': str(output_path), 'afm': afm, 'amka': amka}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--username', required=True)
    parser.add_argument('--password', required=True)
    parser.add_argument('--year', default='2025')
    parser.add_argument('--name', default=None)
    parser.add_argument('--output', default=str(DEFAULT_OUTPUT))
    parser.add_argument('--headless', action='store_true')
    args = parser.parse_args()

    try:
        res = asyncio.run(run(args.username, args.password, args.year, args.output, headless=args.headless, name=args.name))
        print(json.dumps(res, ensure_ascii=False, indent=2))
        root_json = Path(__file__).resolve().parents[2] / 'e1_result.json'
        root_json.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise


if __name__ == '__main__':
    main()
