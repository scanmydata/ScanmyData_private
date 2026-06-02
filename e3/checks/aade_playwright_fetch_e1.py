import asyncio
import re
import base64
import json
import unicodedata
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except Exception as e:
    raise

try:
    from e3.checks import chromium_launch_args
except Exception:
    def chromium_launch_args(extra=None):
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-software-rasterizer",
        ]
        if extra:
            args.extend(extra)
        return args

AFM_RE = re.compile(r"\b(\d{9})\b")
AMKA_RE = re.compile(r"\b(\d{11})\b")
AADE_ENTRY = 'https://www.aade.gr/dilosi-forologias-eisodimatos-fp-e1-e2-e3'


async def _extract_from_text(text: str, name: str = None, afm_hint: str = None):
    if not text:
        return None, None

    def normalize(s):
        if not s:
            return ''
        nfkd = unicodedata.normalize('NFKD', s)
        nfkd = ''.join(c for c in nfkd if not unicodedata.combining(c))
        return nfkd.lower()

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # Prefer AMKA found near a provided AFM hint
    if afm_hint:
        afm_digits = re.sub(r'\D', '', str(afm_hint))
        if afm_digits:
            for i, ln in enumerate(lines):
                if afm_digits in re.sub(r'\D', '', ln):
                    # build a context block around the AFM occurrence
                    start = max(0, i - 3)
                    end = min(len(lines), i + 4)
                    block = "\n".join(lines[start:end])
                    # find ordered occurrences of AFM and AMKA in the block
                    occurrences = []
                    for m in AFM_RE.finditer(block):
                        occurrences.append((m.start(), 'afm', m.group(1)))
                    for m in AMKA_RE.finditer(block):
                        occurrences.append((m.start(), 'amka', m.group(1)))
                    occurrences.sort(key=lambda x: x[0])
                    # First try positional mapping when equal counts of AFMs/AMKAs in block
                    afms = AFM_RE.findall(block)
                    amkas = AMKA_RE.findall(block)
                    if afms and amkas and len(afms) == len(amkas):
                        try:
                            pos = afms.index(afm_digits)
                            return afm_digits, amkas[pos]
                        except Exception:
                            pass
                    # Next, find the AFM occurrence matching afm_digits and pick the next AMKA after it
                    for idx, (pos, kind, val) in enumerate(occurrences):
                        if kind == 'afm' and val == afm_digits:
                            # scan forward for next amka
                            for fpos, fkind, fval in occurrences[idx+1:]:
                                if fkind == 'amka':
                                    return afm_digits, fval
                            break
                    # otherwise try simple nearby search
                    for j in range(start, end):
                        m = AMKA_RE.search(lines[j])
                        if m:
                            return afm_digits, m.group(1)

    # Next, prefer AMKA found near the provided name (label-aware + proximity scoring)
    if name:
        name_norm = normalize(name)
        name_tokens = [t for t in re.split(r'\s+', name_norm) if t and len(t) > 0]
        # collect lines where name tokens appear
        name_lines = []
        for idx, ln in enumerate(lines):
            ln_norm = normalize(ln)
            token_matches = [tok for tok in name_tokens if tok in ln_norm]
            if token_matches:
                name_lines.append((idx, len(token_matches)))

        # collect AMKA candidates and where they appear
        amka_candidates = []
        for idx, ln in enumerate(lines):
            for m in AMKA_RE.finditer(ln):
                amka_val = m.group(1)
                amka_candidates.append({'line': idx, 'amka': amka_val, 'line_text': ln})

        # helper to check label presence
        def has_amka_label(s):
            s2 = normalize(s)
            return 'αμκ' in s2 or 'αμκα' in s2 or 'α.μ.κ' in s2 or 'αριθ' in s2 and 'μητρ' in s2

        # score candidates by proximity to name and label presence
        scored = []
        for cand in amka_candidates:
            idx = cand['line']
            score = 0
            # label in same or nearby lines
            window = '\n'.join(lines[max(0, idx-1):min(len(lines), idx+2)])
            if has_amka_label(window):
                score += 50
            # proximity to any name line
            if name_lines:
                dists = [abs(idx - nl) for nl, _ in name_lines]
                dmin = min(dists)
                if dmin <= 5:
                    score += max(0, 20 - dmin*3)
                    # boost by token match count on closest name line
                    for nl, cnt in name_lines:
                        if abs(idx - nl) == dmin:
                            score += cnt * 5
                            break
            # small boost if AFM hint also appears nearby
            if afm_hint:
                block = '\n'.join(lines[max(0, idx-3):min(len(lines), idx+4)])
                if re.search(re.escape(str(afm_hint)), block):
                    score += 30
            scored.append((score, cand))

        if scored:
            scored.sort(key=lambda x: (-x[0], x[1]['line']))
            best_score, best_cand = scored[0]
            if best_score > 0:
                # try to derive AFM in same context
                ctx = '\n'.join(lines[max(0, best_cand['line']-3):min(len(lines), best_cand['line']+4)])
                a = AFM_RE.search(ctx)
                afm_val = a.group(1) if a else (afm_hint if afm_hint else None)
                return afm_val, best_cand['amka']

        # fallback: previous strict name-token matching (require >=2 token matches)
        if name_tokens:
            for i, ln in enumerate(lines):
                ln_norm = normalize(ln)
                match_count = sum(1 for tok in name_tokens if tok in ln_norm)
                if match_count >= min(2, len([t for t in name_tokens if len(t) > 1])):
                    for j in range(max(0, i - 3), min(len(lines), i + 4)):
                        m = AMKA_RE.search(lines[j])
                        if m:
                            a = AFM_RE.search('\n'.join(lines[max(0, i - 3):min(len(lines), i + 4)]))
                            afm_val = a.group(1) if a else (afm_hint if afm_hint else None)
                            return afm_val, m.group(1)

    # Fallback to first occurrences
    a = AFM_RE.search(text)
    m = AMKA_RE.search(text)
    return (a.group(1) if a else None, m.group(1) if m else None)


