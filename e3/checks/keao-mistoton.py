"""KEAO credits/payments extractor across every non-employer registry.

Logs into the KEAO portal via TAXISNET, opens the registry-selection
picker, and for **every registry except «Ι.Κ.Α. ΕΡΓΟΔΟΤΗΣ»** walks the
«Κινήσεις Οφειλέτη → Πιστώσεις Οφειλών» grid with a date filter,
screenshotting just the «Ηλεκτρονική Καρτέλα Οφειλέτη» card (no
sidebar / no govgr header) on every paginated screen and stitching
them into a per-registry PDF. Per-row amounts are also parsed and
summed so the caller gets totals for the date range without opening
the PDF.

Handles the empty paths the KEAO UI exposes per registry:
  - the registry has no «Α.Μ.Ο.» banner («Δεν βρέθηκε Αριθμός Μητρώου
    Οφειλέτη για τον Α.Φ.Μ.»),
  - the credits tab renders but the grid is empty.

Both cases short-circuit cleanly for that registry and the script
moves on to the next one.
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import (
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DEFAULT_TIMEOUT = 30000
NAV_TIMEOUT = 45000
SHORT_WAIT = 600

KEAO_ENTRY_URL = "https://www.e-efka.gov.gr/el/elektronikes-yperesies/ilektronikes-ypiresies-keao"

NO_AMO_TEXT = "Δεν βρέθηκε Αριθμός Μητρώου Οφειλέτη"
EMPLOYER_TOKEN = "ΕΡΓΟΔΟΤ"  # excludes any «ΕΡΓΟΔΟΤΗΣ» variant


def _to_float(text: str) -> float:
    """Parse Greek-formatted amount like «1.234,56» to float."""
    if not text:
        return 0.0
    t = text.strip().replace("\xa0", " ")
    t = re.sub(r"[^\d,.\-]", "", t)
    if not t:
        return 0.0
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return 0.0


def _parse_page_info(text: str):
    """Return (current, total) parsed from «(Σελίδα X από N)». None on miss."""
    m = re.search(r"Σελίδα\s+(\d+)\s+από\s+(\d+)", text or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _safe_filename(s: str, fallback: str = "registry") -> str:
    s = (s or "").strip()
    if not s:
        return fallback
    # Drop diacritic-less ASCII path-hostile chars while keeping Greek letters.
    s = re.sub(r'[\\/:*?"<>|]+', "_", s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_.")
    return (s or fallback)[:120]


def _login_taxisnet(page: Page, username: str, password: str, afm: str) -> Page:
    """Log into KEAO via TAXISNET. Returns the post-login KEAO page."""
    page.goto(KEAO_ENTRY_URL, timeout=NAV_TIMEOUT)
    with page.context.expect_page() as popup_info:
        page.get_by_role("link", name="Είσοδος στην υπηρεσία").click()
    popup = popup_info.value
    popup.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)

    taxis_btn = popup.get_by_role("button", name="Συνέχεια στο TAXISNET")
    if taxis_btn.count():
        taxis_btn.first.click()
        popup.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)

    user_box = popup.get_by_role("textbox", name="Χρήστης:")
    if user_box.count() == 0:
        user_box = popup.locator("input[name='j_username'], input[name*='user' i]")
    pwd_box = popup.get_by_role("textbox", name="Κωδικός:")
    if pwd_box.count() == 0:
        pwd_box = popup.locator("input[type='password']")
    user_box.first.fill(username)
    pwd_box.first.fill(password)

    login_btn = popup.get_by_role("button", name="Σύνδεση")
    if login_btn.count() == 0:
        login_btn = popup.locator("button:has-text('Σύνδεση'), input[type='submit']")
    login_btn.first.click()
    popup.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)

    radios = popup.get_by_role("radio")
    if radios.count() >= 2:
        radios.nth(1).check()
        send_btn = popup.get_by_role("button", name="Αποστολή")
        if send_btn.count():
            send_btn.first.click()
            popup.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)

    afm_box = popup.get_by_role("textbox", name="ΑΦΜ:")
    if afm_box.count():
        afm_box.first.fill(afm)
        enter_btn = popup.get_by_role("button", name="Είσοδος")
        if enter_btn.count() == 0:
            enter_btn = popup.locator("button:has-text('Είσοδος')")
        enter_btn.first.click()
        popup.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)

    return popup


def _open_registry_picker(page: Page) -> Page:
    """Click «Επιλογή Μητρώου» and return the resulting page."""
    link = page.locator("a").filter(has_text="Επιλογή Μητρώου").first
    link.wait_for(state="visible", timeout=NAV_TIMEOUT)
    try:
        with page.context.expect_page(timeout=8000) as picker_info:
            link.click()
        picker = picker_info.value
    except PlaywrightTimeoutError:
        picker = page
    picker.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
    picker.wait_for_selector("#dataTable_data", timeout=DEFAULT_TIMEOUT)
    return picker


def _list_registry_rows(picker: Page) -> list[dict]:
    """Return list of {forea, amo, epwnymia} for every registry row."""
    rows = picker.locator("#dataTable_data tr")
    n = rows.count()
    out = []
    for i in range(n):
        cells = rows.nth(i).locator("td")
        if cells.count() < 4:
            continue
        try:
            amo = cells.nth(0).inner_text(timeout=2000).strip()
            forea = cells.nth(1).inner_text(timeout=2000).strip()
            epwnymia = cells.nth(3).inner_text(timeout=2000).strip()
        except Exception:
            continue
        out.append({"forea": forea, "amo": amo, "epwnymia": epwnymia})
    return out


def _click_select_for_amo(picker: Page, amo: str) -> bool:
    """Click «Επιλογή» on the row whose Α.Μ.Ο. matches. Returns True on click."""
    rows = picker.locator("#dataTable_data tr")
    n = rows.count()
    for i in range(n):
        cells = rows.nth(i).locator("td")
        if cells.count() < 1:
            continue
        try:
            row_amo = cells.nth(0).inner_text(timeout=2000).strip()
        except Exception:
            continue
        if row_amo != amo:
            continue
        btn = rows.nth(i).get_by_role("button", name="Επιλογή")
        if btn.count() == 0:
            btn = rows.nth(i).locator("button:has-text('Επιλογή')")
        if btn.count() == 0:
            return False
        btn.first.click()
        picker.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        return True
    return False


def _back_to_picker(page: Page) -> bool:
    """From a debtor view, click sidebar «Επιλογή Μητρώου» to return to the picker."""
    link = page.locator("a").filter(has_text="Επιλογή Μητρώου").first
    try:
        link.wait_for(state="visible", timeout=NAV_TIMEOUT)
        link.click()
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        page.wait_for_selector("#dataTable_data", timeout=DEFAULT_TIMEOUT)
        return True
    except PlaywrightTimeoutError:
        return False


def _has_no_amo_message(page: Page) -> bool:
    try:
        return page.locator(f"text={NO_AMO_TEXT}").count() > 0
    except Exception:
        return False


def _open_credits_tab(page: Page, date_from: str) -> str:
    """Navigate Κινήσεις Οφειλέτη → Πιστώσεις Οφειλών, fill date, click Εμφάνιση."""
    page.locator("a").filter(has_text="Κινήσεις Οφειλέτη").first.click()
    page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)

    if _has_no_amo_message(page):
        return "no_amo"

    credits_tab = page.get_by_role("link", name="Πιστώσεις Οφειλών")
    if credits_tab.count() == 0:
        return "no_amo"
    credits_tab.first.click()
    page.wait_for_timeout(SHORT_WAIT)

    date_box = page.locator("#dateFrom_input")
    if date_box.count() == 0:
        return "no_amo"
    date_box.first.click()
    date_box.first.fill(date_from)

    show_btn = page.get_by_role("button", name="Εμφάνιση")
    if show_btn.count() == 0:
        return "no_amo"
    show_btn.first.click()
    page.wait_for_timeout(SHORT_WAIT)

    grid_sel = "[id='tabView:linesTable2_data']"
    try:
        page.wait_for_selector(grid_sel, timeout=12000)
    except PlaywrightTimeoutError:
        return "empty"

    rows = page.locator(f"{grid_sel} tr")
    if rows.count() == 0:
        return "empty"
    return "ok"


def _current_page_info(page: Page):
    pag = page.locator("[id='tabView:linesTable2_paginator_bottom']")
    if pag.count() == 0:
        return None
    return _parse_page_info(pag.first.inner_text(timeout=3000))


def _click_next_page(page: Page, target_page: int) -> bool:
    nxt = page.locator(
        "[id='tabView:linesTable2_paginator_bottom']"
    ).get_by_role("link", name="Επόμενη σελίδα")
    if nxt.count() == 0:
        return False
    nxt.first.click()
    for _ in range(20):
        info = _current_page_info(page)
        if info and info[0] == target_page:
            page.wait_for_timeout(300)
            return True
        page.wait_for_timeout(SHORT_WAIT // 2)
    return False


# JS that returns the smallest wrapper containing both the
# «Ηλεκτρονική Καρτέλα Οφειλέτη» heading and the credits #tabView —
# i.e. the card the user wants on the PDF without the sidebar.
_CARD_JS = r"""
() => {
    const tabView = document.querySelector('#tabView');
    if (!tabView) return null;
    const TITLE = 'Ηλεκτρονική Καρτέλα Οφειλέτη';
    let heading = null;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
    while (walker.nextNode()) {
        const t = (walker.currentNode.nodeValue || '').trim();
        if (t === TITLE) { heading = walker.currentNode.parentElement; break; }
    }
    let target = null;
    if (heading) {
        let el = heading;
        while (el && !el.contains(tabView)) el = el.parentElement;
        target = el;
    }
    if (!target) target = tabView.closest('form') || tabView.parentElement;
    if (target && target.tagName === 'BODY') target = tabView.parentElement;
    return target;
}
"""


def _screenshot_card(page: Page, out_path: Path) -> Path:
    """Screenshot just the «Ηλεκτρονική Καρτέλα Οφειλέτη» card."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    handle = page.evaluate_handle(_CARD_JS)
    elt = handle.as_element()
    if elt is not None:
        try:
            elt.scroll_into_view_if_needed()
            page.wait_for_timeout(150)
            elt.screenshot(path=str(out_path))
            return out_path
        except Exception as exc:
            logging.warning("card screenshot fell back to #content (%s)", exc)
    content = page.locator("#content")
    if content.count():
        content.first.screenshot(path=str(out_path))
    else:
        page.screenshot(path=str(out_path), full_page=True)
    return out_path


def _extract_credit_rows(page: Page) -> list[dict]:
    rows_loc = page.locator("[id='tabView:linesTable2_data'] tr")
    n = rows_loc.count()
    out = []
    for i in range(n):
        cells = rows_loc.nth(i).locator("td")
        c = cells.count()
        if c < 8:
            continue
        try:
            raw = [cells.nth(j).inner_text(timeout=2000).strip() for j in range(c)]
        except Exception:
            continue
        out.append({
            "kod_yp": raw[0],
            "date": raw[1],
            "doc": raw[2],
            "kod_kin": raw[3],
            "total": _to_float(raw[4]),
            "main_contrib": _to_float(raw[5]),
            "extra_fees": _to_float(raw[6]),
            "surcharges": _to_float(raw[7]),
        })
    return out


def _row_in_year(row: dict, year: int) -> bool:
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", row.get("date", ""))
    if not m:
        return False
    return int(m.group(3)) == year


def _png_to_rgb(path: Path) -> Image.Image:
    img = Image.open(path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


def _stitch_pdf(png_paths: list[Path], pdf_path: Path) -> Path:
    if not png_paths:
        return pdf_path
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    images = [_png_to_rgb(p) for p in png_paths]
    first, rest = images[0], images[1:]
    first.save(str(pdf_path), "PDF", save_all=True, append_images=rest)
    return pdf_path


def _process_registry(picker: Page, reg: dict, date_from: str, year: int,
                      output_dir: Path) -> dict:
    """Drive one registry through the credits tab and emit a PDF + totals."""
    safe = _safe_filename(reg["forea"], fallback=f"amo_{reg['amo']}")
    reg_dir = output_dir / "shots" / safe
    reg_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "forea": reg["forea"],
        "amo": reg["amo"],
        "epwnymia": reg["epwnymia"],
        "status": "unknown",
        "pages_captured": 0,
        "pdf": None,
        "rows": [],
        "totals_year": {"total": 0.0, "main_contrib": 0.0, "extra_fees": 0.0, "surcharges": 0.0},
        "totals_all": {"total": 0.0, "main_contrib": 0.0, "extra_fees": 0.0, "surcharges": 0.0},
    }

    if not _click_select_for_amo(picker, reg["amo"]):
        record["status"] = "select_button_missing"
        return record

    if _has_no_amo_message(picker):
        record["status"] = "no_amo"
        return record

    state = _open_credits_tab(picker, date_from)
    if state == "no_amo":
        record["status"] = "no_amo"
        return record
    if state == "empty":
        record["status"] = "no_credits"
        return record

    info = _current_page_info(picker) or (1, 1)
    _, total_pages = info
    logging.info("[%s] credits table: %d page(s)", reg["forea"], total_pages)

    shot_paths: list[Path] = []
    all_rows: list[dict] = []
    idx = 1
    while True:
        shot = reg_dir / f"page_{idx:02d}.png"
        _screenshot_card(picker, shot)
        shot_paths.append(shot)
        all_rows.extend(_extract_credit_rows(picker))
        record["pages_captured"] = idx
        if idx >= total_pages:
            break
        if not _click_next_page(picker, idx + 1):
            break
        idx += 1

    pdf_path = output_dir / f"keao_pistwseis_{safe}.pdf"
    _stitch_pdf(shot_paths, pdf_path)
    record["pdf"] = str(pdf_path)
    record["rows"] = all_rows

    for r in all_rows:
        for k in ("total", "main_contrib", "extra_fees", "surcharges"):
            record["totals_all"][k] += r[k]
        if _row_in_year(r, year):
            for k in ("total", "main_contrib", "extra_fees", "surcharges"):
                record["totals_year"][k] += r[k]

    record["status"] = "ok"
    return record


def run(playwright, username: str, password: str, afm: str,
        date_from: str, year: int, output_dir: Path, headless: bool) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

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

    browser = playwright.chromium.launch(headless=headless, args=_svfb_args())
    context = browser.new_context(
        locale="el-GR",
        viewport={"width": 1440, "height": 1000},
        accept_downloads=True,
    )
    page = context.new_page()

    result = {
        "status": "unknown",
        "year": year,
        "date_from": date_from,
        "registries": [],
        "totals_year": {"total": 0.0, "main_contrib": 0.0, "extra_fees": 0.0, "surcharges": 0.0},
        "totals_all": {"total": 0.0, "main_contrib": 0.0, "extra_fees": 0.0, "surcharges": 0.0},
    }

    try:
        keao_page = _login_taxisnet(page, username, password, afm)
        picker = _open_registry_picker(keao_page)

        all_registries = _list_registry_rows(picker)
        if not all_registries:
            result["status"] = "no_registries"
            return result

        targets = [r for r in all_registries if EMPLOYER_TOKEN not in r["forea"].upper()]
        logging.info("Found %d registries; %d non-employer to process",
                     len(all_registries), len(targets))

        for i, reg in enumerate(targets):
            if i > 0:
                # The picker page has navigated to a debtor view from the
                # previous iteration — go back to the picker before the
                # next selection.
                if not _back_to_picker(picker):
                    logging.warning("Could not return to registry picker before %s", reg["forea"])
                    break

            try:
                rec = _process_registry(picker, reg, date_from, year, output_dir)
            except Exception as exc:
                logging.exception("Registry %s failed: %s", reg["forea"], exc)
                rec = {**reg, "status": f"error: {exc}", "pages_captured": 0,
                       "pdf": None, "rows": [],
                       "totals_year": {"total": 0.0, "main_contrib": 0.0, "extra_fees": 0.0, "surcharges": 0.0},
                       "totals_all": {"total": 0.0, "main_contrib": 0.0, "extra_fees": 0.0, "surcharges": 0.0}}
            result["registries"].append(rec)
            for k in ("total", "main_contrib", "extra_fees", "surcharges"):
                result["totals_year"][k] += rec["totals_year"][k]
                result["totals_all"][k] += rec["totals_all"][k]

        result["status"] = "ok"
        return result
    finally:
        try:
            context.close()
        finally:
            browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="KEAO non-employer credits extractor.")
    parser.add_argument("--username", default=os.getenv("KEAO_USER", ""))
    parser.add_argument("--password", default=os.getenv("KEAO_PASSWORD", ""))
    parser.add_argument("--afm", default=os.getenv("KEAO_AFM", ""))
    parser.add_argument("--date-from", default="01/01/2025")
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--output-dir", default="keao_out")
    parser.add_argument("--output-json", default="keao_out.json")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    headless = True
    if args.headed:
        headless = False
    if args.headless:
        headless = True

    afm = args.afm or args.username

    with sync_playwright() as p:
        result = run(p, args.username, args.password, afm,
                     args.date_from, args.year, Path(args.output_dir), headless)

    Path(args.output_json).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
