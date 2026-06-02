"""Exploratory script: log in to e-EFKA (and a sibling TEKA page),
reach the certificates table, and dump enough info about the
right-most action column to plan a robust PDF download.

Not meant for production. Run once with real TAXISNET credentials and
inspect the JSON + HTML + screenshots that end up under ./screenshots/.
"""
import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

try:
    from e3.checks import chromium_launch_args
except Exception:  # pragma: no cover
    def chromium_launch_args():
        return [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-software-rasterizer",
        ]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ROOT = Path(__file__).resolve().parent / "screenshots"
ROOT.mkdir(parents=True, exist_ok=True)


def clean(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip()


def shoot(page, name: str):
    p = ROOT / name
    try:
        page.screenshot(path=str(p), full_page=True)
        logging.info("Screenshot: %s", p)
    except Exception as e:
        logging.warning("Screenshot %s failed: %s", p, e)


def dump_html(page, name: str):
    p = ROOT / name
    try:
        p.write_text(page.content(), encoding="utf-8")
        logging.info("HTML dump: %s", p)
    except Exception as e:
        logging.warning("HTML dump %s failed: %s", p, e)


def login_taxisnet(page, username, password, amka, label):
    """Navigate from an e-EFKA listing page through TAXISNET login.
    Returns the popup Page that ends up on the IDIKA certificates listing.
    """
    link = page.locator("a", has_text=re.compile(r"Βεβαιώσεις Εισφορών", re.I))
    if link.count() == 0:
        logging.warning("[%s] EFKA/TEKA link not found", label)
        shoot(page, f"{label}_landing.png")
        return None

    with page.expect_popup(timeout=15000) as info:
        link.first.scroll_into_view_if_needed()
        link.first.click()
    page1 = info.value
    page1.wait_for_load_state("networkidle", timeout=20000)
    shoot(page1, f"{label}_after_link.png")

    # TAXISNET continue button (variants)
    for sel in [
        "button:has-text('Συνέχεια στο TAXISNET')",
        "a:has-text('Συνέχεια στο TAXISNET')",
        "button:has-text('Συνέχεια')",
    ]:
        btn = page1.locator(sel).first
        if btn.count():
            try:
                with page1.expect_navigation(timeout=15000):
                    btn.click()
                break
            except PlaywrightTimeoutError:
                try:
                    btn.click()
                except Exception:
                    pass
                break
    page1.wait_for_load_state("networkidle", timeout=20000)
    shoot(page1, f"{label}_taxisnet_login.png")

    user_field = page1.locator("input[name*='username' i], input[id*='username' i], input[placeholder*='Χρήστ' i]").first
    pass_field = page1.locator("input[type='password']").first
    if user_field.count() and pass_field.count():
        user_field.fill(username)
        pass_field.fill(password)
        submit = page1.locator("#btn-login-submit, button:has-text('Σύνδεση'), input[type='submit']").first
        try:
            with page1.expect_navigation(timeout=20000):
                submit.click()
        except PlaywrightTimeoutError:
            try:
                submit.click()
            except Exception:
                pass
    page1.wait_for_load_state("networkidle", timeout=20000)
    shoot(page1, f"{label}_post_login.png")

    # Authorization radio + Αποστολή
    if page1.locator("input[type='radio']").count() > 1:
        try:
            page1.locator("input[type='radio']").nth(1).check()
        except Exception:
            pass
        submit_btn = page1.locator("button:has-text('Αποστολή'), input[type='submit'][value*='Αποστολ']").first
        if submit_btn.count():
            try:
                with page1.expect_navigation(timeout=20000):
                    submit_btn.click()
            except PlaywrightTimeoutError:
                try:
                    submit_btn.click()
                except Exception:
                    pass
        page1.wait_for_load_state("networkidle", timeout=20000)
        shoot(page1, f"{label}_after_consent.png")

        # AMKA prompt
        amka_field = page1.locator("input[name*='AMKA' i], input[id*='AMKA' i]").first
        if amka_field.count():
            amka_field.fill(amka)
            enter = page1.locator("button:has-text('Είσοδος'), input[type='submit'][value*='σοδος']").first
            if enter.count():
                try:
                    with page1.expect_navigation(timeout=20000):
                        enter.click()
                except PlaywrightTimeoutError:
                    try:
                        enter.click()
                    except Exception:
                        pass
            page1.wait_for_load_state("networkidle", timeout=20000)
            shoot(page1, f"{label}_after_amka.png")
    return page1


def find_table(page, label):
    # Try clicking the certificates section first.
    for sel in [
        f"section div:has-text('Φορολογικές Βεβαιώσεις')",
        "div:has-text('Φορολογικές Βεβαιώσεις')",
    ]:
        loc = page.locator(sel)
        if loc.count():
            try:
                loc.nth(min(4, loc.count() - 1)).click(timeout=5000)
                page.wait_for_load_state("networkidle", timeout=10000)
                break
            except Exception:
                continue

    try:
        page.wait_for_selector("#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable", timeout=30000)
    except PlaywrightTimeoutError:
        logging.warning("[%s] Table did not appear", label)
        dump_html(page, f"{label}_no_table.html")
        return None
    shoot(page, f"{label}_table.png")
    dump_html(page, f"{label}_table.html")
    return page.locator("#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable")


def inspect_action_column(page, label):
    table = page.locator("#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable")
    if not table.count():
        return {}

    info = {"label": label, "rows": []}
    rows = page.locator("#ContentPlaceHolder1_TaxNotificationsGrid_DXMainTable tr[class*='dxgvDataRow']")
    n = min(rows.count(), 5)
    for i in range(n):
        row = rows.nth(i)
        cells = row.locator("td")
        m = cells.count()
        last = cells.nth(m - 1)
        try:
            html = last.inner_html()
        except Exception:
            html = ""
        try:
            txt = clean(last.inner_text())
        except Exception:
            txt = ""
        anchors = []
        for j in range(last.locator("a, button, input[type='button'], input[type='submit'], [onclick]").count()):
            el = last.locator("a, button, input[type='button'], input[type='submit'], [onclick]").nth(j)
            try:
                anchors.append({
                    "tag": el.evaluate("e => e.tagName"),
                    "text": clean(el.inner_text()),
                    "href": el.get_attribute("href"),
                    "onclick": el.get_attribute("onclick"),
                    "id": el.get_attribute("id"),
                    "class": el.get_attribute("class"),
                    "title": el.get_attribute("title"),
                    "aria_label": el.get_attribute("aria-label"),
                })
            except Exception:
                continue
        info["rows"].append({
            "cell_count": m,
            "last_text": txt,
            "last_html": html[:2000],
            "actionables": anchors,
        })
    out = ROOT / f"{label}_action_column.json"
    out.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Action column inspection: %s", out)
    return info


def run_one(label, landing_url, username, password, amka, headless):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=chromium_launch_args())
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="el-GR",
            viewport={"width": 1600, "height": 1100},
            accept_downloads=True,
        )
        page = ctx.new_page()
        try:
            logging.info("[%s] Goto %s", label, landing_url)
            page.goto(landing_url, timeout=30000)
            page.wait_for_load_state("networkidle", timeout=20000)
            shoot(page, f"{label}_landing.png")
            cert_page = login_taxisnet(page, username, password, amka, label)
            target = cert_page or page
            if find_table(target, label) is not None:
                inspect_action_column(target, label)
                # Try clicking the right-most actionable for the first row and
                # capturing any popup/download.
                # Print buttons are DevExpress custom buttons.
                # Each row has <a id="..._DXCBtnN"> with an inner
                # <img title="Εκτύπωση Φορολογικής Βεβαίωσης" ...>. Their
                # data-args carry ['CustomButton','btnPrintEidop',rowIdx].
                try:
                    print_btn = target.locator("a:has(img[title*='Εκτύπωση'])").first
                    if not print_btn.count():
                        # fallback: image then closest A
                        print_img = target.locator("img[title*='Εκτύπωση Φορολογικής Βεβαίωσης']").first
                        print_btn = print_img.locator("xpath=ancestor::a[1]") if print_img.count() else None
                    if print_btn and print_btn.count():
                        # Print clicks usually open a popup; also handle direct download.
                        download_path = None
                        popup_url = None
                        try:
                            with target.expect_popup(timeout=15000) as popup_info:
                                print_btn.first.click()
                            popup = popup_info.value
                            popup.wait_for_load_state("domcontentloaded", timeout=20000)
                            popup_url = popup.url
                            logging.info("[%s] Print popup URL: %s", label, popup_url)
                            try:
                                with popup.expect_download(timeout=15000) as dl_info:
                                    pass
                                dl = dl_info.value
                                download_path = ROOT / f"{label}_row0.pdf"
                                dl.save_as(str(download_path))
                                logging.info("[%s] Downloaded PDF: %s", label, download_path)
                            except PlaywrightTimeoutError:
                                shoot(popup, f"{label}_popup.png")
                                dump_html(popup, f"{label}_popup.html")
                        except PlaywrightTimeoutError:
                            # No popup: maybe a download attached to the same page
                            try:
                                with target.expect_download(timeout=8000) as dl_info:
                                    print_btn.first.click()
                                dl = dl_info.value
                                download_path = ROOT / f"{label}_row0.pdf"
                                dl.save_as(str(download_path))
                                logging.info("[%s] Downloaded PDF inline: %s", label, download_path)
                            except PlaywrightTimeoutError:
                                logging.info("[%s] No popup or download — capturing screenshot", label)
                                shoot(target, f"{label}_after_print_click.png")
                    else:
                        logging.info("[%s] No print button locator hit", label)
                except Exception as e:
                    logging.info("[%s] Print click error: %s", label, e)
        except Exception as e:
            logging.exception("[%s] FAILED: %s", label, e)
            try:
                shoot(page, f"{label}_error.png")
            except Exception:
                pass
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default=os.getenv("TX_USER", ""))
    parser.add_argument("--password", default=os.getenv("TX_PASS", ""))
    parser.add_argument("--amka", default=os.getenv("TX_AMKA", "22096604537"))
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--only", choices=["efka", "teka", "both"], default="both")
    args = parser.parse_args()

    if not args.username or not args.password:
        print("Need --username and --password (or TX_USER/TX_PASS env).", file=sys.stderr)
        sys.exit(2)

    targets = []
    if args.only in ("efka", "both"):
        targets.append((
            "efka",
            "https://www.e-efka.gov.gr/el/elektronikes-yperesies/bebaiose-eisphoron-gia-phorologike-chrese",
        ))
    if args.only in ("teka", "both"):
        targets.append((
            "teka",
            "https://www.e-efka.gov.gr/el/elektronikes-yperesies/bebaiose-eisphoron-gia-phorologike-chrese",
        ))
    for label, url in targets:
        run_one(label, url, args.username, args.password, args.amka, headless=not args.headed)


if __name__ == "__main__":
    main()