async def _save_debug(page, screenshot_dir: Path, name: str):
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    try:
        html = await page.content()
        (screenshot_dir / f"{name}.html").write_text(html, encoding='utf-8')
    except Exception:
        pass
    try:
        await page.screenshot(path=str(screenshot_dir / f"{name}.png"), full_page=True)
    except Exception:
        pass


async def _run_impl(username, password, year, output_path, headless=True, name=None, initial_storage: str = None, afm_hint: str = None):
    outp = Path(output_path)
    outp.parent.mkdir(parents=True, exist_ok=True)
    screenshot_dir = outp.parent / 'screenshots'

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, args=chromium_launch_args())
        ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36'
        if initial_storage:
            try:
                context = await browser.new_context(user_agent=ua, locale='el-GR', storage_state=str(initial_storage))
            except Exception:
                context = await browser.new_context(user_agent=ua, locale='el-GR')
        else:
            context = await browser.new_context(user_agent=ua, locale='el-GR')
        page = await context.new_page()
        page.set_default_navigation_timeout(30000)
        page.set_default_timeout(30000)

        # start tracing to capture network activity for debugging
        try:
            await context.tracing.start(screenshots=True, snapshots=True)
        except Exception:
            pass

        await page.goto(AADE_ENTRY)
        await page.wait_for_timeout(2000)

        # open integrated login if present
        popup = page
        try:
            if await page.locator('a:has-text("Είσοδος στην εφαρμογή")').count() > 0:
                async with context.expect_page(timeout=5000) as popup_info:
                    await page.locator('a:has-text("Είσοδος στην εφαρμογή")').first.click()
                popup = await popup_info.value
        except Exception:
            popup = page

        await _save_debug(popup, screenshot_dir, 'after_entry')

        # fill credentials if present
        try:
            if await popup.locator('input[name="username"]').count() > 0 and await popup.locator('input[name="password"]').count() > 0:
                await popup.locator('input[name="username"]').fill(username)
                await popup.locator('input[name="password"]').fill(password)
                btn = popup.locator('button[name="btn_login"], button:has-text("Συνδεση"), button:has-text("ΣΥΝΔΕΣΗ"), input[type=submit]')
                if await btn.count() > 0:
                    await _save_debug(popup, screenshot_dir, 'before_login')
                    await btn.first.click()
                    # wait for navigation/networkidle or for an element that indicates successful login
                    try:
                        await popup.wait_for_load_state('networkidle', timeout=20000)
                    except Exception:
                        pass
                    await popup.wait_for_timeout(1000)
                    await _save_debug(popup, screenshot_dir, 'after_login')
                    # optional interactive pause for debugging (set via CLI flag)
                    if getattr(_run_impl, '_pause_after_login', False):
                        try:
                            await asyncio.get_event_loop().run_in_executor(None, input, 'Paused after login. Inspect browser then press Enter to continue...')
                        except Exception:
                            pass
                    # immediately try clicking the local "Είσοδος στην εφαρμογή" control to enter the app UI
                    try:
                        entry_loc = popup.locator('button[name="PB_EKKATH_PDF_SYZ"], a[href*="login.done"], a:has-text("Είσοδος στην εφαρμογή")')
                        if await entry_loc.count() > 0:
                            try:
                                await entry_loc.first().click()
                            except Exception:
                                try:
                                    await popup.click('button[name="PB_EKKATH_PDF_SYZ"], a[href*="login.done"], a:has-text("Είσοδος στην εφαρμογή")')
                                except Exception:
                                    pass
                            try:
                                await popup.wait_for_load_state('networkidle', timeout=20000)
                            except Exception:
                                pass
                            await popup.wait_for_timeout(1500)
                            await _save_debug(popup, screenshot_dir, 'after_local_entry_click_post_login')
                    except Exception:
                        pass
        except Exception:
            pass

        # try to select the year from an on-page selector first, try clicking local "Είσοδος στην εφαρμογή" button,
        # then fallback to direct menu URL
        menu_url = f'https://www1.aade.gr/webtax/incomefp/year{year}-income-menu.do'
        try:
            try:
                # attempt to find and select the year option via JS (handles dynamically populated selects)
                selected = await popup.evaluate('''async (year) => {
                    const sel = document.querySelector('select[name="pt1:yearSelect"], select[name="year"], select[id*="year"]');
                    if (!sel) return false;
                    for (const opt of Array.from(sel.options)) {
                        if ((opt.text || '').includes(year) || (opt.value || '').includes(year)) {
                            sel.value = opt.value;
                            sel.dispatchEvent(new Event('change', { bubbles: true }));
                            return true;
                        }
                    }
                    return false;
                }''', str(year))
                if selected:
                    await popup.wait_for_timeout(1500)
                    await _save_debug(popup, screenshot_dir, 'after_year_selected')
                else:
                    # try clicking a local "Είσοδος στην εφαρμογή" button if present (avoids direct goto)
                    try:
                        entry_loc = popup.locator('button[name="PB_EKKATH_PDF_SYZ"], a[href*="login.done"], a:has-text("Είσοδος στην εφαρμογή")')
                        if await entry_loc.count() > 0:
                            await entry_loc.first().click()
                            try:
                                await popup.wait_for_load_state('networkidle', timeout=20000)
                            except Exception:
                                pass
                            await popup.wait_for_timeout(1500)
                            await _save_debug(popup, screenshot_dir, 'after_local_entry_click')
                            # try selection again after clicking local entry
                            selected = await popup.evaluate('''async (year) => {
                                const sel = document.querySelector('select[name="pt1:yearSelect"], select[name="year"], select[id*="year"]');
                                if (!sel) return false;
                                for (const opt of Array.from(sel.options)) {
                                    if ((opt.text || '').includes(year) || (opt.value || '').includes(year)) {
                                        sel.value = opt.value;
                                        sel.dispatchEvent(new Event('change', { bubbles: true }));
                                        return true;
                                    }
                                }
                                return false;
                            }''', str(year))
                            if selected:
                                await popup.wait_for_timeout(1000)
                                await _save_debug(popup, screenshot_dir, 'after_year_selected')
                            else:
                                await popup.goto(menu_url)
                        else:
                            await popup.goto(menu_url)
                    except Exception:
                        await popup.goto(menu_url)
                    try:
                        await popup.wait_for_url(lambda u: f'year{year}-income-menu.do' in u, timeout=20000)
                    except Exception:
                        try:
                            sel = popup.locator('select[name="pt1:yearSelect"], select[name="year"], select[id*="year"]')
                            await sel.wait_for(timeout=20000)
                        except Exception:
                            pass
                    await popup.wait_for_timeout(1000)
                    await _save_debug(popup, screenshot_dir, 'after_year_nav')
            except Exception:
                # if any error selecting, fallback to navigating directly
                await popup.goto(menu_url)
                try:
                    await popup.wait_for_url(lambda u: f'year{year}-income-menu.do' in u, timeout=20000)
                except Exception:
                    pass
                await popup.wait_for_timeout(1000)
                await _save_debug(popup, screenshot_dir, 'after_year_nav')
            # detect temporary loss message and try a recovery by clearing cookies and reloading
            try:
                page_text = (await popup.content() or '').lower()
                if 'προσωρινή απώλεια' in page_text or 'προσωρινη απωλεια' in page_text:
                    for attempt in range(3):
                        try:
                            await context.clear_cookies()
                            await popup.goto(AADE_ENTRY)
                            await popup.wait_for_timeout(1500)
                            await popup.goto(menu_url)
                            await popup.wait_for_timeout(1500)
                            await _save_debug(popup, screenshot_dir, f'after_recovery_nav_{attempt}')
                            pt = (await popup.content() or '').lower()
                            if 'προσωρινή απώλεια' not in pt and 'προσωρινη απωλεια' not in pt:
                                break
                        except Exception:
                            await popup.wait_for_timeout(1000)
                            continue
            except Exception:
                pass
                # after recovery, if login form present, fill credentials again
                try:
                    if await popup.locator('input[name="username"]').count() > 0 and await popup.locator('input[name="password"]').count() > 0:
                        await popup.locator('input[name="username"]').fill(username)
                        await popup.locator('input[name="password"]').fill(password)
                        btn = popup.locator('button[name="btn_login"], button:has-text("Συνδεση"), input[type=submit]')
                        if await btn.count() > 0:
                            await _save_debug(popup, screenshot_dir, 'before_login_after_recovery')
                            await btn.first.click()
                            try:
                                await popup.wait_for_load_state('networkidle', timeout=20000)
                            except Exception:
                                pass
                            await popup.wait_for_timeout(1000)
                            await _save_debug(popup, screenshot_dir, 'after_login_after_recovery')
                            if getattr(_run_impl, '_pause_after_login', False):
                                try:
                                    await asyncio.get_event_loop().run_in_executor(None, input, 'Paused after recovery login. Inspect browser then press Enter to continue...')
                                except Exception:
                                    pass
                except Exception:
                    pass
        except Exception:
            pass

        # find E1 button (wait until the UI loads the menu/actions)
        try:
            await popup.wait_for_timeout(500)
        except Exception:
            pass
        # ensure year option selected matches requested year (if selector exists)
        try:
            sel = popup.locator('select[name="pt1:yearSelect"], select[name="year"], select[id*="year"]')
            if await sel.count() > 0:
                try:
                    opts = sel.locator('option')
                    found = False
                    for i in range(await opts.count()):
                        txt = (await opts.nth(i).inner_text()).strip()
                        if str(year) in txt:
                            found = True
                            break
                    if not found:
                        # try to wait a bit for JS to populate options
                        await sel.wait_for(timeout=5000)
                except Exception:
                    pass
        except Exception:
            pass

        # find E1 button
        e1_btn = None
        for sel in ['button[name*="PBE1"]', 'input[name*="PBE1"]', 'a:has-text("Ε1")', 'a:has-text("E1")']:
            try:
                loc = popup.locator(sel)
                if await loc.count() > 0:
                    e1_btn = loc.first
                    break
            except Exception:
                pass

        # brute-force: search frames/pages for elements with print-related text and click them
        async def brute_force_click_print(target_context):
            patterns = ['Εκτύπ', 'Εκτυπ', 'Εκτύπωση', 'Ε1', 'Print', 'PDF', 'Κατέβ', 'Λήψη']
            # try on main popup first
            try:
                for p in patterns:
                    try:
                        res = await popup.evaluate('(pat)=>{const els=Array.from(document.querySelectorAll("a,button,input"));for(const e of els){const t=(e.innerText||e.value||"").trim();if(t.includes(pat)){try{e.click();return true;}catch(e){}}}return false;}', p)
                        if res:
                            return True
                    except Exception:
                        continue
            except Exception:
                pass
            # try each page/frame in context
            try:
                for pg in target_context.pages:
                    try:
                        for p in patterns:
                            try:
                                res = await pg.evaluate('(pat)=>{const els=Array.from(document.querySelectorAll("a,button,input"));for(const e of els){const t=(e.innerText||e.value||"").trim();if(t.includes(pat)){try{e.click();return true;}catch(e){}}}return false;}', p)
                                if res:
                                    return True
                            except Exception:
                                continue
                    except Exception:
                        continue
            except Exception:
                pass
            return False

        pdf_bytes = None

        # listeners
        pdf_responses = []
        downloads = []
        new_pages = []
        debug = {
            'responses': [],
            'downloads': [],
            'new_pages': [],
            'events': [],
        }

        def on_response(r):
            try:
                ctype = (r.headers.get('content-type') or '').lower()
                url = r.url.lower()
                if 'application/pdf' in ctype or url.endswith('.pdf'):
                    pdf_responses.append(r)
                debug['responses'].append({'url': r.url, 'status': getattr(r, 'status', None), 'content-type': r.headers.get('content-type')})
            except Exception:
                pass

        def on_page(pobj):
            new_pages.append(pobj)
            try:
                debug['new_pages'].append({'url': pobj.url})
            except Exception:
                pass

        def on_download(dl):
            downloads.append(dl)
            try:
                debug['downloads'].append({'url': dl.url})
            except Exception:
                pass

        context.on('response', on_response)
        context.on('page', on_page)
        context.on('download', on_download)

        if e1_btn:
            try:
                # click and allow more time for print flow to produce PDF responses
                await e1_btn.click()
            except Exception:
                try:
                    await popup.click(e1_btn)
                except Exception:
                    pass
            # wait longer for PDF generation / network activity
            await popup.wait_for_timeout(15000)

        # if no immediate PDF, try brute-force clicking print controls across frames/pages
        if not pdf_bytes:
            for attempt in range(3):
                try:
                    clicked = await brute_force_click_print(context)
                    if clicked:
                        await popup.wait_for_timeout(5000 + attempt * 3000)
                    # check responses/downloads again
                    for r in pdf_responses:
                        try:
                            b = await r.body()
                            if isinstance(b, (bytes, bytearray)) and b[:4] == b'%PDF':
                                pdf_bytes = b
                                break
                        except Exception:
                            continue
                    if pdf_bytes:
                        break
                except Exception:
                    await popup.wait_for_timeout(1000)

        # prefer response bodies
        for r in pdf_responses:
            try:
                b = await r.body()
                if isinstance(b, (bytes, bytearray)) and b[:4] == b'%PDF':
                    pdf_bytes = b
                    break
            except Exception:
                continue

        # check downloads
        if not pdf_bytes and downloads:
            try:
                dl = downloads[0]
                dl_path = outp
                await dl.save_as(str(dl_path))
                pdf_bytes = dl_path.read_bytes()
            except Exception:
                pass

        # inspect new pages for embedded PDFs
        if not pdf_bytes and new_pages:
            for npg in new_pages:
                try:
                    await npg.wait_for_load_state('load', timeout=3000)
                except Exception:
                    pass
                await _save_debug(npg, screenshot_dir, 'newpage')
                try:
                    debug['events'].append({'type': 'new_page', 'url': npg.url})
                except Exception:
                    pass
                try:
                    inner = await npg.evaluate('''() => {
                        const sources = [];
                        for (const e of document.querySelectorAll('iframe,embed,object')) {
                            const src = e.src || e.data || e.getAttribute('data');
                            if (src) sources.push(src);
                        }
                        return sources;
                    }''')
                    for src in inner or []:
                        if not src:
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
                                b64 = await npg.evaluate('''async (url) => {
                                    const resp = await fetch(url, { credentials: 'include' });
                                    if (!resp.ok) return null;
                                    const ab = await resp.arrayBuffer();
                                    let binary = '';
                                    const bytes = new Uint8Array(ab);
                                    const chunk = 0x8000;
                                    for (let i = 0; i < bytes.length; i += chunk) {
                                        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                                    }
                                    return btoa(binary);
                                }''', src)
                                if b64:
                                    pdf_bytes = base64.b64decode(b64)
                                    break
                            except Exception:
                                continue
                        if src.startswith('http'):
                            try:
                                b64 = await npg.evaluate('''async (url) => {
                                    const resp = await fetch(url, { credentials: 'include' });
                                    if (!resp.ok) return null;
                                    const ab = await resp.arrayBuffer();
                                    let binary = '';
                                    const bytes = new Uint8Array(ab);
                                    const chunk = 0x8000;
                                    for (let i = 0; i < bytes.length; i += chunk) {
                                        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                                    }
                                    return btoa(binary);
                                }''', src)
                                if b64:
                                    candidate = base64.b64decode(b64)
                                    if isinstance(candidate, (bytes, bytearray)) and candidate[:4] == b'%PDF':
                                        pdf_bytes = candidate
                                        break
                            except Exception:
                                continue
                except Exception:
                    pass
                if pdf_bytes:
                    break

        # form POST fallback
        if not pdf_bytes:
            try:
                forms = await popup.query_selector_all('form')
                for f in forms:
                    try:
                        action = (await f.get_attribute('action')) or popup.url
                        if 'print' in (action or '').lower() or 'menuprint' in (action or '').lower():
                            action_url = action if action.startswith('http') else popup.url
                            params = {}
                            inputs = await f.query_selector_all('input,select,textarea')
                            for inp in inputs:
                                name = await inp.get_attribute('name')
                                if not name:
                                    continue
                                val = await (await inp.get_property('value')).json_value()
                                params[name] = val or ''
                            # ensure some print param exists
                            if 'PBE1_PRINT_PDF' not in ''.join(params.keys()):
                                params['PBE1_PRINT_PDF'] = ''
                            try:
                                b64 = await popup.evaluate('''async (action, params) => {
                                    const form = new URLSearchParams();
                                    for (const k of Object.keys(params)) form.append(k, params[k]);
                                    const resp = await fetch(action, { method: 'POST', body: form, credentials: 'include', headers: { 'accept': 'application/pdf, */*' } });
                                    if (!resp.ok) return null;
                                    const ab = await resp.arrayBuffer();
                                    let binary = '';
                                    const bytes = new Uint8Array(ab);
                                    const chunk = 0x8000;
                                    for (let i = 0; i < bytes.length; i += chunk) {
                                        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                                    }
                                    return btoa(binary);
                                }''', action_url, params)
                                if b64:
                                    candidate = base64.b64decode(b64)
                                    if isinstance(candidate, (bytes, bytearray)) and candidate[:4] == b'%PDF':
                                        pdf_bytes = candidate
                                        break
                            except Exception:
                                pass
                    except Exception:
                        continue
            except Exception:
                pass

        # direct print URL fallback: try known menuPrint endpoints using page fetch (include cookies)
        if not pdf_bytes:
            try:
                candidates = [
                    f'https://www1.aade.gr/webtax/incomefp/year{year}-income-menuPrint.do',
                    f'https://www1.aade.gr/webtax/incomefp/year{year}-income-menuPrint.action',
                    f'https://www1.aade.gr/webtax/incomefp/year{year}-income-menuPrint'
                ]
                for url in candidates:
                    try:
                        b64 = await popup.evaluate('''async (url) => {
                            const resp = await fetch(url, { credentials: 'include', headers: { accept: 'application/pdf, */*' } });
                            if (!resp.ok) return null;
                            const ab = await resp.arrayBuffer();
                            let binary = '';
                            const bytes = new Uint8Array(ab);
                            const chunk = 0x8000;
                            for (let i = 0; i < bytes.length; i += chunk) {
                                binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                            }
                            return btoa(binary);
                        }''', url)
                        if b64:
                            candidate = base64.b64decode(b64)
                            if isinstance(candidate, (bytes, bytearray)) and candidate[:4] == b'%PDF':
                                pdf_bytes = candidate
                                debug['events'].append({'type': 'direct_fetch', 'url': url})
                                break
                    except Exception:
                        continue
                    if pdf_bytes:
                        break
            except Exception:
                pass

        # write debug summary
        try:
            (screenshot_dir / 'debug_summary.json').write_text(json.dumps(debug, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass

        # cleanup listeners
        try:
            context.off('response', on_response)
        except Exception:
            pass
        try:
            context.off('page', on_page)
        except Exception:
            pass
        try:
            context.off('download', on_download)
        except Exception:
            pass

        if not pdf_bytes:
            try:
                await context.storage_state(path=str(screenshot_dir / 'storage_state.json'))
            except Exception:
                pass
            try:
                await context.tracing.stop(path=str(screenshot_dir / 'trace.zip'))
            except Exception:
                pass
            await browser.close()
            raise RuntimeError('Could not obtain PDF from AADE print flow')

        outp.write_bytes(pdf_bytes)

        # try extracting AFM/AMKA from the PDF text first
        afm = None
        amka = None
        pdf_afm = None
        pdf_amka = None
        try:
            import pdfplumber
            pages = []
            try:
                with pdfplumber.open(str(outp)) as pdf:
                    for pg in pdf.pages:
                        pages.append(pg.extract_text() or '')
            except Exception:
                pages = []
            if pages:
                combined = '\n'.join(pages)
                pdf_afm, pdf_amka = await _extract_from_text(combined, name=name, afm_hint=afm_hint)
                try:
                    debug['pdf_text_len'] = len(combined)
                    debug['pdf_afm_candidates'] = AFM_RE.findall(combined)
                    debug['pdf_amka_candidates'] = AMKA_RE.findall(combined)
                except Exception:
                    pass
        except Exception:
            try:
                txt = pdf_bytes.decode('utf-8', 'ignore')
                pdf_afm, pdf_amka = await _extract_from_text(txt, name=name, afm_hint=afm_hint)
            except Exception:
                pdf_afm = None
                pdf_amka = None

        # Also try to extract AMKA from the page HTML (some fields may not be embedded in the PDF text)
        page_html_afm = None
        page_html_amka = None
        try:
            page_html = (await popup.content()) or ''
            page_html_afm, page_html_amka = await _extract_from_text(page_html, name=name, afm_hint=afm_hint)
            try:
                debug['html_amka_candidates'] = AMKA_RE.findall(page_html)
            except Exception:
                pass
        except Exception:
            page_html = ''

        # check new pages (popups) as well
        npg_amka = None
        try:
            for npg in new_pages:
                try:
                    c = (await npg.content()) or ''
                    a_afm, a_amka = await _extract_from_text(c, name=name, afm_hint=afm_hint)
                    if a_amka:
                        npg_amka = a_amka
                        break
                except Exception:
                    continue
        except Exception:
            pass

        # If the page HTML contains the AFM for the target name, prefer it and remap PDF results
        afm = None
        amka = None
        try:
            # prefer AFM extracted from page HTML to remap PDF values
            if page_html_afm:
                # attempt to remap AMKA from PDF using the AFM found in the page HTML
                try:
                    if 'combined' in locals() and combined:
                        mapped_afm, mapped_amka = await _extract_from_text(combined, name=name, afm_hint=page_html_afm)
                        if mapped_amka:
                            afm = mapped_afm or page_html_afm
                            amka = mapped_amka
                        else:
                            afm = page_html_afm
                    else:
                        afm = page_html_afm
                except Exception:
                    afm = page_html_afm

            # if we didn't get an AMKA via remapping, prefer other sources in order
            if not amka:
                if page_html_amka:
                    amka = page_html_amka
                elif npg_amka:
                    amka = npg_amka
                elif pdf_amka:
                    # if page HTML AFM was present but remapping failed, try remapping again
                    if page_html_afm and 'combined' in locals() and combined:
                        try:
                            mapped_afm, mapped_amka = await _extract_from_text(combined, name=name, afm_hint=page_html_afm)
                            if mapped_amka:
                                afm = mapped_afm or page_html_afm
                                amka = mapped_amka
                            else:
                                amka = pdf_amka
                                afm = afm or pdf_afm
                        except Exception:
                            amka = pdf_amka
                            afm = afm or pdf_afm
                    else:
                        amka = pdf_amka
                        afm = afm or pdf_afm
                else:
                    amka = None

            # final fallback for AFM
            if not afm:
                afm = pdf_afm or page_html_afm or None
            debug['final_afm'] = afm
            debug['final_amka'] = amka
        except Exception:
            try:
                debug['final_afm'] = pdf_afm or page_html_afm or None
                debug['final_amka'] = pdf_amka
            except Exception:
                pass

        await browser.close()
        return {'pdf_path': str(outp), 'afm': afm, 'amka': amka}


async def run(username, password, year, output_path, headless=True, name=None, afm_hint: str = None):
    try:
        return await _run_impl(username, password, year, output_path, headless=headless, name=name, afm_hint=afm_hint)
    except RuntimeError as e:
        if headless and 'Could not obtain PDF' in str(e):
            # retry once in headed mode
            storage_path = Path(output_path).parent / 'screenshots' / 'storage_state.json'
            if storage_path.exists():
                return await _run_impl(username, password, year, output_path, headless=False, name=name, initial_storage=str(storage_path), afm_hint=afm_hint)
            return await _run_impl(username, password, year, output_path, headless=False, name=name, afm_hint=afm_hint)
        raise


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--username', required=True)
    parser.add_argument('--password', required=True)
    parser.add_argument('--year', default='2025')
    parser.add_argument('--output', default='downloads/e1_extracted.pdf')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--pause-after-login', action='store_true', help='Pause after login to inspect the headed browser')
    parser.add_argument('--afm-hint', default=None, help='Optional AFM to help disambiguate AMKA extraction')
    args = parser.parse_args()
    try:
        # support interactive pause by attaching attribute to impl function
        if args.pause_after_login:
            setattr(_run_impl, '_pause_after_login', True)
        res = asyncio.run(run(args.username, args.password, args.year, args.output, headless=args.headless, afm_hint=args.afm_hint))
        print(json.dumps(res, ensure_ascii=False, indent=2))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise