"""sub_home.py — fetch branch (υποκαταστήματα) addresses from AADE.

Logs into the AADE Σύστημα Μητρώου (saadeapps3/comregistry) with the
company TAXIS credentials and pulls the "Εγκαταστάσεις Εσωτερικού" and
"Εγκαταστάσεις Εξωτερικού" sections under "Τρέχουσα Εικόνα
Οντότητας/Επιχείρησης". Outputs a JSON document the brain / UI can pick
the branch addresses from.

Usage::

    python sub_home.py --username WW... --password ... \
        --output sub_home.json [--headless]

The previous version was a literal Playwright recording with hard-coded
credentials and a `page` variable that was never created. This module
replaces it with a properly parameterised, headless-capable scraper.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import (
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

GOVGR_URL = (
    "https://www.gov.gr/upourgeia/oloi-foreis/anexartete-arkhe-demosion-esodon-aade/"
    "bebaiose-phorologikou-metroou"
)
COMREG_URL = "https://www1.aade.gr/saadeapps3/comregistry"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    s = re.sub(r"\s+", " ", text).strip()
    return s


async def _login(page, username: str, password: str) -> None:
    """Open the gov.gr landing and authenticate against AADE comregistry."""
    logging.info("Opening gov.gr landing page")
    await page.goto(GOVGR_URL, timeout=45000)
    try:
        await page.wait_for_load_state("networkidle", timeout=20000)
    except PlaywrightTimeoutError:
        pass

    entry = page.get_by_role("link", name="Είσοδος στην υπηρεσία")
    if await entry.count() == 0:
        entry = page.locator(f"a[href*='saadeapps3/comregistry']")
    if await entry.count() == 0:
        # Direct fallback — go straight to the comregistry sign-in.
        logging.warning("gov.gr entry link missing; opening comregistry directly.")
        await page.goto(COMREG_URL, timeout=45000)
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except PlaywrightTimeoutError:
            pass
    else:
        # The gov.gr link may open a new window; wait for either.
        try:
            async with page.context.expect_page(timeout=20000) as new_page_info:
                await entry.first.click()
            popup = await new_page_info.value
            await popup.wait_for_load_state("domcontentloaded", timeout=30000)
            # Caller expected to drive `page`; switch context.
            page = popup  # noqa: F841 — caller passes object back via closure
        except PlaywrightTimeoutError:
            # Same-tab navigation
            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except PlaywrightTimeoutError:
                pass

    # Some AADE flows redirect through TAXISnet GSIS again — handle both.
    user_input = page.get_by_role("textbox", name="Όνομα χρήστη")
    if await user_input.count() == 0:
        user_input = page.locator("input[name='username'], input[name='j_username']")
    pass_input = page.get_by_role("textbox", name="Κωδικός πρόσβασης")
    if await pass_input.count() == 0:
        pass_input = page.locator("input[type='password']")

    if await user_input.count() and await pass_input.count():
        await user_input.first.fill(username)
        await pass_input.first.fill(password)
        login_btn = page.get_by_role("button", name="Σύνδεση")
        if await login_btn.count() == 0:
            login_btn = page.get_by_role("button", name="Συνδεση")
        if await login_btn.count() == 0:
            login_btn = page.locator("button[name='btn_login'], input[type='submit']")
        if await login_btn.count():
            await login_btn.first.click()
            try:
                await page.wait_for_load_state("networkidle", timeout=30000)
            except PlaywrightTimeoutError:
                pass
        else:
            raise RuntimeError("Δεν βρέθηκε κουμπί σύνδεσης στο comregistry.")
    else:
        raise RuntimeError(
            "Δεν βρέθηκαν τα πεδία 'Όνομα χρήστη' / 'Κωδικός' στη σελίδα της AADE."
        )


async def _ng_click(page, ng_click_value: str) -> bool:
    """Invoke an AngularJS ng-click handler the most reliable way available.

    Plain Playwright clicks sometimes land on an inner span/div whose
    click doesn't reach the element with `ng-click`; AngularJS then
    never schedules the action. We try a DOM click first (cheap) then
    fall back to invoking the scope function via Angular itself.
    """
    sel = f"[ng-click=\"{ng_click_value}\"]"
    loc = page.locator(sel)
    n = await loc.count()
    for i in range(n):
        target = loc.nth(i)
        try:
            if not await target.is_visible():
                continue
            disabled = await target.get_attribute("disabled")
            if disabled is not None:
                continue
            await target.scroll_into_view_if_needed(timeout=2000)
            await target.click(timeout=4000)
            return True
        except Exception:
            continue
    try:
        result = await page.evaluate(
            r"""(expr) => {
                if (!window.angular) return 'no-angular';
                const el = document.querySelector("[ng-click=\"" + expr + "\"]");
                if (!el) return 'no-element';
                const scope = angular.element(el).scope();
                if (!scope) return 'no-scope';
                const m = expr.match(/^(\w+)\(([-\d, ]*)\)$/);
                if (!m) return 'unparsed';
                const fname = m[1];
                const args = m[2] ? m[2].split(',').map(x => Number(x.trim())) : [];
                let target = scope;
                while (target && typeof target[fname] !== 'function') target = target.$parent;
                if (!target || typeof target[fname] !== 'function') return 'no-fn';
                scope.$apply(() => target[fname].apply(target, args));
                return 'ok';
            }""",
            ng_click_value,
        )
        return result == "ok"
    except Exception:
        return False


async def _open_current_entity_view(page, debug_dir: Optional[Path] = None) -> None:
    """Navigate to «Τρέχουσα Εικόνα Οντότητας/Επιχείρησης»."""
    # Step 1 — open the «Βεβαιώσεις Μητρώου» dashboard. The post-login
    # homepage exposes that card via ng-click="menu(16)".
    if not await _ng_click(page, "menu(16)"):
        bev_link = page.get_by_text("Βεβαιώσεις Μητρώου", exact=True)
        if await bev_link.count():
            try:
                await bev_link.first.click(timeout=5000)
            except Exception:
                pass
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        pass
    try:
        await page.wait_for_selector(
            "text=Τρέχουσα Εικόνα", timeout=8000
        )
    except PlaywrightTimeoutError:
        logging.info("Βεβαιώσεις dashboard didn't render the entity cards.")

    # Step 2 — click the «Τρέχουσα Εικόνα Οντότητας/Επιχείρησης» card on
    # the Βεβαιώσεις Μητρώου dashboard. That card has ng-click="menu(2)"
    # (in both the mflag==2 and mflag==3 sections). We try the AngularJS
    # scope invocation first because earlier tests showed that synthetic
    # clicks on the inner label do not always fire ng-click.
    candidate_selectors = [
        "div[ng-click='menu(2)']:not([disabled])",
        ".btn1[ng-click='menu(2)']",
        # Last-ditch fallback: any element whose visible text matches.
        "a:has-text('Τρέχουσα Εικόνα Οντότητας')",
        ".panel-heading:has-text('Τρέχουσα Εικόνα Οντότητας')",
        "text=Τρέχουσα Εικόνα Οντότητας/Επιχείρησης",
    ]
    clicked = False
    for sel in candidate_selectors:
        if clicked:
            break
        loc = page.locator(sel)
        n = await loc.count()
        for i in range(n):
            target = loc.nth(i)
            try:
                if not await target.is_visible():
                    continue
                # Skip disabled-looking clones in hidden ng-show=mflag=X
                # branches (Angular leaves them in the DOM).
                disabled = await target.get_attribute("disabled")
                if disabled is not None:
                    continue
                await target.scroll_into_view_if_needed(timeout=2000)
                await target.click(timeout=4000)
                clicked = True
                break
            except Exception:
                continue

    if not clicked:
        # Dump debug artefacts so we can iterate without re-logging in.
        if debug_dir is not None:
            try:
                debug_dir.mkdir(parents=True, exist_ok=True)
                html = await page.content()
                (debug_dir / "comreg_after_login.html").write_text(html, encoding="utf-8")
                await page.screenshot(path=str(debug_dir / "comreg_after_login.png"), full_page=True)
                logging.info("Debug artefacts: %s", debug_dir)
            except Exception:
                pass
        raise RuntimeError("Δεν βρέθηκε ο σύνδεσμος «Τρέχουσα Εικόνα Οντότητας/Επιχείρησης».")
    try:
        await page.wait_for_load_state("networkidle", timeout=20000)
    except PlaywrightTimeoutError:
        pass

    # Even after the Playwright click reports success, AngularJS's
    # `ng-click="menu(2)"` may not fire if the click was synthesised on
    # the wrong element in the (replicated) dashboard. Re-invoke menu(2)
    # via scope as a hard fallback. Then wait for the section dropdown
    # (#myselect1) to be present — that is the marker we're on the new
    # «Τρέχουσα Εικόνα Οντότητας/Επιχείρησης» page.
    try:
        await page.wait_for_selector("#myselect1, select", timeout=4000, state="attached")
    except PlaywrightTimeoutError:
        logging.info("Click probably did not navigate — invoking menu(2) via Angular scope.")
        try:
            await page.evaluate(
                """() => {
                    const el = document.querySelector("[ng-click=\\"menu(2)\\"]") || document.body;
                    const scope = window.angular && angular.element(el).scope();
                    if (scope && typeof scope.menu === 'function') {
                        scope.$apply(() => scope.menu(2));
                    } else if (scope && scope.$parent && typeof scope.$parent.menu === 'function') {
                        scope.$apply(() => scope.$parent.menu(2));
                    }
                }"""
            )
        except Exception as exc:
            logging.info("Angular scope invocation failed: %s", exc)
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except PlaywrightTimeoutError:
            pass


async def _select_section(page, value: str, expected_label: str,
                          debug_dir: Optional[Path] = None) -> None:
    """Drive AngularJS into rendering the section keyed by `value`.

    The page uses a custom `multi-select` directive on #myselect1
    (``selected-options="selectedSection"``); Playwright's
    `select_option` does NOT propagate through it. The cleanest path is
    to set ``$scope.selectedSection`` directly and trigger a digest. We
    also wait for the matching ``<section ng-show="selectedSection
    .includes('<value>')…">`` to lose its `ng-hide` class so the
    subsequent scrape sees the populated rows.
    """
    # First try Angular scope (the only reliable mechanism for the
    # custom multi-select directive used here).
    result = await page.evaluate(
        r"""(value) => {
            if (!window.angular) return 'no-angular';
            // Try the custom multi-select select first, fall back to any.
            const sel = document.querySelector('#myselect1') || document.querySelector('select');
            if (!sel) return 'no-select';
            const scope = angular.element(sel).scope();
            if (!scope) return 'no-scope';
            // Some sections expose selectedSection as an array; others as a string.
            const cur = scope.selectedSection;
            if (Array.isArray(cur)) {
                scope.$apply(() => { scope.selectedSection = [value]; });
            } else if (typeof cur === 'string' || cur == null) {
                scope.$apply(() => { scope.selectedSection = value; });
            } else {
                scope.$apply(() => { scope.selectedSection = [value]; });
            }
            return 'ok';
        }""",
        value,
    )
    if result != "ok":
        # Fallback: try Playwright select_option + a manual change event.
        select = page.locator("select").filter(
            has=page.locator(f"option[value='{value}']")
        )
        if await select.count() == 0:
            select = page.locator("#myselect1")
        if await select.count() == 0:
            if debug_dir is not None:
                try:
                    (debug_dir / f"select_missing_{value}.html").write_text(
                        await page.content(), encoding="utf-8"
                    )
                except Exception:
                    pass
            raise RuntimeError(f"Δεν βρέθηκε dropdown ενότητας για '{value}'.")
        select = select.first
        await select.wait_for(state="attached", timeout=10000)
        await select.select_option(value=value)
        try:
            await select.evaluate("el => el.dispatchEvent(new Event('change', {bubbles: true}))")
        except Exception:
            pass

    # The matching section is `<section ng-show="selectedSection.includes('<value>')…">`.
    section_sel = f"section[ng-show*=\"{value}\"]"
    try:
        await page.wait_for_function(
            """([sel]) => {
                const el = document.querySelector(sel);
                return !!(el && !el.classList.contains('ng-hide'));
            }""",
            arg=[section_sel],
            timeout=20000,
        )
    except PlaywrightTimeoutError:
        logging.info("Section for '%s' did not become visible.", value)
    # The page is fully dynamic — table rows arrive via an XHR after the
    # section is shown. Give it a beat to settle (the user explicitly
    # asked for «λίγη αναμονή»).
    await page.wait_for_timeout(2500)


async def _scrape_section_tables(page, value: str) -> List[Dict[str, Any]]:
    """Scrape tables inside the AngularJS section keyed by `value`.

    The AADE entity view renders one section per dropdown option:
    ``<section ng-show="selectedSection.includes('<value>') && !loading">``.
    Each section embeds a single Bootstrap panel containing the data
    table (gktable). We anchor on the `ng-show` attribute so the panel
    heading text (which differs per section, e.g. «Στοιχεία
    Εγκαταστάσεων Εσωτερικού» vs «Στοιχεία Εγκαταστάσεων Εξωτερικού»)
    doesn't need to be hard-coded.
    """
    section_sel = f"section[ng-show*=\"{value}\"]"
    section = page.locator(section_sel)
    if await section.count() == 0:
        return []
    section = section.first
    tables = section.locator("table.gktable, table")
    n = await tables.count()
    out: List[Dict[str, Any]] = []
    for i in range(n):
        t = tables.nth(i)
        try:
            visible = await t.is_visible()
        except Exception:
            visible = False
        if not visible:
            continue
        headers: List[str] = []
        head_cells = t.locator("thead th, thead td")
        h_n = await head_cells.count()
        for j in range(h_n):
            headers.append(_clean(await head_cells.nth(j).inner_text()))
        if not headers:
            first_row_cells = t.locator("tr").first.locator("th, td")
            f_n = await first_row_cells.count()
            for j in range(f_n):
                headers.append(_clean(await first_row_cells.nth(j).inner_text()))
        rows: List[List[str]] = []
        body_rows = t.locator("tbody tr")
        br_n = await body_rows.count()
        if br_n == 0:
            body_rows = t.locator("tr")
            br_n = await body_rows.count()
        for r in range(br_n):
            cell_texts = await body_rows.nth(r).locator("th, td").all_inner_texts()
            cells = [_clean(c) for c in cell_texts]
            if any(cells):
                rows.append(cells)
        joined = " ".join(c for row in rows for c in row).lower()
        if "δεν υπάρχουν καταχωρημένες εγγραφές" in joined or "δεν βρέθηκαν εγγραφές" in joined:
            out.append({"headers": headers, "rows": [], "empty": True})
            continue
        out.append({"headers": headers, "rows": rows, "empty": False})
    return out


async def _scrape_tables(page) -> List[Dict[str, Any]]:
    """Pull every table that is currently visible and return its rows."""
    out: List[Dict[str, Any]] = []
    tables = page.locator("table")
    n = await tables.count()
    for i in range(n):
        t = tables.nth(i)
        try:
            visible = await t.is_visible()
        except Exception:
            visible = False
        if not visible:
            continue
        headers: List[str] = []
        head_cells = t.locator("thead th, thead td")
        h_n = await head_cells.count()
        for j in range(h_n):
            headers.append(_clean(await head_cells.nth(j).inner_text()))
        if not headers:
            # Fallback to first row cells as header guess.
            first_row_cells = t.locator("tr").first.locator("th, td")
            f_n = await first_row_cells.count()
            for j in range(f_n):
                headers.append(_clean(await first_row_cells.nth(j).inner_text()))

        rows: List[List[str]] = []
        body_rows = t.locator("tbody tr")
        br_n = await body_rows.count()
        if br_n == 0:
            body_rows = t.locator("tr")
            br_n = await body_rows.count()
        for r in range(br_n):
            cell_texts = await body_rows.nth(r).locator("th, td").all_inner_texts()
            cells = [_clean(c) for c in cell_texts]
            if any(cells):
                rows.append(cells)

        # Skip the "Δεν βρέθηκαν εγγραφές" placeholder table.
        joined = " ".join(c for row in rows for c in row).lower()
        if "δεν βρέθηκαν εγγραφές" in joined and len(rows) <= 2:
            out.append({"headers": headers, "rows": [], "empty": True})
            continue

        out.append({"headers": headers, "rows": rows, "empty": False})
    return out


def _strip_greek_accents(s: str) -> str:
    """Drop combining accents so «Είδος».startswith('ειδ') is True."""
    import unicodedata
    if not s:
        return ""
    nf = unicodedata.normalize("NFD", s)
    return "".join(c for c in nf if unicodedata.category(c) != "Mn")


def _rows_to_branches(tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Best-effort row → branch dict mapping.

    The actual AADE table (κατά την επιβεβαίωση 2026-06) has columns:
    «Αριθμός», «Είδος» (e.g. ΕΡΓΑΣΤΗΡΙΟ), «Τίτλος», «Διεύθυνση» (the
    full address blob, e.g. «ΔΑΦΝΙΟΥ 4 ΤΚ:18545 ΠΕΙΡΑΙΑΣ»), «Ημ/νία
    Έναρξης», «Ανάλυση». We map by header substrings (accent-stripped)
    so any column permutation also works.
    """
    out: List[Dict[str, Any]] = []
    for t in tables:
        headers = [h for h in (t.get("headers") or [])]
        headers_norm = [_strip_greek_accents(h).lower() for h in headers]
        if not headers:
            continue
        idx_map: Dict[str, int] = {}
        for i, h in enumerate(headers_norm):
            if h.startswith("αριθμ"):
                idx_map["seq"] = i
            elif h.startswith("ειδ"):
                idx_map["type"] = i
            elif h.startswith("τιτλ"):
                idx_map["title"] = i
            elif "διευθυν" in h:
                idx_map["address"] = i
            elif "εναρ" in h or "απο" in h:
                idx_map["from"] = i
            elif "ληξ" in h or "εως" in h:
                idx_map["to"] = i

        for row in t.get("rows") or []:
            if not row:
                continue
            # Skip header rows that snuck in.
            if all(c == h for c, h in zip(row, headers) if c):
                continue

            def at(key: str) -> str:
                j = idx_map.get(key)
                return row[j] if j is not None and j < len(row) else ""

            address = at("address")
            if not address and len(row) >= 2:
                # No header mapping → take everything after the first cell
                # (usually Αριθμός) as the address blob.
                address = " ".join(c for c in row[1:] if c)
            entry = {
                "type": at("type"),
                "title": at("title"),
                "address": address,
                "valid_from": at("from"),
                "valid_to": at("to"),
                "raw_row": row,
            }
            # Filter out the "Δεν υπάρχουν εγγραφές" placeholder so the UI
            # doesn't show empty branch entries.
            if not entry["type"] and not entry["address"]:
                continue
            placeholder = "δεν υπάρχουν" in " ".join(row).lower()
            if placeholder:
                continue
            out.append(entry)
    return out


