"""Οικονομική Καρτέλα Εργοδότη e-ΕΦΚΑ (ΝΕΟ ΟΠΣ) + ανάκτηση ΑΜΕ.

* Καρτέλα: ``services.e-efka.gov.gr/ssp.efka.emp-transactions-ext`` — login TAXISnet + ΑΜΕ
  (KeyCloak, ρόλος ``external-employer``), καθαρό HTTP (port του live-verified
  runner/configs/efka-employer-card.js): Εμφάνιση με εύρος ημερομηνιών → όλες οι
  σελίδες του ``linesTable`` → «Εκτύπωση Κινήσεων» → PDF.
* ΑΜΕ: «Πιστοποίηση Εργοδοτών» (ePistErg → «Κοινών Επιχειρήσεων» → AMEs.xhtml) με
  browser (Playwright), port του runner/configs/efka-ame.js. Τρέχει μόνο όταν δεν
  υπάρχει ήδη ΑΜΕ.

Το παλιό app ``apps.e-efka.gov.gr/eEmployerTransactions`` (κωδικοί ΙΚΑ) έχει αποσυρθεί.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

try:
    from e3.checks import efka_teka_certificate as cert
except Exception:  # pragma: no cover — standalone
    import efka_teka_certificate as cert  # type: ignore

APP = "https://services.e-efka.gov.gr/ssp.efka.emp-transactions-ext/"
PAGE = APP + "secure/transactions.xhtml"
EFKA_APPS = "https://apps.e-efka.gov.gr"
AJAX_HEADERS = {"Faces-Request": "partial/ajax", "X-Requested-With": "XMLHttpRequest"}

# Κείμενα που σημαίνουν ότι ο e-ΕΦΚΑ ζητά ενημέρωση στοιχείων (eProfile) πριν δώσει την υπηρεσία.
_UPDATE_RE = re.compile(r"ενημ[εέ]ρωσ[ηη]\s+(?:των\s+)?στοιχε[ιί]ων|επικαιροπο[ιί]ησ|eProfile|"
                        r"στοιχε[ιί]α\s+επικοινων[ιί]ας", re.I)


class _Http(cert._HyperHttp):
    def __init__(self, timeout: float = 60.0):
        super().__init__(timeout)
        self._extra: Optional[Dict[str, str]] = None

    def _once(self, method, url, form=None):  # type: ignore[override]
        import requests
        from urllib.parse import urlencode
        headers = {"User-Agent": cert.UA,
                   "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                   "Accept-Language": "el-GR,el;q=0.9,en;q=0.8"}
        if self._extra:
            headers.update(self._extra)
        ck = self._cookie(url)
        if ck:
            headers["Cookie"] = ck
        data = None
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            data = urlencode(form).encode("utf-8")
        resp = requests.request(method, url, headers=headers, data=data, allow_redirects=False, timeout=self.timeout)
        self._store(url, resp)
        return resp

    def ajax(self, url: str, form: Dict[str, str]) -> str:
        self._extra = dict(AJAX_HEADERS)
        try:
            return self._once("POST", url, form).text
        finally:
            self._extra = None


def _vs(s: str) -> str:
    m = re.search(r"ViewState[^>]*><!\[CDATA\[([^\]]+)\]\]", s, re.I)
    return m.group(1) if m else cert._view_state(s)


def form_fields(html: str, form_id: str) -> Dict[str, str]:
    """name→value όλων των απλών inputs/selects μέσα σε <form id=...>."""
    m = re.search(r'<form[^>]*id="' + re.escape(form_id) + '"', html, re.I)
    if not m:
        return {}
    end = html.find("</form>", m.start())
    block = html[m.start(): end + 7 if end >= 0 else len(html)]
    f: Dict[str, str] = {}
    for im in re.finditer(r"<input\b[^>]*>", block, re.I):
        tag = im.group(0)
        nm = re.search(r'name="([^"]*)"', tag, re.I)
        if not nm:
            continue
        typ = (re.search(r'type="([^"]*)"', tag, re.I) or [None, "text"])[1].lower()
        if typ in ("submit", "button"):
            continue
        if typ in ("checkbox", "radio") and not re.search(r"\bchecked\b", tag, re.I):
            continue
        val = re.search(r'value="([^"]*)"', tag, re.I)
        f[nm.group(1)] = cert._decode_html(val.group(1) if val else "")
    for sm in re.finditer(r'<select\b[^>]*name="([^"]*)"[^>]*>([\s\S]*?)</select>', block, re.I):
        sel = re.search(r'<option[^>]*value="([^"]*)"[^>]*selected', sm.group(2), re.I) or \
            re.search(r'<option[^>]*value="([^"]*)"', sm.group(2), re.I)
        f[sm.group(1)] = cert._decode_html(sel.group(1) if sel else "")
    return f


def parse_rows(xml: str) -> List[Dict[str, str]]:
    body = cert._extract_update(xml, "transactionsForm") if "<partial-response" in xml else xml
    out = []
    for tr in re.findall(r'<tr\b[^>]*data-ri="\d+"[\s\S]*?</tr>', body, re.I):
        c = [cert._strip_tags(x) for x in re.findall(r"<td\b[^>]*>([\s\S]*?)</td>", tr, re.I)]
        if not c:
            continue
        c += [""] * (7 - len(c))
        out.append({"date": c[0], "kind": c[1], "docNo": c[2], "period": c[3],
                    "charge": c[4], "credit": c[5], "balance": c[6]})
    return out


def _button_id(html: str, text_re: str, fallback: str) -> str:
    """id κουμπιού PrimeFaces από το κείμενο/τίτλο του (τα j_idtNNN αλλάζουν με τις εκδόσεις)."""
    for m in re.finditer(r"<button\b([^>]*)>([\s\S]*?)</button>", html, re.I):
        attrs, inner = m.group(1), cert._strip_tags(m.group(2))
        title = (re.search(r'title="([^"]*)"', attrs) or [None, ""])[1]
        if re.search(text_re, inner + " " + title, re.I):
            idm = re.search(r'\bid="([^"]+)"', attrs)
            if idm:
                return idm.group(1)
    return fallback


def _print_submit_name(html: str) -> str:
    m = re.search(r'<form[^>]*id="PrintTransactionsForm"[\s\S]*?</form>', html, re.I)
    if m:
        b = re.search(r'<button\b[^>]*name="([^"]+)"[^>]*type="submit"', m.group(0), re.I) or \
            re.search(r'<button\b[^>]*type="submit"[^>]*name="([^"]+)"', m.group(0), re.I)
        if b:
            return b.group(1)
    return "PrintTransactionsForm:j_idt209"


def fetch_card(user: str, password: str, afm: str, ame: str, date_from: str, date_to: str,
               out_dir: Path, year: Optional[str] = None, diag_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Καρτέλα ΕΦΚΑ εργοδότη (καθαρό HTTP). Επιστρέφει {ok, pdf, error, count, rows, reason}."""
    res: Dict[str, Any] = {"ok": False, "pdf": None, "error": None, "count": 0, "rows": [], "source": "http"}

    def dump(name: str, text: str) -> None:
        if diag_dir:
            try:
                diag_dir.mkdir(parents=True, exist_ok=True)
                (diag_dir / name).write_text(text or "", encoding="utf-8")
            except Exception:
                pass

    if not (user and password and afm and ame):
        res.update(error="Χρειάζονται κωδικοί TAXISnet + ΑΦΜ + ΑΜΕ", reason="InvalidInput")
        return res
    http = _Http()
    r = http.follow("GET", APP)
    bm = re.search(r'id="social-taxisnet-employer"[^>]*href="([^"]*)"', r["text"], re.I) or \
        re.search(r'href="([^"]*)"[^>]*id="social-taxisnet-employer"', r["text"], re.I)
    if not bm:
        dump("emp_01_app.html", r["text"])
        res.update(error="δεν βρέθηκε η «Είσοδος Εργοδότη» (TAXISnet)", reason="NoEmployerBroker")
        return res
    http.follow("GET", urljoin(r["url"], cert._decode_html(bm.group(1))))
    g = cert._gsis_submit_and_approve(http, user, password)
    if not g.get("ok"):
        res.update(error="λάθος κωδικοί TAXISnet", reason=g.get("reason") or "InvalidCredentials")
        return res
    role_action = cert._decode_html(cert._own_form_action(g["page"]["text"], "kc-form-select-role"))
    if not role_action:
        dump("emp_02_role.html", g["page"]["text"])
        res.update(error="δεν εμφανίστηκε η επιλογή ρόλου εργοδότη", reason="NoRoleForm")
        return res
    land = http.follow("POST", urljoin(g["page"]["url"], role_action), {
        "role": "external-employer", "afm": afm, "amka": "", "pa": "", "ame": ame, "amoe": "",
        "authorizing-afm": "", "authorizing-contractor-afm": "", "submit-role-attribute": "Υποβολή"})
    dump("emp_03_landing.html", land["text"])
    if "Οικονομική Καρτέλα Εργοδότη" not in land["text"]:
        txt = cert._strip_tags(land["text"])[:300]
        if _UPDATE_RE.search(land["text"]):
            res.update(error="ο e-ΕΦΚΑ ζητά ενημέρωση στοιχείων επικοινωνίας πριν την πρόσβαση", reason="UpdateRequired")
        elif re.search(r"ΑΜΕ|ame", txt) and re.search(r"λάθος|δεν|άκυρ|invalid", txt, re.I):
            res.update(error=f"ο ΑΜΕ {ame} δεν αντιστοιχεί στον ΑΦΜ {afm}: {txt}", reason="BadAme")
        else:
            res.update(error=f"η σύνδεση δεν οδήγησε στην καρτέλα: {txt}", reason="LoginLanding")
        return res
    vs = _vs(land["text"])
    base = form_fields(land["text"], "transactionsForm")
    search_id = _button_id(land["text"], r"Εμφάνιση", "j_idt135")
    print_id = _button_id(land["text"], r"Εκτύπωση Κινήσεων", "j_idt137")
    body = dict(base, **{
        "jakarta.faces.partial.ajax": "true", "jakarta.faces.source": search_id,
        "jakarta.faces.partial.execute": "transactionsForm", "jakarta.faces.partial.render": "transactionsForm",
        search_id: search_id, "dateFrom_input": date_from, "dateTo_input": date_to, "jakarta.faces.ViewState": vs})
    xml = http.ajax(PAGE, body)
    dump("emp_10_search.xml", xml)
    vs = _vs(xml) or vs
    rows = parse_rows(xml)
    pm = re.search(r"Σελίδα\s*\d+\s*από\s*(\d+)", xml, re.I)
    pages = int(pm.group(1)) if pm else 1
    for p in range(1, min(pages, 300)):
        px = http.ajax(PAGE, dict(base, **{
            "jakarta.faces.partial.ajax": "true", "jakarta.faces.source": "linesTable",
            "jakarta.faces.partial.execute": "linesTable", "jakarta.faces.partial.render": "linesTable",
            "jakarta.faces.behavior.event": "page", "jakarta.faces.partial.event": "page",
            "linesTable_pagination": "true", "linesTable_first": str(p * 10), "linesTable_rows": "10",
            "linesTable_encodeFeature": "true", "transactionsForm": "transactionsForm",
            "dateFrom_input": date_from, "dateTo_input": date_to, "jakarta.faces.ViewState": vs}))
        vs = _vs(px) or vs
        rows.extend(parse_rows(px))
    res["rows"], res["count"] = rows, len(rows)
    prep = http.ajax(PAGE, dict(base, **{
        "jakarta.faces.partial.ajax": "true", "jakarta.faces.source": print_id,
        "jakarta.faces.partial.execute": "transactionsForm", print_id: print_id,
        "dateFrom_input": date_from, "dateTo_input": date_to, "jakarta.faces.ViewState": vs}))
    dump("emp_20_print_prep.xml", prep)
    vs = _vs(prep) or vs
    pdf = http.post_for_pdf(PAGE, {"PrintTransactionsForm": "PrintTransactionsForm",
                                   _print_submit_name(prep + land["text"]): "", "jakarta.faces.ViewState": vs})
    if not pdf:
        res.update(error=f"δεν επιστράφηκε PDF ({len(rows)} κινήσεις διαβάστηκαν)", reason="NoPdf")
        return res
    out_dir.mkdir(parents=True, exist_ok=True)
    yr = year or (re.search(r"(20\d{2})", date_from) or [None, "unknown"])[1]
    target = out_dir / f"kartela_ergodoti_efka_{cert._safe_name(afm)}_{yr}.pdf"
    target.write_bytes(pdf)
    res.update(ok=True, pdf=str(target), exists=True, size=len(pdf))
    logging.info("[emp-card] PDF %s (%d κινήσεις)", target, len(rows))
    return res


