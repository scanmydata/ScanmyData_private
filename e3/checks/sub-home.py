import asyncio
import re
from playwright.async_api import Playwright, async_playwright, expect


async def run(playwright: Playwright) -> None:
    browser = await playwright.chromium.launch(headless=False)
    context = await browser.new_context()
    await page.goto("https://www.gov.gr/upourgeia/oloi-foreis/anexartete-arkhe-demosion-esodon-aade/bebaiose-phorologikou-metroou")
    await expect(page.locator("[id=\"__layout\"]")).to_match_aria_snapshot("- link \"Είσοδος στην υπηρεσία\":\n  - /url: https://www1.aade.gr/saadeapps3/comregistry\n  - img")
    await page.get_by_role("link", name="Είσοδος στην υπηρεσία").click()
    await page.get_by_role("textbox", name="Όνομα χρήστη").click()
    await page.get_by_role("textbox", name="Όνομα χρήστη").fill("802576637")
    await page.get_by_role("textbox", name="Κωδικός πρόσβασης").click()
    await page.get_by_role("textbox", name="Κωδικός πρόσβασης").fill("Tv802576!")
    await page.get_by_role("button", name="Συνδεση").click()
    await page.get_by_text("Βεβαιώσεις Μητρώου", exact=True).click()
    await expect(page.locator("body")).to_match_aria_snapshot("- text: Τρέχουσα Εικόνα Οντότητας/Επιχείρησης")
    await page.get_by_text("Τρέχουσα ΕικόναΟντότητας/Επιχείρησης").nth(2).click()
    await page.locator("#myselect1").select_option("flagegkeswt")
    await page.locator("#myselect1").select_option("flagegkatastaseisexwterikoy")
    await expect(page.locator("section:nth-child(14) > .container-fluid > .row > .panel > .panel-heading")).to_be_visible()
    await expect(page.locator("section:nth-child(15) > .container-fluid > .row > .panel > .panel-heading")).to_be_visible()
    await expect(page.locator("body")).to_match_aria_snapshot("- text: Δεν βρέθηκαν εγγραφές")
    await expect(page.locator("body")).to_match_aria_snapshot("- text: Δεν βρέθηκαν εγγραφές")
    await page.get_by_role("link", name="User Image ΤΟ ΒΑΨΙΜΟ Ε Ε").click()
    await page.get_by_role("link", name="Αποσύνδεση").click()
    await page.get_by_role("textbox", name="Όνομα χρήστη").fill("802576637")

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    async with async_playwright() as playwright:
        await run(playwright)


asyncio.run(main())
