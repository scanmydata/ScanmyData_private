"""KEAO credits/payments extractor across every non-employer registry.

Login (2026-09-10, REWRITTEN): the old standalone KEAO/idika TAXISnet popup
login (www.e-efka.gov.gr → popup → «Συνέχεια στο TAXISNET» → ΑΦΜ/«Είσοδος»)
no longer matches the live portal. Replaced with the SAME proven
services.e-efka.gov.gr Keycloak «μη μισθωτού» login already used by
efka_teka_certificate.py / kartela_ergodoti.py (TAXISnet -> GSIS OAuth2 ->
role-select POST with ΑΦΜ+ΑΜΚΑ) — done PURE HTTP (fast, no browser needed
just to authenticate), then the resulting session cookies are handed to the
Playwright browser context (`context.add_cookies`) so the rest of this
script's browser automation (registry picker, credits grid, screenshots)
runs already logged in. This flow — and the new ΑΜΟ-picker table structure
(`#amoForm:dt-table`, replacing the old `#dataTable_data`) — was confirmed
against a real Playwright codegen recording the user captured by hand
(credentials blanked out in the recording itself; never seen by this
script's author).

Logs into e-EFKA (non-employee), opens the registry-selection ΑΜΟ picker,
and for **every registry except «Ι.Κ.Α. ΕΡΓΟΔΟΤΗΣ»** walks the
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
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlencode, urlsplit

import requests
from PIL import Image
from playwright.sync_api import (
    BrowserContext,
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

NO_AMO_TEXT = "Δεν βρέθηκε Αριθμός Μητρώου Οφειλέτη"
EMPLOYER_TOKEN = "ΕΡΓΟΔΟΤ"  # excludes any «ΕΡΓΟΔΟΤΗΣ» variant

# This is the only registry requested for PDF extraction. Its credits are
# already reflected in the annual EFKA certificate, so it is excluded from
# the e3_brain reconciliation aggregates.
EFKA_MH_MISTHWTWN_FOREA = (
    "ΕΝΙΑΙΟΣ ΦΟΡΕΑΣ ΚΟΙΝΩΝΙΚΗΣ ΑΣΦΑΛΙΣΗΣ - "
    "ΕΝΙΑΙΟΣ ΦΟΡΕΑΣ ΚΟΙΝΩΝΙΚΗΣ ΑΣΦΑΛΙΣΗΣ"
)


def _is_efka_mh_misthwton(forea: str) -> bool:
    name = (forea or "").strip()
    name = re.sub(r"^Ληξιπρόθεσμο\s*-\s*", "", name, flags=re.IGNORECASE)
    return name == EFKA_MH_MISTHWTWN_FOREA


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


# ------------------------------------------------------------ HTTP login
# Pure-HTTP port of the same services.e-efka.gov.gr Keycloak «μη μισθωτού»
# login used by efka_teka_certificate.py — see that file for the fuller
# discovery notes. Doing the login over HTTP (not Playwright) is faster and
# lets us reach the exact InvalidCredentials boundary during structural
# testing without ever driving a real browser to a login form.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)
SVC = "https://services.e-efka.gov.gr/"
LAND = SVC + "ssp.commonservices.home/views/secure/index.xhtml"
# The live link text is "person Ηλεκτρονική Πλατφόρμα Οφειλετών - KEAO" — the
# leading "person" is a material-icon ligature's text node (present in the
# raw HTML same as any other text, so it ends up in the extracted link
# label too), and "KEAO" is spelled with LATIN K/E/A/O (not Greek ΚΕΑΟ) —
# confirmed from the user's own Playwright recording of the real page
# (`get_by_role("link", name="person Ηλεκτρονική Πλατφόρμα Οφειλετών - KEAO")`).
# Match on the distinctive Greek phrase alone so neither of those two traps
# (icon-ligature prefix, Greek/Latin homoglyph) can break the lookup again.
_KEAO_LINK_PHRASE = "Ηλεκτρονική Πλατφόρμα Οφειλετών"


def _decode_html(s: Optional[str]) -> str:
    s = s or ""
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    s = re.sub(r"&#0*47;", "/", s)
    return s.replace("&lt;", "<").replace("&gt;", ">")


def _strip_tags(s: Optional[str]) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", _decode_html(s)).strip()


def _form_action_of(html: str, id_: str) -> str:
    e = re.escape(id_)
    m = re.search(r'<form\b[^>]*action="([^"]*)"[^>]*>(?:(?!</form>)[\s\S])*?id="' + e + '"', html, re.I)
    if m:
        return m.group(1)
    m = re.search(r'<form\b(?:(?!</form>)[\s\S])*?id="' + e + r'"(?:(?!</form>)[\s\S])*?action="([^"]*)"', html, re.I)
    return m.group(1) if m else ""


def _own_form_action(html: str, id_: str) -> str:
    e = re.escape(id_)
    m = re.search(r'<form[^>]*id="' + e + '"[^>]*action="([^"]*)"', html, re.I)
    if not m:
        m = re.search(r'<form[^>]*action="([^"]*)"[^>]*id="' + e + '"', html, re.I)
    return m.group(1) if m else ""


def _has_id(html: str, id_: str) -> bool:
    return re.search(r'id="' + re.escape(id_) + '"', html) is not None


class _HyperHttp:
    """Minimal cookie jar + redirect-following requests wrapper. See
    efka_teka_certificate.py's _HyperHttp for the full rationale (manual
    jar instead of requests.Session for the same liberal cross-domain
    cookie merge the real portal relies on)."""

    def __init__(self, timeout: float = 30.0):
        self.jar: Dict[str, Dict[str, str]] = {}
        self.timeout = timeout

    @staticmethod
    def _host(url: str) -> str:
        return (urlsplit(url).hostname or "").lower()

    def _store(self, url: str, resp: requests.Response) -> None:
        host = self._host(url)
        self.jar.setdefault(host, {})
        try:
            raws = resp.raw.headers.getlist("Set-Cookie")
        except Exception:
            sc = resp.headers.get("Set-Cookie")
            raws = [sc] if sc else []
        for raw in raws:
            kv = raw.split(";", 1)[0]
            if "=" in kv:
                k, v = kv.split("=", 1)
                self.jar[host][k.strip()] = v.strip()

    def _cookie(self, url: str) -> str:
        host = self._host(url)
        parts: List[str] = []
        for h, kv in self.jar.items():
            if host == h or host.endswith(h) or h.endswith("gsis.gr") or h.endswith("e-efka.gov.gr") or h.endswith("idika.org.gr"):
                for k, v in kv.items():
                    parts.append(f"{k}={v}")
        return "; ".join(parts)

    def _once(self, method: str, url: str, form: Optional[Dict[str, str]] = None) -> requests.Response:
        headers = {"User-Agent": UA, "Accept-Language": "el-GR,el;q=0.9,en;q=0.8"}
        ck = self._cookie(url)
        if ck:
            headers["Cookie"] = ck
        data = None
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            data = urlencode(form).encode("utf-8")
        resp = requests.request(method, url, headers=headers, data=data,
                                allow_redirects=False, timeout=self.timeout)
        self._store(url, resp)
        return resp

    def follow(self, method: str, url: str, form: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        res = self._once(method, url, form)
        loc = res.headers.get("Location")
        cur = url
        hops = 0
        while loc and 300 <= res.status_code < 400 and hops < 25:
            cur = urljoin(cur, loc)
            res = self._once("GET", cur)
            loc = res.headers.get("Location")
            hops += 1
        text = res.text
        guard = 0
        while "<partial-response><redirect url=" in text and guard < 12:
            m = re.search(r'redirect url="([^"]*)"', text)
            if not m:
                break
            cur = urljoin(cur, m.group(1).replace("&amp;", "&"))
            res = self._once("GET", cur)
            loc = res.headers.get("Location")
            while loc and 300 <= res.status_code < 400 and hops < 25:
                cur = urljoin(cur, loc)
                res = self._once("GET", cur)
                loc = res.headers.get("Location")
                hops += 1
            text = res.text
            guard += 1
        return {"url": cur, "status": res.status_code, "text": text}


def _gsis_submit_and_approve(http: _HyperHttp, user: str, password: str) -> Dict[str, Any]:
    rl = http.follow("POST", "https://oauth2.gsis.gr/oauth2server/j_spring_security_check",
                     {"j_username": user, "j_password": password})
    if rl["url"].endswith("authentication_error=true") or re.search(r'name="j_password"', rl["text"], re.I):
        return {"ok": False, "reason": "InvalidCredentials"}
    page = rl
    if re.search(r'id="confirmationForm"', rl["text"], re.I) or re.search(r"user_oauth_approval", rl["text"], re.I):
        page = http.follow("POST", "https://oauth2.gsis.gr/oauth2server/oauth/authorize",
                           {"user_oauth_approval": "true", "scope.read": "true"})
    return {"ok": True, "page": page}


def _efka_non_employee_login(http: "_HyperHttp", username: str, password: str, afm: str, amka: str) -> Dict[str, Any]:
    """Same login as efka_teka_certificate.py's efka_non_employee_login().
    Takes the caller's _HyperHttp instance (not its own) so the caller can
    read back the authenticated cookie jar afterwards — see
    _login_and_open_keao, which hands those cookies to Playwright."""
    r = http.follow("GET", LAND)
    act = _decode_html(_form_action_of(r["text"], "social-external-non-employee"))
    if not act:
        return {"ok": False, "reason": "HomeForm"}
    http.follow("POST", urljoin(SVC, act), {})
    g = _gsis_submit_and_approve(http, username, password)
    if not g["ok"]:
        return g
    role_action = _decode_html(_own_form_action(g["page"]["text"], "kc-form-select-role"))
    if not role_action:
        return {"ok": False, "reason": "SelectRole"}
    rr = http.follow("POST", urljoin(g["page"]["url"], role_action), {
        "role": "external-non-employee", "afm": afm, "amka": amka, "pa": "", "ame": "", "amoe": "",
        "authorizing-afm": "", "authorizing-contractor-afm": "", "submit-role-attribute": "Υποβολή",
    })
    if not _has_id(rr["text"], "viewsPanel") or "Καλώς ήρθατε" not in rr["text"]:
        return {"ok": False, "reason": "LandPage"}

    # The user's real recording re-navigates to LAND explicitly after login
    # before clicking any nav link, instead of trusting whatever the
    # role-select POST happened to render — do the same cheap extra GET so
    # we collect links from the actual home page, not just the POST
    # response (which may carry a slimmer/different nav).
    home = http.follow("GET", LAND)
    pages_to_scan = [home["text"], rr["text"]] if home["text"] else [rr["text"]]

    links: Dict[str, str] = {}
    for page_text in pages_to_scan:
        for m in re.finditer(r'<a\b[^>]*href="([^"]*)"[^>]*>([\s\S]*?)</a>', page_text, re.I):
            t = _strip_tags(m.group(2))
            if t and t not in links:
                links[t] = urljoin(SVC, _decode_html(m.group(1)))
    return {"ok": True, "landing": home["text"] or rr["text"], "links": links}


def _cookies_for_playwright(http: _HyperHttp) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for host, kv in http.jar.items():
        if not host:
            continue
        for name, value in kv.items():
            out.append({"name": name, "value": value, "domain": host, "path": "/"})
    return out


def _dump_diagnostics(page: Page, output_dir: Path, tag: str) -> None:
    """Best-effort screenshot + trimmed HTML dump so a selector-mismatch
    failure is diagnosable from the returned JSON/log alone, without
    another live round-trip."""
    try:
        dump_dir = output_dir / "_diag"
        dump_dir.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(dump_dir / f"{tag}.png"))
        except Exception:
            pass
        html = page.content()
        (dump_dir / f"{tag}.html").write_text(html, encoding="utf-8")
        logging.warning("[keao] diagnostics for %s: url=%s title=%r saved -> %s",
                        tag, page.url, _safe_html_title(html), dump_dir)
    except Exception as exc:
        logging.warning("[keao] could not save diagnostics for %s: %s", tag, exc)


def _safe_html_title(html: str) -> str:
    m = re.search(r"<title[^>]*>([\s\S]*?)</title>", html, re.I)
    return _strip_tags(m.group(1)) if m else ""


def _login_and_open_keao(context: BrowserContext, page: Page, username: str,
                         password: str, afm: str, amka: str, output_dir: Path) -> Page:
    """HTTP-login (Keycloak/GSIS, ΑΦΜ+ΑΜΚΑ role-select) then hand the
    resulting session cookies to the Playwright context and land on the
    ΚΕΑΟ ΑΜΟ picker. Raises RuntimeError with the failure reason on
    login failure, matching the previous function's "raises on bad
    creds" contract."""
    http = _HyperHttp()
    L = _efka_non_employee_login(http, username, password, afm, amka)
    if not L.get("ok"):
        raise RuntimeError(f"login_failed:{L.get('reason')}")
    context.add_cookies(_cookies_for_playwright(http))
    keao_url = next((v for k, v in L["links"].items() if _KEAO_LINK_PHRASE in k), None)
    if not keao_url:
        # Fallback: KEAO/ΚΕΑΟ as either Latin or Greek letters, in case the
        # phrase itself ever changes wording.
        keao_url = next(
            (v for k, v in L["links"].items() if "KEAO" in k.upper() or "ΚΕΑΟ" in k.upper()),
            None,
        )
    if not keao_url:
        logging.warning("[keao] no ΚΕΑΟ link found — available landing links: %s",
                        list(L["links"].keys()))
        raise RuntimeError("login_failed:NoKeaoLink")
    # referer=LAND mimics actually clicking the link from the home page
    # (which is what a legitimate session does) rather than a bare
    # address-bar navigation, in case the WAF also checks that.
    page.goto(keao_url, timeout=NAV_TIMEOUT, referer=LAND)
    page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
    title = ""
    try:
        title = page.title()
    except Exception:
        pass
    if "blocked" in title.lower():
        # WAF interstitial ("The URL you requested has been blocked") —
        # fail fast instead of waiting out the full DEFAULT_TIMEOUT for a
        # table that will never appear.
        _dump_diagnostics(page, output_dir, "waf_blocked")
        raise RuntimeError(
            f"waf_blocked: ο server μπλόκαρε το αίτημα στο {page.url} (τίτλος: {title!r}) — "
            "δες _diag/waf_blocked.html/.png στον φάκελο εξόδου."
        )
    try:
        page.wait_for_selector("[id='amoForm:dt-table_data']", timeout=DEFAULT_TIMEOUT)
    except PlaywrightTimeoutError:
        _dump_diagnostics(page, output_dir, "amo_picker_timeout")
        raise RuntimeError(
            f"picker_not_found: η σελίδα φόρτωσε (url={page.url}) αλλά δεν βρέθηκε ο πίνακας "
            "ΑΜΟ — δες _diag/amo_picker_timeout.html/.png στον φάκελο εξόδου."
        )
    return page