async def fetch_sub_home(playwright: Playwright, username: str, password: str,
                         headless: bool = True, debug_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Drive the AADE comregistry session and return the branches payload."""
    try:
        from e3.checks import chromium_launch_args as _svfb_args
    except Exception:
        def _svfb_args():
            return [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
            ]

    browser = await playwright.chromium.launch(headless=headless, args=_svfb_args())
    context = await browser.new_context(
        user_agent=USER_AGENT,
        locale="el-GR",
        viewport={"width": 1366, "height": 900},
        accept_downloads=True,
    )
    page = await context.new_page()

    result: Dict[str, Any] = {
        "headquarter": None,
        "branches_domestic": [],
        "branches_abroad": [],
        "warnings": [],
    }

    try:
        await _login(page, username, password)
        # If login opened a popup, the active tab may have changed.
        if len(context.pages) > 1:
            page = context.pages[-1]

        await _open_current_entity_view(page, debug_dir=debug_dir)

        # Dump the post-click HTML once so we can iterate without re-login.
        if debug_dir:
            try:
                (debug_dir / "after_open.html").write_text(
                    await page.content(), encoding="utf-8"
                )
                await page.screenshot(path=str(debug_dir / "after_open.png"), full_page=True)
            except Exception:
                pass

        # Section A: εγκαταστάσεις εσωτερικού
        try:
            await _select_section(page, "flagegkeswt", "Εγκαταστάσεις Εσωτερικού",
                                  debug_dir=debug_dir)
            if debug_dir:
                (debug_dir / "after_select_domestic.html").write_text(
                    await page.content(), encoding="utf-8"
                )
            tables = await _scrape_section_tables(page, "flagegkeswt")
            result["branches_domestic"] = _rows_to_branches(tables)
            if debug_dir:
                (debug_dir / "domestic.json").write_text(
                    json.dumps(tables, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        except Exception as exc:
            result["warnings"].append(f"Εσωτερικού: {exc}")

        # Section B: εγκαταστάσεις εξωτερικού
        try:
            await _select_section(page, "flagegkatastaseisexwterikoy", "Εγκαταστάσεις Εξωτερικού",
                                  debug_dir=debug_dir)
            if debug_dir:
                (debug_dir / "after_select_abroad.html").write_text(
                    await page.content(), encoding="utf-8"
                )
            tables = await _scrape_section_tables(page, "flagegkatastaseisexwterikoy")
            result["branches_abroad"] = _rows_to_branches(tables)
            if debug_dir:
                (debug_dir / "abroad.json").write_text(
                    json.dumps(tables, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        except Exception as exc:
            result["warnings"].append(f"Εξωτερικού: {exc}")

    finally:
        try:
            await context.close()
        except Exception:
            pass
        await browser.close()

    return result


async def main_async(args: argparse.Namespace) -> int:
    debug_dir = Path(args.debug_dir) if args.debug_dir else None
    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        try:
            data = await fetch_sub_home(
                playwright,
                username=args.username,
                password=args.password,
                headless=args.headless,
                debug_dir=debug_dir,
            )
        except Exception as exc:
            logging.exception("sub_home failed: %s", exc)
            data = {"ok": False, "error": str(exc),
                    "headquarter": None,
                    "branches_domestic": [], "branches_abroad": [], "warnings": []}
        else:
            data["ok"] = True

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logging.info("Saved sub_home output → %s", args.output)
    return 0 if data.get("ok") else 1


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch υποκαταστήματα (εσωτερικού & εξωτερικού) από AADE.")
    p.add_argument("--username", default=os.getenv("AADE_USER"))
    p.add_argument("--password", default=os.getenv("AADE_PASS"))
    p.add_argument("--output", default="sub_home.json")
    p.add_argument("--headless", action="store_true", default=False)
    p.add_argument("--debug-dir", default=None)
    args = p.parse_args()
    if not args.username or not args.password:
        print("Missing --username / --password", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