# ------------------------------------------------------------------ ΑΜΕ (browser)
def retrieve_ame(user: str, password: str, afm: str, headless: bool = True,
                 kind: str = "koinon", diag_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Ανάκτηση ΑΜΕ από «Πιστοποίηση Εργοδοτών» (ePistErg). Επιστρέφει {ok, ame, ames, reason}."""
    from playwright.sync_api import sync_playwright
    try:
        from e3.checks import chromium_launch_args
    except Exception:
        def chromium_launch_args(extra=None):
            return ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                    "--disable-blink-features=AutomationControlled"]
    link_text = "Οικοδομοτεχνικών Έργων" if kind.startswith("oikodom") else "Κοινών Επιχειρήσεων"

    def dump(page, name):
        if diag_dir:
            try:
                diag_dir.mkdir(parents=True, exist_ok=True)
                (diag_dir / name).write_text(page.content(), encoding="utf-8")
            except Exception:
                pass

    with sync_playwright() as p:
        b = p.chromium.launch(headless=headless, args=chromium_launch_args())
        try:
            ctx = b.new_context(user_agent=cert.UA, locale="el-GR")
            page = ctx.new_page()
            page.set_default_timeout(45000)
            page.goto(EFKA_APPS + "/eAccess/login.xhtml")
            page.wait_for_timeout(1500)
            try:
                page.get_by_text("Συνέχεια στο TAXISNET").first.click()
            except Exception:
                pass
            page.wait_for_timeout(2500)
            if page.locator('input[name="j_username"]').count():
                page.fill('input[name="j_username"]', user)
                page.fill('input[name="j_password"]', password)
                page.locator("#btn-login-submit").first.click()
                page.wait_for_timeout(3500)
            if page.locator('input[name="user_oauth_approval"]').count():
                page.locator("#btn-submit").first.click()
                page.wait_for_timeout(3000)
            dump(page, "ame_01_after_gsis.html")
            if page.locator('input[name="j_password"]').count():
                return {"ok": False, "reason": "InvalidCredentials", "error": "λάθος κωδικοί TAXISnet"}
            try:
                page.get_by_text("Επιχείρηση/Πολίτης").first.click()
                page.wait_for_timeout(800)
            except Exception:
                pass
            afm1 = page.evaluate("() => { const el = document.getElementById('gsisTabView:afm1'); return el ? (el.value || '') : ''; }")
            if afm1 and afm1 != afm:
                return {"ok": False, "reason": "AfmMismatch",
                        "error": f"οι κωδικοί TAXISnet ανήκουν σε άλλο ΑΦΜ ({afm1})"}
            try:
                page.get_by_role("button", name="Είσοδος").first.click()
            except Exception:
                try:
                    page.get_by_text("Είσοδος", exact=True).first.click()
                except Exception:
                    pass
            page.wait_for_timeout(4000)
            try:
                page.goto(EFKA_APPS + "/ePistErg")
            except Exception:
                pass
            page.wait_for_timeout(3500)
            for _ in range(2):
                if "eprofile" not in (page.url or "").lower():
                    break
                try:
                    page.get_by_role("button", name="Παράλειψη").first.click()
                    page.wait_for_timeout(4000)
                except Exception:
                    break
            dump(page, "ame_02_episterg.html")
            if "eprofile" in (page.url or "").lower():
                return {"ok": False, "reason": "UpdateRequired",
                        "error": "ο e-ΕΦΚΑ ζητά υποχρεωτική ενημέρωση στοιχείων επικοινωνίας (eProfile)"}
            if "episterg" not in (page.url or "").lower():
                return {"ok": False, "reason": "NoPistErg", "error": f"δεν άνοιξε η Πιστοποίηση Εργοδοτών ({page.url})"}
            try:
                page.get_by_text(link_text).first.click()
            except Exception:
                return {"ok": False, "reason": "NoKoinonLink", "error": f"δεν βρέθηκε «{link_text}»"}
            page.wait_for_timeout(4000)
            dump(page, "ame_03_ames.html")
            rows = page.evaluate("""() => {
                const t = document.getElementById('AMEsForm:AMEsResults_data'); if (!t) return null;
                const out = [];
                for (const tr of t.querySelectorAll('tr')) { const ri = tr.getAttribute('data-ri');
                  if (ri != null && +ri >= 0) out.push([...tr.querySelectorAll('td')].map(td => td.innerText.trim())); }
                return out; }""")
            if rows is None:
                return {"ok": False, "reason": "NoAmeTable", "error": "δεν βρέθηκε ο πίνακας ΑΜΕ"}
            ames = [{"ame": r[0], "name": r[1] if len(r) > 1 else ""} for r in rows if r and re.fullmatch(r"\d+", r[0] or "")]
            if not ames:
                return {"ok": False, "reason": "AmeNotFound", "error": "ο ΑΦΜ δεν έχει ΑΜΕ (δεν είναι εργοδότης)"}
            return {"ok": True, "ame": ames[0]["ame"], "ames": ames}
        except Exception as exc:
            logging.exception("retrieve_ame")
            return {"ok": False, "reason": "BrowserError", "error": str(exc)}
        finally:
            b.close()


if __name__ == "__main__":  # pragma: no cover — χειροκίνητη δοκιμή
    import argparse
    import getpass
    ap = argparse.ArgumentParser(description="Καρτέλα ΕΦΚΑ εργοδότη (TAXISnet + ΑΜΕ) / ανάκτηση ΑΜΕ")
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", default="")
    ap.add_argument("--afm", required=True)
    ap.add_argument("--ame", default="")
    ap.add_argument("--year", default="2025")
    ap.add_argument("--out-dir", default="emp_card_out")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    pw = a.password or getpass.getpass("Κωδικός TAXISnet: ")
    ame = a.ame
    if not ame:
        r = retrieve_ame(a.username, pw, a.afm, headless=not a.headed, diag_dir=Path(a.out_dir) / "_diag")
        print(json.dumps(r, ensure_ascii=False, indent=2))
        ame = r.get("ame") or ""
    if ame:
        r = fetch_card(a.username, pw, a.afm, ame, f"01/01/{a.year}", f"31/12/{a.year}", Path(a.out_dir),
                       year=a.year, diag_dir=Path(a.out_dir) / "_diag")
        r.pop("rows", None)
        print(json.dumps(r, ensure_ascii=False, indent=2))