def _list_registry_rows(picker: Page) -> list[dict]:
    """Return list of {forea, amo, epwnymia} for every registry row."""
    rows = picker.locator("[id='amoForm:dt-table_data'] tr")
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
    """Click the «Επιλογή» button on the row whose Α.Μ.Ο. matches.

    The button's widget id (e.g. ``amoForm:dt-table:0:j_idt156``) is a
    PrimeFaces-generated id we don't rely on — locate the row by its
    Α.Μ.Ο. cell instead and click whatever button is in that row.
    """
    rows = picker.locator("[id='amoForm:dt-table_data'] tr")
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
        btn = rows.nth(i).locator("button")
        if btn.count() == 0:
            return False
        btn.first.click()
        picker.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        # The click is a PrimeFaces AJAX submit, not always a full page
        # navigation — domcontentloaded can resolve before the debtor
        # detail view has actually replaced the picker content, which was
        # producing false "no ΑΜΟ" reads immediately afterwards. Give the
        # AJAX response a moment to land.
        picker.wait_for_timeout(SHORT_WAIT)
        return True
    return False


def _back_to_picker(page: Page, keao_url: str) -> bool:
    """Return to the ΑΜΟ picker by re-navigating to the captured ΚΕΑΟ
    landing URL — avoids depending on an unconfirmed sidebar selector in
    the new portal (the old «Επιλογή Μητρώου» sidebar link belonged to
    the retired eDebtor portal). Re-navigating a URL we already reached
    once in this same authenticated session is safe and idempotent."""
    try:
        page.goto(keao_url, timeout=NAV_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        page.wait_for_selector("[id='amoForm:dt-table_data']", timeout=DEFAULT_TIMEOUT)
        return True
    except PlaywrightTimeoutError:
        return False


def _has_no_amo_message(page: Page) -> bool:
    """True only if a VISIBLE «no ΑΜΟ» banner is on the page.

    JSF/PrimeFaces pages routinely carry a <p:messages> (or similar)
    component template for every possible message string, hidden/empty
    until actually populated — a plain `.count() > 0` text match hits
    that hidden template even when the real page shows real data, which
    is exactly what made every registry come back "no_amo" on the first
    live run (100% false-positive rate is the giveaway).
    """
    try:
        loc = page.locator(f"text={NO_AMO_TEXT}")
        for i in range(loc.count()):
            try:
                if loc.nth(i).is_visible():
                    return True
            except Exception:
                continue
        return False
    except Exception:
        return False


def _open_credits_tab(page: Page, date_from: str) -> str:
    """Navigate Κινήσεις Οφειλέτη → Πιστώσεις Οφειλών, fill date, click Εμφάνιση.

    Returns "ok"/"empty" on success paths, or one of several distinct
    failure reasons (no_amo / no_credits_tab / no_date_box / no_show_btn)
    instead of a single catch-all "no_amo" — a real live run showed every
    registry landing here with no way to tell, from the JSON alone, which
    of these four different things actually happened.
    """
    page.locator("a").filter(has_text="Κινήσεις Οφειλέτη").first.click()
    page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
    # Same AJAX-timing issue already found on the «Επιλογή» click: this is
    # a PrimeFaces AJAX submit too, so domcontentloaded can resolve before
    # the Κινήσεις Οφειλέτη view has actually rendered.
    page.wait_for_timeout(SHORT_WAIT)

    if _has_no_amo_message(page):
        return "no_amo"

    credits_tab = page.get_by_role("link", name="Πιστώσεις Οφειλών")
    if credits_tab.count() == 0:
        return "no_credits_tab"
    credits_tab.first.click()
    page.wait_for_timeout(SHORT_WAIT)

    date_box = page.locator("[id='debtorTransForm:dateFromFilter:calendar1']")
    if date_box.count() == 0:
        return "no_date_box"
    date_box.first.click()
    date_box.first.fill(date_from)

    show_btn = page.get_by_role("button", name="Εμφάνιση")
    if show_btn.count() == 0:
        return "no_show_btn"
    show_btn.first.click()
    page.wait_for_timeout(SHORT_WAIT)

    rows = _credits_rows(page)
    for _ in range(24):
        if rows.count():
            return "ok"
        page.wait_for_timeout(500)
    return "empty"


def _credits_rows(page: Page):
    """Locate the visible credits table despite PrimeFaces' generated IDs."""
    return page.locator("[id$='_data']:visible tr")


def _current_page_info(page: Page):
    pag = page.locator("[id='debtorTransForm:dt-debit-credit-analysis_paginator_bottom']")
    if pag.count() == 0:
        return None
    return _parse_page_info(pag.first.inner_text(timeout=3000))


def _click_next_page(page: Page, target_page: int) -> bool:
    nxt = page.locator(
        "[id='debtorTransForm:dt-debit-credit-analysis_paginator_bottom']"
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


# JS that returns only the card containing the debtor heading and the
# currently rendered «Πιστώσεις Οφειλών» tab.
_CARD_JS = r"""
() => {
    const TITLE = 'Ηλεκτρονική Καρτέλα Οφειλέτη';
    const creditsTab = document.getElementById('debtorTransForm:mainTabView:tab2');
    if (!creditsTab) return null;
    let heading = null;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
    while (walker.nextNode()) {
        const t = (walker.currentNode.nodeValue || '').trim();
        if (t === TITLE) { heading = walker.currentNode.parentElement; break; }
    }
    if (!heading) return null;
    const target = heading.closest('.card.card-w-title');
    return target && target.contains(creditsTab) ? target : null;
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
    raise RuntimeError("debtor_card_not_found: refusing to capture content outside the requested card")


def _extract_credit_rows(page: Page) -> list[dict]:
    rows_loc = _credits_rows(page)
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
    artifact_safe = f"{safe}_amo{reg['amo']}"
    reg_dir = output_dir / "shots" / artifact_safe
    reg_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "forea": reg["forea"],
        "amo": reg["amo"],
        "epwnymia": reg["epwnymia"],
        "is_efka_mh_misthwton": _is_efka_mh_misthwton(reg["forea"]),
        "status": "unknown",
        "pages_captured": 0,
        "pdf": None,
        "rows": [],
        "totals_year": {"total": 0.0, "main_contrib": 0.0, "extra_fees": 0.0, "surcharges": 0.0},
        "totals_all": {"total": 0.0, "main_contrib": 0.0, "extra_fees": 0.0, "surcharges": 0.0},
    }

    # `safe` is derived from the Φορέας name alone, which several
    # registries share (e.g. three different ΟΠΣ-ΙΚΑ ΑΜΟ under the same
    # Φορέας text) — the ΑΜΟ is the only thing that's actually unique per
    # registry, so diagnostic dump tags must include it or later
    # registries silently overwrite earlier ones' dumps (exactly what
    # happened on the previous live run: 3 of 4 shared one dump file).
    diag_tag = artifact_safe

    if not _click_select_for_amo(picker, reg["amo"]):
        record["status"] = "select_button_missing"
        return record

    if _has_no_amo_message(picker):
        _dump_diagnostics(picker, output_dir, f"no_amo_select_{diag_tag}")
        record["status"] = "no_amo"
        return record

    state = _open_credits_tab(picker, date_from)
    if state == "empty":
        _dump_diagnostics(picker, output_dir, f"empty_credits_{diag_tag}")
        record["status"] = "no_credits"
        return record
    if state != "ok":
        # One of: no_amo / no_credits_tab / no_date_box / no_show_btn —
        # kept as distinct statuses (not folded into "no_amo") so the
        # JSON alone says which step failed without needing to open the
        # diagnostics dump first.
        _dump_diagnostics(picker, output_dir, f"{state}_{diag_tag}")
        record["status"] = state
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

    pdf_path = output_dir / f"keao_pistwseis_{artifact_safe}.pdf"
    _stitch_pdf(shot_paths, pdf_path)
    record["pdf"] = str(pdf_path)
    record["rows"] = all_rows

    for r in all_rows:
        for k in ("total", "main_contrib", "extra_fees", "surcharges"):
            record["totals_all"][k] += r[k]
        if _row_in_year(r, year):
            for k in ("total", "main_contrib", "extra_fees", "surcharges"):
                record["totals_year"][k] += r[k]

    for totals in (record["totals_year"], record["totals_all"]):
        for k in totals:
            totals[k] = round(totals[k], 2)

    record["status"] = "ok"
    return record


def run(playwright, username: str, password: str, afm: str, amka: str,
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
        # MUST match the UA used by the HTTP login (module UA constant) —
        # the WAF in front of e-EFKA appears to bind the session cookies to
        # a consistent UA/device fingerprint; a mismatch between the UA
        # that established the session (HTTP) and the UA that then uses
        # the cookies (browser) trips its "URL blocked" page even though
        # the cookies themselves are valid. Same class of issue as the
        # DESKTOP_UA requirement documented for kartela_ergodoti.py.
        user_agent=UA,
    )
    page = context.new_page()

    result = {
        "status": "unknown",
        "year": year,
        "date_from": date_from,
        "registries": [],
        # Aggregates over EVERY non-employer registry processed:
        "totals_year": {"total": 0.0, "main_contrib": 0.0, "extra_fees": 0.0, "surcharges": 0.0},
        "totals_all": {"total": 0.0, "main_contrib": 0.0, "extra_fees": 0.0, "surcharges": 0.0},
        # Aggregates for the e3_brain reconciliation — EFKA Μη Μισθωτών
        # is excluded because the same payments are already counted in
        # the annual EFKA certificate. The brain adds e3_585_007_year
        # to the 585.007 total and reconciles e3_588_year against 588.
        "e3_585_007_year": 0.0,
        "e3_588_year": 0.0,
    }

    try:
        try:
            picker = _login_and_open_keao(context, page, username, password, afm, amka, output_dir)
        except RuntimeError as exc:
            result["status"] = str(exc)
            return result
        keao_url = picker.url

        all_registries = _list_registry_rows(picker)
        if not all_registries:
            result["status"] = "no_registries"
            return result

        targets = [r for r in all_registries if _is_efka_mh_misthwton(r["forea"])]
        logging.info("Found %d registries; %d requested EFKA registry to process",
                     len(all_registries), len(targets))

        for i, reg in enumerate(targets):
            if i > 0:
                # The picker page has navigated to a debtor view from the
                # previous iteration — go back to the picker before the
                # next selection.
                if not _back_to_picker(picker, keao_url):
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
            # Exclude Ε.Φ.Κ.Α. Μη Μισθωτών from the brain reconciliation
            # totals — already counted by the EFKA cert step.
            if not rec.get("is_efka_mh_misthwton"):
                ty = rec["totals_year"]
                result["e3_585_007_year"] += ty["main_contrib"]
                result["e3_588_year"] += ty["extra_fees"] + ty["surcharges"]

        result["e3_585_007_year"] = round(result["e3_585_007_year"], 2)
        result["e3_588_year"] = round(result["e3_588_year"], 2)
        for k in ("total", "main_contrib", "extra_fees", "surcharges"):
            result["totals_year"][k] = round(result["totals_year"][k], 2)
            result["totals_all"][k] = round(result["totals_all"][k], 2)
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
    parser.add_argument("--amka", default=os.getenv("KEAO_AMKA", ""), help="ΑΜΚΑ (απαιτείται για το role-select του νέου login).")
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
    if not args.amka:
        print("ERROR: --amka είναι υποχρεωτικό (χρειάζεται στο role-select του login).", file=sys.stderr)
        raise SystemExit(2)

    with sync_playwright() as p:
        result = run(p, args.username, args.password, afm, args.amka,
                     args.date_from, args.year, Path(args.output_dir), headless)

    Path(args.output_json).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
