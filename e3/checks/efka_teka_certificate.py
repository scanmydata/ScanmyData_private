"""Φορολογικές Βεβαιώσεις ΕΦΚΑ / ΤΕΚΑ (φορολογικής χρήσης) — PURE HTTP port.

Πιστή μετάφραση του αποδεδειγμένα δουλεμένου configs/efka-teka-certificate.js +
lib/hyper-http.js (repo scanmydata/ScanMyData-Tax-Center, αποκωδικοποιημένο από
Hyper.Server.Tax.dll — Easy_EFKA_SelfEmployed.GetDikaiomataAsfalisis). Χωρίς
Playwright/browser — καθαρό HTTP με requests, ίδιο ύφος με το myamka_http.py.

Login (efkaNonEmployeeLogin): services.e-efka.gov.gr, Keycloak-brokered.
  GET  ssp.commonservices.home/views/secure/index.xhtml
  βρες <form id="social-external-non-employee"> action -> POST (κενό σώμα) ->
    redirect στο GSIS OAuth2
  GSIS: POST j_spring_security_check {j_username, j_password} -> confirmationForm
    approval αν χρειαστεί (user_oauth_approval)
  ΒΗΜΑ ΡΟΛΟΥ (αυτό έλειπε από την πρώτη μου προσπάθεια): μετά το GSIS auth η
    σελίδα δείχνει <form id="kc-form-select-role"> — POST σε αυτήν με
    {role: 'external-non-employee', afm, amka, pa:'', ame:'', amoe:'',
     authorizing-afm:'', authorizing-contractor-afm:'', submit-role-attribute:'Υποβολή'}
  landing: πρέπει να έχει id="viewsPanel" + κείμενο «Καλώς ήρθατε». Όλοι οι
    σύνδεσμοι της landing συλλέγονται σε {κείμενο: href} (links) — π.χ.
    links['Δικαιώματα Ασφάλισης'].

Navigation/PDF (getCertificates): «Δικαιώματα Ασφάλισης» = insuranceRoyalties.xhtml
  (form-id:accordion-insurance-royalties-citizen, JSF/PrimeFaces accordion). Tab
  «Φορολογικές Βεβαιώσεις» / «Φορολογικές Βεβαιώσεις TEKA» αλλάζει με JSF
  partial-request POST (tabChange) -> partial-response <update> με πίνακα
  (έτος στο td[0], κουμπί «Εκτύπωση» στο td[8]/button). Το PDF κατεβαίνει με
  POST του ίδιου button id (== C# PostDataStream).

CLI:
    python efka_teka_certificate.py --username <taxisnet_user> --password <taxisnet_pass> \\
        --afm <afm> --amka <amka> [--year 2025] --pdf-dir <dir> --summary-out <path>

ΣΗΜΕΙΩΣΗ: η αναγνώριση του «συνολικού ποσού» μέσα στο PDF (label heuristic
Σύνολο/Εισφορές/Ποσό) παραμένει ανεπικύρωτη έναντι πραγματικού certificate — το
summary JSON εκθέτει όλους τους υποψήφιους αριθμούς + απόσπασμα κειμένου.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlencode, urlsplit

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)

FORM_ID = "form-id:accordion-insurance-royalties-citizen"
TAB_LABEL = {"EFKA": "Φορολογικές Βεβαιώσεις", "TEKA": "Φορολογικές Βεβαιώσεις TEKA"}
ROY_PATH_FALLBACK = "/ssp.efka.non.employees/views/insuranceRoyalties.xhtml"

_AMOUNT_RE = re.compile(r"(?<!\d)\d{1,3}(?:\.\d{3})*,\d{2}(?!\d)")
_TOTAL_KEYWORDS = ("συνολ", "εισφορ", "ποσο", "ποσό")


# ------------------------------------------------------------------ html helpers
def _decode_html(s: Optional[str]) -> str:
    s = s or ""
    s = s.replace("&nbsp;", " ").replace("&euro;", "€").replace("&#8364;", "€")
    s = s.replace("&amp;", "&").replace("&quot;", '"').replace("&#47;", "/")
    s = re.sub(r"&#0*47;", "/", s)
    s = s.replace("&lt;", "<").replace("&gt;", ">")
    return s


def _strip_tags(s: Optional[str]) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = _decode_html(s)
    return re.sub(r"\s+", " ", s).strip()


def _view_state(html: str, name: str = r"jakarta\.faces\.ViewState") -> str:
    m = re.search(r'name="' + name + r'"[^>]*value="([^"]*)"', html, re.I)
    if not m:
        m = re.search(r'value="([^"]*)"[^>]*name="' + name + r'"', html, re.I)
    return m.group(1) if m else ""


def _form_action_of(html: str, id_: str) -> str:
    """Href/action του <form> που ΠΕΡΙΕΧΕΙ ένα στοιχείο με το δοσμένο id."""
    e = re.escape(id_)
    m = re.search(r'<form\b[^>]*action="([^"]*)"[^>]*>(?:(?!</form>)[\s\S])*?id="' + e + '"', html, re.I)
    if m:
        return m.group(1)
    m = re.search(r'<form\b(?:(?!</form>)[\s\S])*?id="' + e + r'"(?:(?!</form>)[\s\S])*?action="([^"]*)"', html, re.I)
    return m.group(1) if m else ""


def _own_form_action(html: str, id_: str) -> str:
    """Action του ίδιου του <form id="..."> (όχι στοιχείου μέσα σε αυτό)."""
    e = re.escape(id_)
    m = re.search(r'<form[^>]*id="' + e + '"[^>]*action="([^"]*)"', html, re.I)
    if not m:
        m = re.search(r'<form[^>]*action="([^"]*)"[^>]*id="' + e + '"', html, re.I)
    return m.group(1) if m else ""


def _has_id(html: str, id_: str) -> bool:
    return re.search(r'id="' + re.escape(id_) + '"', html) is not None


def _find_tab_by_text(html: str, label: str) -> Optional[Dict[str, str]]:
    for li in re.finditer(r'<li\b[^>]*class="ui-tabs-header[^"]*"[^>]*>([\s\S]*?)</li>', html, re.I):
        if _strip_tags(li.group(1)) != label:
            continue
        dm = re.search(r'data-index="([^"]*)"', li.group(0), re.I)
        hm = re.search(r'<a\b[^>]*href="([^"]*)"', li.group(1), re.I)
        return {"dataIndex": dm.group(1) if dm else "", "href": _decode_html(hm.group(1) if hm else "")}
    return None


def _extract_update(xml: str, id_: str) -> str:
    e = re.escape(id_)
    m = re.search(r'<update id="' + e + r'"><!\[CDATA\[([\s\S]*?)\]\]></update>', xml, re.I)
    if m:
        return m.group(1)
    m = re.search(r'<update[^>]*><!\[CDATA\[([\s\S]*?)\]\]></update>', xml, re.I)
    return m.group(1) if m else xml


def _safe_name(token: str, fallback: str = "row") -> str:
    token = (token or "").strip()
    token = re.sub(r"[^\w\-Ͱ-Ͽἀ-῿.]+", "_", token, flags=re.U)
    return token or fallback


def _to_float_amount(s: str) -> float:
    try:
        return float(s.replace(".", "").replace(",", "."))
    except Exception:
        return 0.0


# ------------------------------------------------------------- HTTP engine
class _HyperHttp:
    """Cookie jar + follow() (HTTP 3xx ΚΑΙ JSF partial-response redirects) +
    post_for_pdf(). Χειροκίνητο cookie jar (ΟΧΙ requests.Session) γιατί
    χρειαζόμαστε το ΙΔΙΟ φιλελεύθερο cross-domain merge του πρωτότυπου
    (gsis.gr <-> e-efka.gov.gr <-> idika.org.gr μοιράζονται cookies) που το
    αυστηρό per-domain matching του http.cookiejar δεν θα έκανε."""

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
            raws = []
            sc = resp.headers.get("Set-Cookie")
            if sc:
                raws = [sc]
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
        headers = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "el-GR,el;q=0.9,en;q=0.8",
        }
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
        logging.info("  %s %s -> %s%s", method, url, res.status_code, f" -> {loc}" if loc else "")
        while loc and 300 <= res.status_code < 400 and hops < 25:
            cur = urljoin(cur, loc)
            res = self._once("GET", cur)
            loc = res.headers.get("Location")
            logging.info("  GET %s -> %s%s", cur, res.status_code, f" -> {loc}" if loc else "")
            hops += 1
        text = res.text
        guard = 0
        while "<partial-response><redirect url=" in text and guard < 12:
            m = re.search(r'redirect url="([^"]*)"', text)
            if not m:
                break
            cur = urljoin(cur, m.group(1).replace("&amp;", "&"))
            logging.info("  [partial-redirect] -> %s", cur)
            res = self._once("GET", cur)
            loc = res.headers.get("Location")
            while loc and 300 <= res.status_code < 400 and hops < 25:
                cur = urljoin(cur, loc)
                res = self._once("GET", cur)
                loc = res.headers.get("Location")
                logging.info("  GET %s -> %s", cur, res.status_code)
                hops += 1
            text = res.text
            guard += 1
        return {"url": cur, "status": res.status_code, "text": text}

    def post_for_pdf(self, url: str, form: Dict[str, str]) -> Optional[bytes]:
        res = self._once("POST", url, form)
        ct = (res.headers.get("content-type") or "").lower()
        logging.info("  [pdf-post] %s -> %s %s", url, res.status_code, ct)
        if res.status_code != 200:
            return None
        buf = res.content
        if "application/pdf" in ct or (len(buf) > 4 and buf[:4] == b"%PDF"):
            return buf
        return None


# --------------------------------------------------------------------- login
def _gsis_submit_and_approve(http: _HyperHttp, user: str, password: str) -> Dict[str, Any]:
    rl = http.follow("POST", "https://oauth2.gsis.gr/oauth2server/j_spring_security_check",
                     {"j_username": user, "j_password": password})
    if rl["url"].endswith("authentication_error=true") or re.search(r'id="j_password"', rl["text"], re.I) or re.search(r'name="j_password"', rl["text"], re.I):
        return {"ok": False, "reason": "InvalidCredentials"}
    page = rl
    if re.search(r'id="confirmationForm"', rl["text"], re.I) or re.search(r"user_oauth_approval", rl["text"], re.I):
        page = http.follow("POST", "https://oauth2.gsis.gr/oauth2server/oauth/authorize",
                           {"user_oauth_approval": "true", "scope.read": "true"})
    return {"ok": True, "page": page}


def efka_non_employee_login(http: _HyperHttp, user: str, password: str, afm: str, amka: str) -> Dict[str, Any]:
    """Πλήρες login «μη μισθωτού» στο services.e-efka.gov.gr (Keycloak).
    Επιστρέφει {ok, landing, links:{κείμενο: href}, SVC, url} ή {ok:False, reason}."""
    SVC = "https://services.e-efka.gov.gr/"
    LAND = SVC + "ssp.commonservices.home/views/secure/index.xhtml"
    logging.info("[efka-login] GET home")
    r = http.follow("GET", LAND)
    act = _decode_html(_form_action_of(r["text"], "social-external-non-employee"))
    if not act:
        return {"ok": False, "reason": "HomeForm"}
    logging.info("[efka-login] enter non-employee -> GSIS")
    http.follow("POST", urljoin(SVC, act), {})
    logging.info("[efka-login] TAXISnet credentials + approval")
    g = _gsis_submit_and_approve(http, user, password)
    if not g["ok"]:
        return g
    role_action = _decode_html(_own_form_action(g["page"]["text"], "kc-form-select-role"))
    if not role_action:
        return {"ok": False, "reason": "SelectRole"}
    logging.info("[efka-login] select role external-non-employee (afm+amka)")
    rr = http.follow("POST", urljoin(g["page"]["url"], role_action), {
        "role": "external-non-employee", "afm": afm, "amka": amka, "pa": "", "ame": "", "amoe": "",
        "authorizing-afm": "", "authorizing-contractor-afm": "", "submit-role-attribute": "Υποβολή",
    })
    if not _has_id(rr["text"], "viewsPanel") or "Καλώς ήρθατε" not in rr["text"]:
        return {"ok": False, "reason": "LandPage"}
    links: Dict[str, str] = {}
    for m in re.finditer(r'<a\b[^>]*href="([^"]*)"[^>]*>([\s\S]*?)</a>', rr["text"], re.I):
        t = _strip_tags(m.group(2))
        if t:
            links[t] = urljoin(SVC, _decode_html(m.group(1)))
    logging.info("[efka-login] OK (AMKA %s)", amka)
    return {"ok": True, "landing": rr["text"], "links": links, "SVC": SVC, "url": rr["url"]}


# ------------------------------------------------------------ PDF amount(s)
def extract_amount_from_pdf(pdf_bytes: bytes) -> Dict[str, Any]:
    """Εξαγωγή υποψήφιων χρηματικών ποσών από το PDF βεβαίωσης (label heuristic,
    ανεπικύρωτη έναντι πραγματικού certificate — εκθέτει όλους τους υποψήφιους)."""
    import pdfplumber
    all_text = ""
    candidates: List[Dict[str, Any]] = []
    keyword_candidates: List[Dict[str, Any]] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pg in pdf.pages:
            text = pg.extract_text() or ""
            all_text += text + "\n"
            for line in text.split("\n"):
                low = line.lower()
                for a in _AMOUNT_RE.findall(line):
                    val = round(_to_float_amount(a), 2)
                    if val <= 0:
                        continue
                    entry = {"value": val, "line": line.strip()}
                    candidates.append(entry)
                    if any(kw in low for kw in _TOTAL_KEYWORDS):
                        keyword_candidates.append(entry)
    best_guess = None
    if keyword_candidates:
        best_guess = keyword_candidates[-1]["value"]
    elif candidates:
        best_guess = max(c["value"] for c in candidates)
    return {
        "candidates": candidates,
        "keyword_candidates": keyword_candidates,
        "best_guess": best_guess,
        "text_excerpt": all_text[:2000],
    }


# --------------------------------------------------------- certificate retrieval
def _get_certificates(http: _HyperHttp, url: str, kind: str, afm: str,
                      year_filter: Optional[str], out_dir: Path) -> Dict[str, Any]:
    label = TAB_LABEL[kind]
    result: Dict[str, Any] = {"ok": False, "years_available": [], "year": None,
                              "pdf": None, "amount": None, "error": None}
    logging.info("[cert:%s] GET Δικαιώματα Ασφάλισης", kind)
    page = http.follow("GET", url)["text"]
    if not _has_id(page, FORM_ID):
        result["error"] = "Η σελίδα δεν περιέχει το accordion Δικαιωμάτων Ασφάλισης."
        return result
    vs = _view_state(page)
    tab = _find_tab_by_text(page, label)
    if not vs or not tab or not tab.get("href"):
        result["error"] = f'Δεν βρέθηκε ViewState/tab «{label}».'
        return result
    new_tab = tab["href"].lstrip("#")
    if not new_tab.startswith(FORM_ID + ":"):
        result["error"] = "Μη αναμενόμενο tab href."
        return result

    logging.info("[cert:%s] tabChange (dataIndex=%s)", kind, tab["dataIndex"])
    tab_resp = http.follow("POST", url, {
        "jakarta.faces.partial.ajax": "true", "jakarta.faces.source": FORM_ID,
        "jakarta.faces.partial.execute": FORM_ID, "jakarta.faces.partial.render": FORM_ID,
        "jakarta.faces.behavior.event": "tabChange", "jakarta.faces.partial.event": "tabChange",
        f"{FORM_ID}_contentLoad": "true", f"{FORM_ID}_newTab": new_tab, f"{FORM_ID}_tabindex": tab["dataIndex"],
        "form-id": "form-id",
        f"{FORM_ID}:application-type-selector_input": "registration",
        f"{FORM_ID}:reg-apps-reg_activeIndex": "1",
        f"{FORM_ID}_activeIndex": tab["dataIndex"],
        "jakarta.faces.ViewState": vs,
    })
    upd = _extract_update(tab_resp["text"], FORM_ID)

    raw_rows: List[List[str]] = []
    for m in re.finditer(r'<tr\b[^>]*class="[^"]*ui-datatable-(?:even|odd)[^"]*"[^>]*>([\s\S]*?)</tr>', upd, re.I):
        tds = [t.group(1) for t in re.finditer(r"<td\b[^>]*>([\s\S]*?)</td>", m.group(1), re.I)]
        raw_rows.append(tds)
    if not raw_rows:
        result["ok"] = True
        result["error"] = "Δεν βρέθηκαν γραμμές (έτη) — πιθανόν καμία βεβαίωση διαθέσιμη."
        return result

    def cell_text(c: str) -> str:
        if re.search(r"<button|PrimeFaces\.cw|<script", c, re.I):
            return ""
        return _strip_tags(c)

    table: List[Dict[str, str]] = []
    for tds in raw_rows:
        cells = [cell_text(c) for c in tds]
        row: Dict[str, str] = {"year": cells[0] if cells else ""}
        btn_id = ""
        if len(tds) > 7:
            bm = re.search(r'<button\b[^>]*id="([^"]*)"', tds[7], re.I)
            if bm:
                btn_id = bm.group(1)
        if not btn_id:
            for c in tds:
                bm = re.search(r'<button\b[^>]*id="([^"]*)"', c, re.I)
                if bm:
                    btn_id = bm.group(1)
                    break
        row["_btn_id"] = btn_id
        table.append(row)

    result["years_available"] = [r["year"] for r in table]

    target = None
    if year_filter:
        target = next((r for r in table if r["year"] == year_filter), None)
        if not target:
            result["error"] = f"Το έτος {year_filter} δεν βρέθηκε — διαθέσιμα: {', '.join(result['years_available'])}"
            return result
    else:
        target = table[-1]

    if not target.get("_btn_id"):
        result["error"] = f"Δεν βρέθηκε κουμπί «Εκτύπωση» για το έτος {target['year']}."
        return result

    logging.info("[cert:%s %s] print button id=%s", kind, target["year"], target["_btn_id"])
    pdf_bytes = http.post_for_pdf(url, {
        "form-id": "form-id",
        f"{FORM_ID}:application-type-selector_input": "registration",
        target["_btn_id"]: "",
        f"{FORM_ID}:reg-apps-reg_activeIndex": "1",
        f"{FORM_ID}_activeIndex": tab["dataIndex"],
        "jakarta.faces.ViewState": vs,
    })
    if not pdf_bytes:
        result["error"] = f"{label}: το server δεν επέστρεψε PDF."
        return result

    afm_token = _safe_name(afm or "unknown")
    year_token = _safe_name(target["year"])
    dest = out_dir / f"efka_teka_cert_{kind.lower()}_{afm_token}_{year_token}.pdf"
    try:
        if dest.exists():
            dest.unlink()
    except Exception:
        pass
    dest.write_bytes(pdf_bytes)

    result["pdf"] = str(dest)
    result["year"] = target["year"]
    try:
        result["amount"] = extract_amount_from_pdf(pdf_bytes)
    except Exception as exc:
        result["amount"] = {"error": f"Αποτυχία εξαγωγής ποσού: {exc}"}
    result["ok"] = True
    return result


def run_extractor(username: str, password: str, afm: str, amka: str,
                  year: Optional[str], pdf_dir: Path) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "ok": False, "afm": afm, "year": year, "pdf_dir": str(pdf_dir),
        "efka": {"ok": False, "error": "not run"},
        "teka": {"ok": False, "error": "not run"},
        "error": None,
    }
    http = _HyperHttp()
    try:
        L = efka_non_employee_login(http, username, password, afm, amka)
        if not L.get("ok"):
            summary["error"] = f"Αποτυχία σύνδεσης: {L.get('reason')}"
            return summary

        url = L["links"].get("Δικαιώματα Ασφάλισης")
        if not url:
            m = re.search(r'href="([^"]*insuranceRoyalties\.xhtml[^"]*)"', L.get("landing") or "", re.I)
            url = urljoin(L["SVC"], _decode_html(m.group(1))) if m else urljoin(L["SVC"], ROY_PATH_FALLBACK)
        logging.info("[cert] royalties URL = %s", url)

        try:
            summary["efka"] = _get_certificates(http, url, "EFKA", afm, year, pdf_dir)
        except requests.RequestException as exc:
            logging.exception("EFKA certificate request failed: %s", exc)
            summary["efka"] = {"ok": False, "years_available": [], "year": None,
                                "pdf": None, "amount": None, "error": f"EFKA request: {exc}"}
        except Exception as exc:
            logging.exception("EFKA certificate stage failed: %s", exc)
            summary["efka"] = {"ok": False, "years_available": [], "year": None,
                                "pdf": None, "amount": None, "error": f"EFKA stage: {exc}"}

        try:
            summary["teka"] = _get_certificates(http, url, "TEKA", afm, year, pdf_dir)
        except requests.RequestException as exc:
            logging.exception("TEKA certificate request failed: %s", exc)
            summary["teka"] = {"ok": False, "years_available": [], "year": None,
                                "pdf": None, "amount": None, "error": f"TEKA request: {exc}"}
        except Exception as exc:
            logging.exception("TEKA certificate stage failed: %s", exc)
            summary["teka"] = {"ok": False, "years_available": [], "year": None,
                                "pdf": None, "amount": None, "error": f"TEKA stage: {exc}"}
        summary["ok"] = bool(summary["efka"].get("ok") and summary["efka"].get("pdf")) or bool(summary["teka"].get("ok") and summary["teka"].get("pdf"))
    except Exception as exc:
        logging.exception("efka_teka_certificate run failed: %s", exc)
        summary["error"] = str(exc)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Φορολογικές Βεβαιώσεις ΕΦΚΑ/ΤΕΚΑ (φορολογικής χρήσης) — PDF download + αναγνώριση ποσού."
    )
    parser.add_argument("--username", default=os.getenv("EFKA_CERT_USER"), help="Όνομα χρήστη TAXISnet.")
    parser.add_argument("--password", default=os.getenv("EFKA_CERT_PASS"), help="Κωδικός TAXISnet.")
    parser.add_argument("--afm", default=os.getenv("EFKA_CERT_AFM", ""), help="ΑΦΜ (απαιτείται για το role-select).")
    parser.add_argument("--amka", default=os.getenv("EFKA_CERT_AMKA", ""), help="ΑΜΚΑ (απαιτείται για το role-select).")
    parser.add_argument("--year", default=os.getenv("EFKA_CERT_YEAR", ""), help="Έτος (κενό = το πιο πρόσφατο διαθέσιμο).")
    parser.add_argument("--pdf-dir", default=os.getenv("EFKA_CERT_PDF_DIR"), help="Φάκελος εξόδου. Default: ./efka_teka_cert_pdfs/")
    parser.add_argument("--summary-out", default=os.getenv("EFKA_CERT_SUMMARY_OUT"), help="Αν δοθεί, γράφει JSON summary σε αυτό το αρχείο.")
    # Καθαρό HTTP — χωρίς browser. Δεχόμαστε/αγνοούμε --headless/--headed για
    # συμβατότητα με callers (π.χ. e3_brain.py) που τα περνάνε πάντα.
    parser.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--headed", dest="headless", action="store_false", help=argparse.SUPPRESS)
    parser.set_defaults(headless=True)
    args = parser.parse_args()

    if not args.username or not args.password:
        print("ERROR: --username και --password είναι υποχρεωτικά.", file=sys.stderr)
        return 2
    if not args.afm or not args.amka:
        print("ERROR: --afm και --amka είναι υποχρεωτικά (χρειάζονται στο role-select του login).", file=sys.stderr)
        return 2

    pdf_dir = Path(args.pdf_dir).resolve() if args.pdf_dir else (Path(__file__).resolve().parent / "efka_teka_cert_pdfs")
    pdf_dir.mkdir(parents=True, exist_ok=True)

    summary = run_extractor(
        username=args.username, password=args.password, afm=args.afm, amka=args.amka,
        year=(args.year.strip() or None), pdf_dir=pdf_dir,
    )

    summary_target = Path(args.summary_out) if args.summary_out else (pdf_dir / "efka_teka_certificate_summary.json")
    try:
        summary_target.parent.mkdir(parents=True, exist_ok=True)
        summary_target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        logging.info("Summary → %s", summary_target)
    except Exception as exc:
        logging.warning("Could not write summary file (%s): %s", summary_target, exc)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
