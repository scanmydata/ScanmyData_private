"""Ολοκληρωμένος έλεγχος ΕΦΚΑ/ΤΕΚΑ μη μισθωτού — αντιπαραβολή φορολογικής
βεβαίωσης έτους με τις καταβολές e-ΕΦΚΑ και τις πιστώσεις ΚΕΑΟ.

Ένα login (Keycloak «μη μισθωτού», ίδιο με efka_teka_certificate.py) και μετά,
στην ΙΔΙΑ συνεδρία, καθαρό HTTP:

  1. «Δικαιώματα Ασφάλισης» → Φορολογικές Βεβαιώσεις e-ΕΦΚΑ / ΤΕΚΑ (ποσό από
     τον πίνακα της σελίδας πρώτα, από το PDF ως fallback).
  2. «Υποχρεώσεις Ασφάλισης» → tab «Καταβολές» (ίδιο σημείο που ανοίγει το
     EpsilonDocs.Katavoles: ClickOptionButton «Καταβολές»). Η δομή του πίνακα
     δεν έχει επιβεβαιωθεί live, γι' αυτό ο parser είναι γενικός: διαβάζει
     επικεφαλίδες και αναγνωρίζει τις στήλες (ημερομηνία, περίοδος, καθαρό,
     προσαυξήσεις/τέλη, σύνολο). Όλα τα ακατέργαστα HTML σώζονται στο _diag/.
  3. ΚΕΑΟ → για κάθε μητρώο «Κινήσεις Οφειλέτη» → «Πιστώσεις» (κάθε πίστωση
     χωρίζεται σε Κύρια Εισφορά / Πρόσθετα Τέλη / Προσαυξήσεις). Αν το HTTP
     αποτύχει, fallback στο keao-mistoton.py (Playwright).

Αντιπαραβολή (reconcile): η βεβαίωση περιέχει μόνο καθαρά ποσά. Δοκιμάζονται
οι συνδυασμοί καθαρών ποσών (καταβολές + ΚΕΑΟ, μόνο καταβολές, μόνο ΚΕΑΟ) και
κρατιέται αυτός που συμφωνεί με τη βεβαίωση. Οι επιβαρύνσεις (πρόσθετα τέλη,
προσαυξήσεις) αθροίζονται χωριστά. Όταν υπάρχουν διαφορές ή επιβαρύνσεις
βγαίνει επεξηγηματική καρτέλα PDF.

CLI:
    python efka_teka_reconciliation.py --username U --afm AFM --amka AMKA --year 2025 --out-dir DIR
        (ο κωδικός ζητείται κρυφά αν δεν δοθεί με --password ή EFKA_PASS)
    python efka_teka_reconciliation.py --from-raw DIR/efka_recon_raw_<afm>_<year>.json --out-dir DIR
        (ξανατρέχει μόνο αντιπαραβολή + καρτέλα από αποθηκευμένα δεδομένα)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import getpass
import html as _html
import json
import logging
import math
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import efka_teka_certificate as cert  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SVC = "https://services.e-efka.gov.gr/"
OBL_FORM = "form-id:accordion-insurance-obligations-citizen"
OBL_PATH_FALLBACK = "/ssp.efka.non.employees/views/insuranceObligations.xhtml"
KEAO_PATH_FALLBACK = "/ssp.efka.keao/views/eDebtorCM/mainCM.xhtml?from=commonservices-home"
AJAX_HEADERS = {"Faces-Request": "partial/ajax", "X-Requested-With": "XMLHttpRequest"}
TOLERANCE = 0.02

EFKA_MH_MISTHWTWN_FOREA = (
    "ΕΝΙΑΙΟΣ ΦΟΡΕΑΣ ΚΟΙΝΩΝΙΚΗΣ ΑΣΦΑΛΙΣΗΣ - ΕΝΙΑΙΟΣ ΦΟΡΕΑΣ ΚΟΙΝΩΝΙΚΗΣ ΑΣΦΑΛΙΣΗΣ"
)

_AMOUNT_RE = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}|-?\d+,\d{2}|-?\d+\.\d{2}(?!\d)")
_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


# ------------------------------------------------------------------ helpers
def progress(pct: float, label: str) -> None:
    """Βήμα προόδου για τον καλούντα (e3_brain): γραμμή στο stderr που διαβάζεται live."""
    try:
        sys.stderr.write(f"@@PROGRESS@@ {int(max(0, min(100, pct)))} {label}\n")
        sys.stderr.flush()
    except Exception:
        pass


def _norm(s: str) -> str:
    """Πεζά χωρίς τόνους — για ταίριασμα επικεφαλίδων."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", s).strip().lower()


def to_amount(text: Any) -> Optional[float]:
    """«1.234,56 €» → 1234.56· None αν δεν υπάρχει ποσό."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    t = str(text).replace("\xa0", " ")
    m = _AMOUNT_RE.search(t)
    if not m:
        t2 = re.sub(r"[^\d\-]", "", t)
        if t2 and t2 not in ("-",) and re.fullmatch(r"-?\d+", t2) and len(t2) < 9:
            return float(t2)
        return None
    a = m.group(0)
    if "," in a:
        a = a.replace(".", "").replace(",", ".")
    try:
        return float(a)
    except ValueError:
        return None


def _r2(x: float) -> float:
    return round(float(x or 0.0) + 0.0, 2)


def fmt_eur(x: Optional[float]) -> str:
    if x is None:
        return "—"
    s = f"{abs(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if x < -0.004 else "") + s + " €"


def _year_of(text: str) -> Optional[int]:
    m = _DATE_RE.search(text or "")
    if m:
        return int(m.group(3))
    m = re.search(r"(?<!\d)(19|20)\d{2}(?!\d)", text or "")
    return int(m.group(0)) if m else None


def _strip_cell(c: str) -> str:
    # Τα responsive PrimeFaces tables βάζουν <span class="ui-column-title"> μέσα στο td.
    c = re.sub(r'<span\b[^>]*class="[^"]*ui-column-title[^"]*"[^>]*>[\s\S]*?</span>', " ", c or "", flags=re.I)
    c = re.sub(r"<script\b[\s\S]*?</script>", " ", c, flags=re.I)
    return cert._strip_tags(c)


# ------------------------------------------------------- generic datatables
def parse_datatables(html: str) -> List[Dict[str, Any]]:
    """Όλοι οι PrimeFaces datatables ενός HTML: [{id, headers, rows:[[κελιά]], buttons:[[ids]], row_count}].
    row_count = συνολικές εγγραφές κατά τον paginator (0 αν άγνωστο)."""
    out: List[Dict[str, Any]] = []
    for m in re.finditer(r'<div\b[^>]*id="([^"]+)"[^>]*class="[^"]*\bui-datatable\b[^"]*"', html or "", re.I):
        tid = m.group(1)
        start = m.start()
        nxt = re.search(r'<div\b[^>]*id="[^"]+"[^>]*class="[^"]*\bui-datatable\b[^"]*"', html[m.end():], re.I)
        end = m.end() + nxt.start() if nxt else len(html)
        seg = html[start:end]
        headers: List[str] = []
        thead = re.search(r"<thead\b[\s\S]*?</thead>", seg, re.I)
        if thead:
            for th in re.finditer(r"<th\b([^>]*)>([\s\S]*?)</th>", thead.group(0), re.I):
                tm = re.search(r'<span\b[^>]*class="[^"]*ui-column-title[^"]*"[^>]*>([\s\S]*?)</span>', th.group(2), re.I)
                if tm:
                    headers.append(cert._strip_tags(tm.group(1)))
                else:
                    am = re.search(r'aria-label="([^"]*)"', th.group(1))
                    headers.append(cert._strip_tags(th.group(2)) or (cert._decode_html(am.group(1)) if am else ""))
        body = re.search(r'<tbody\b[^>]*id="' + re.escape(tid) + r'_data"[^>]*>([\s\S]*?)</tbody>', seg, re.I)
        body_html = body.group(1) if body else seg
        rows: List[List[str]] = []
        buttons: List[List[str]] = []
        for tr in re.finditer(r"<tr\b([^>]*)>([\s\S]*?)</tr>", body_html, re.I):
            if not re.search(r'data-ri="\d+"', tr.group(1)):
                continue
            tds = re.findall(r"<td\b[^>]*>([\s\S]*?)</td>", tr.group(2), re.I)
            rows.append([_strip_cell(c) for c in tds])
            buttons.append(re.findall(r'<button\b[^>]*id="([^"]*)"', tr.group(2), re.I))
        if not headers and rows:
            # responsive mode: τίτλοι μέσα στα κελιά
            first = re.search(r"<tr\b[^>]*data-ri=\"0\"[^>]*>([\s\S]*?)</tr>", body_html, re.I)
            if first:
                headers = [cert._strip_tags(x) for x in re.findall(
                    r'<span\b[^>]*class="[^"]*ui-column-title[^"]*"[^>]*>([\s\S]*?)</span>', first.group(1), re.I)]
        row_count = 0
        rpp = 0
        # Widget config στο τέλος του πίνακα: PrimeFaces.cw("DataTable",..,{id:"<tid>",paginator:{..rows:10,rowCount:74..}})
        pc = re.search(r'id:"' + re.escape(tid) + r'",paginator:\{([^}]*)\}', html)
        if pc:
            rm = re.search(r"\browCount:\s*(\d+)", pc.group(1))
            pm = re.search(r"\brows:\s*(\d+)", pc.group(1))
            row_count = int(rm.group(1)) if rm else 0
            rpp = int(pm.group(1)) if pm else 0
        else:
            rc = re.search(r'id="' + re.escape(tid) + r'"[\s\S]{0,6000}?rowCount:\s*(\d+)', html, re.I)
            if rc:
                row_count = int(rc.group(1))
            rp = re.search(r'id="' + re.escape(tid) + r'"[\s\S]{0,6000}?paginator:\{[^}]*?rows:\s*(\d+)', html, re.I)
            if rp:
                rpp = int(rp.group(1))
        out.append({"id": tid, "headers": headers, "rows": rows, "buttons": buttons,
                    "row_count": row_count, "rows_per_page": rpp})
    return out


# ------------------------------------------------- column classification
_ROLE_RULES: List[Tuple[str, Tuple[str, ...]]] = [
    ("surcharge", ("προσαυξ", "προστιμ", "επιβαρυν", "τοκ", "προσθετα τελ", "προσθετ")),
    ("total", ("συνολ",)),
    ("net", ("εισφορ", "κυρια", "καθαρ")),
    ("date", ("ημερομην", "ημ/νια", "ημ. ", "ημνια")),
    ("period", ("περιοδ", "μηνα", "μηνας", "ετος", "ετους")),
    ("amount", ("ποσ", "πληρωθ", "καταβλ")),
    ("kind", ("φορεα", "κλαδ", "ταμει", "ειδος", "τυπος", "κατηγορ")),
    ("doc", ("παραστατ", "κωδικ", "αριθμ", "ταυτοτ", "rf")),
]


def classify_headers(headers: List[str]) -> Dict[str, List[int]]:
    roles: Dict[str, List[int]] = {}
    for i, h in enumerate(headers):
        n = _norm(h)
        if not n:
            continue
        for role, keys in _ROLE_RULES:
            if any(k in n for k in keys):
                # «Ημ/νία Καταβολής» είναι ημερομηνία, όχι ποσό· «Ποσό Εισφοράς» είναι καθαρό.
                roles.setdefault(role, []).append(i)
                break
    # προτίμηση ημερομηνίας πληρωμής
    if len(roles.get("date", [])) > 1:
        pref = [i for i in roles["date"] if any(k in _norm(headers[i]) for k in ("καταβολ", "πληρωμ", "εισπραξ", "εξοφλ", "πιστωσ"))]
        if pref:
            roles["date"] = pref + [i for i in roles["date"] if i not in pref]
    return roles


def _kind_of(text: str, default: str = "EFKA") -> str:
    n = _norm(text)
    if "τεκα" in n or "teka" in n:
        return "TEKA"
    return default


def normalize_payment_rows(table: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Γραμμές πίνακα «Καταβολές» → [{date, year, period, kind, net, surcharge, total, doc, raw}]."""
    headers = table.get("headers") or []
    roles = classify_headers(headers)
    tkind = _kind_of(table.get("id", ""), "EFKA")
    out: List[Dict[str, Any]] = []
    for cells in table.get("rows") or []:
        def cell(role: str, k: int = 0) -> str:
            idx = roles.get(role, [])
            return cells[idx[k]] if len(idx) > k and idx[k] < len(cells) else ""
        if not any(c.strip() for c in cells):
            continue
        if any("δεν βρεθηκαν" in _norm(c) for c in cells):
            continue
        sur = 0.0
        for i in roles.get("surcharge", []):
            if i < len(cells):
                sur += to_amount(cells[i]) or 0.0
        net = to_amount(cell("net")) if roles.get("net") else None
        amount = to_amount(cell("amount")) if roles.get("amount") else None
        total = to_amount(cell("total")) if roles.get("total") else None
        if net is None:
            if amount is not None:
                net = amount
            elif total is not None:
                net = total - sur
        if net is None:
            continue
        if total is None:
            total = net + sur
        date = cell("date")
        date_alt = cell("date", 1)
        period = cell("period")
        year = _year_of(date) or _year_of(period)
        kind = _kind_of(cell("kind"), tkind) if roles.get("kind") else tkind
        out.append({
            "date": date, "year": year, "period": period, "kind": kind,
            "date_alt": date_alt, "year_alt": _year_of(date_alt) if date_alt else None,
            "net": _r2(net), "surcharge": _r2(sur), "total": _r2(total),
            "doc": cell("doc"), "raw": dict(zip(headers, cells)) if headers else cells,
        })
    return out


# ------------------------------------------------------------ HTTP engine
class _Http(cert._HyperHttp):
    """Ίδιο cookie jar με το efka_teka_certificate + προαιρετικά extra headers
    (Faces-Request: partial/ajax για τα endpoints του ΚΕΑΟ)."""

    def __init__(self, timeout: float = 45.0):
        super().__init__(timeout)
        self._extra: Optional[Dict[str, str]] = None

    def _once(self, method, url, form=None):  # type: ignore[override]
        import requests
        from urllib.parse import urlencode
        headers = {
            "User-Agent": cert.UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "el-GR,el;q=0.9,en;q=0.8",
        }
        if self._extra:
            headers.update(self._extra)
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

    def ajax(self, url: str, form: Dict[str, str]) -> Dict[str, Any]:
        self._extra = dict(AJAX_HEADERS)
        try:
            return self.follow("POST", url, form)
        finally:
            self._extra = None


class _Diag:
    def __init__(self, root: Optional[Path]):
        self.root = root
        if root:
            root.mkdir(parents=True, exist_ok=True)

    def dump(self, name: str, text: str) -> None:
        if not self.root:
            return
        try:
            (self.root / name).write_text(text or "", encoding="utf-8")
        except Exception as exc:
            logging.warning("diag %s: %s", name, exc)


def _find_link(links: Dict[str, str], landing: str, phrase: str, href_pat: str, fallback: str) -> str:
    for k, v in links.items():
        if phrase in k:
            return v
    m = re.search(r'href="([^"]*' + href_pat + r'[^"]*)"', landing or "", re.I)
    if m:
        return urljoin(SVC, cert._decode_html(m.group(1)))
    return urljoin(SVC, fallback)


# ------------------------------------------------------------ certificates
def fetch_certificates(http: _Http, royalties_url: str, afm: str, year: str, pdf_dir: Path,
                       diag: _Diag) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for kind in ("EFKA", "TEKA"):
        try:
            res = cert._get_certificates(http, royalties_url, kind, afm, year, pdf_dir)
        except Exception as exc:
            logging.exception("certificate %s failed", kind)
            res = {"ok": False, "error": f"{kind}: {exc}"}
        # Ποσό: πίνακας σελίδας πρώτα, PDF ως fallback.
        amount = None
        source = None
        row = res.get("table_row") or {}
        if res.get("table_amount") is not None:
            amount, source = res["table_amount"], "table"
        elif isinstance(res.get("amount"), dict) and res["amount"].get("best_guess") is not None:
            amount, source = res["amount"]["best_guess"], "pdf"
        res["certificate_amount"] = amount
        res["certificate_amount_source"] = source
        res["holder"] = {k: row.get(k) for k in ("Επίθετο", "Όνομα", "ΑΜΚΑ", "ΑΦΜ", "Ημ/νία Έκδοσης") if row.get(k)}
        out[kind] = res
    return out


# ------------------------------------------------------------ payments
def _tabs_of(html: str) -> List[Dict[str, str]]:
    tabs = []
    for li in re.finditer(r'<li\b[^>]*class="ui-tabs-header[^"]*"[^>]*>([\s\S]*?)</li>', html or "", re.I):
        dm = re.search(r'data-index="([^"]*)"', li.group(0), re.I)
        hm = re.search(r'<a\b[^>]*href="([^"]*)"', li.group(1), re.I)
        tabs.append({"label": cert._strip_tags(li.group(1)), "dataIndex": dm.group(1) if dm else "",
                     "href": cert._decode_html(hm.group(1) if hm else "")})
    return tabs


def _selects_of(html: str) -> List[Dict[str, Any]]:
    out = []
    for s in re.finditer(r'<select\b([^>]*)>([\s\S]*?)</select>', html or "", re.I):
        nm = re.search(r'name="([^"]*)"', s.group(1))
        opts = [(cert._decode_html(v), cert._strip_tags(t)) for v, t in
                re.findall(r'<option\b[^>]*value="([^"]*)"[^>]*>([\s\S]*?)</option>', s.group(2), re.I)]
        sel = re.search(r'<option\b[^>]*value="([^"]*)"[^>]*selected', s.group(2), re.I)
        out.append({"name": nm.group(1) if nm else "", "options": opts[:40],
                    "selected": sel.group(1) if sel else ""})
    return out


def _paginate(http: _Http, url: str, table: Dict[str, Any], vs: str, active_index: str,
              upd_html: str, diag: _Diag) -> List[List[str]]:
    """Επόμενες σελίδες ενός datatable (PrimeFaces pagination ajax)."""
    rows = list(table["rows"])
    total = table.get("row_count") or 0
    rpp = table.get("rows_per_page") or len(rows) or 10
    if not total or total <= len(rows):
        return rows
    src = table["id"]
    first = len(rows)
    guard = 0
    while first < total and guard < 200:
        form = {
            "jakarta.faces.partial.ajax": "true", "jakarta.faces.source": src,
            "jakarta.faces.partial.execute": src, "jakarta.faces.partial.render": src, src: src,
            f"{src}_pagination": "true", f"{src}_first": str(first), f"{src}_rows": str(rpp),
            f"{src}_skipChildren": "true", f"{src}_encodeFeature": "true",
            "form-id": "form-id", f"{OBL_FORM}_activeIndex": active_index,
            "jakarta.faces.ViewState": vs,
        }
        if total:
            progress(30 + 15 * min(1.0, first / total), f"Καταβολές e-ΕΦΚΑ — σελίδα {guard + 2}/{max(1, -(-total // rpp))}")
        xml = http.ajax(url, form)["text"]
        diag.dump(f"payments_page_{guard + 2}.xml", xml)
        frag = re.findall(r"<update[^>]*><!\[CDATA\[([\s\S]*?)\]\]></update>", xml, re.I)
        got = []
        for f in frag:
            for tr in re.finditer(r"<tr\b([^>]*)>([\s\S]*?)</tr>", f, re.I):
                if re.search(r'data-ri="\d+"', tr.group(1)):
                    got.append([_strip_cell(c) for c in re.findall(r"<td\b[^>]*>([\s\S]*?)</td>", tr.group(2), re.I)])
        if not got:
            break
        rows.extend(got)
        first += len(got)
        guard += 1
    return rows


PAYMENT_CATEGORIES = {
    # κατηγορία: (ετικέτα, μετρά ως καταβολή στον e-ΕΦΚΑ;)
    "efka_payments": ("Καταβολές στον e-ΕΦΚΑ μη μισθωτών (Ιστορικό Πληρωμών)", True),
    "bar_bills": ("Γραμμάτια προείσπραξης δικηγορικών συλλόγων", True),
    "credit_balances": ("Πιστωτικά υπόλοιπα e-ΕΦΚΑ (κατά ημ. αναγγελίας)", True),
    "debit_balances": ("Χρεωστικά υπόλοιπα e-ΕΦΚΑ (πληροφοριακά)", False),
    "other": ("Άλλος πίνακας (πληροφοριακά)", False),
}


def table_category(tid: str) -> str:
    """Πίνακες του tab «Καταβολές» (ids επιβεβαιωμένα live 2026-10-15)."""
    t = (tid or "").lower()
    if "total-payments" in t:
        return "efka_payments"
    if "bill-citizen" in t:
        return "bar_bills"
    if "credits-citizen" in t:
        return "credit_balances"
    if "debts-citizen" in t:
        return "debit_balances"
    return "other"


def fetch_payments(http: _Http, obl_url: str, diag: _Diag) -> Dict[str, Any]:
    res: Dict[str, Any] = {"ok": False, "tabs": [], "tables": [], "rows": [], "selects": [], "error": None}
    page = http.follow("GET", obl_url)["text"]
    diag.dump("obligations_page.html", page)
    if not cert._has_id(page, OBL_FORM):
        res["error"] = "Η σελίδα Υποχρεώσεων Ασφάλισης δεν περιέχει το αναμενόμενο accordion."
        return res
    tabs = _tabs_of(page)
    res["tabs"] = [t["label"] for t in tabs]
    tab = next((t for t in tabs if "καταβολ" in _norm(t["label"])), None)
    if not tab:
        res["error"] = "Δεν βρέθηκε tab «Καταβολές» (διαθέσιμα: " + ", ".join(res["tabs"]) + ")."
        return res
    vs = cert._view_state(page)
    new_tab = tab["href"].lstrip("#")
    logging.info("[payments] tabChange «%s» (dataIndex=%s)", tab["label"], tab["dataIndex"])
    resp = http.follow("POST", obl_url, {
        "jakarta.faces.partial.ajax": "true", "jakarta.faces.source": OBL_FORM,
        "jakarta.faces.partial.execute": OBL_FORM, "jakarta.faces.partial.render": OBL_FORM,
        "jakarta.faces.behavior.event": "tabChange", "jakarta.faces.partial.event": "tabChange",
        f"{OBL_FORM}_contentLoad": "true", f"{OBL_FORM}_newTab": new_tab,
        f"{OBL_FORM}_tabindex": tab["dataIndex"], "form-id": "form-id",
        f"{OBL_FORM}_activeIndex": tab["dataIndex"], "jakarta.faces.ViewState": vs,
    })
    diag.dump("payments_tab.xml", resp["text"])
    upd = cert._extract_update(resp["text"], OBL_FORM)
    diag.dump("payments_tab.html", upd)
    res["selects"] = _selects_of(upd)
    tables = parse_datatables(upd)
    for t in tables:
        try:
            t["rows"] = _paginate(http, obl_url, t, vs, tab["dataIndex"], upd, diag)
        except Exception as exc:
            logging.warning("[payments] pagination %s: %s", t["id"], exc)
        cat = table_category(t["id"])
        norm = normalize_payment_rows(t)
        for r in norm:
            r["source"] = cat
        res["tables"].append({"id": t["id"], "category": cat, "headers": t["headers"], "row_count": t["row_count"],
                              "rows_parsed": len(t["rows"]), "roles": classify_headers(t["headers"]),
                              "raw_rows": t["rows"]})
        if t["row_count"] and len(t["rows"]) < t["row_count"]:
            logging.warning("[payments] %s: %d/%d γραμμές", t["id"], len(t["rows"]), t["row_count"])
        res["rows"].extend(norm)
    res["ok"] = True
    if not tables:
        res["error"] = "Το tab «Καταβολές» δεν περιείχε πίνακα (δες _diag/payments_tab.html)."
    return res


# ------------------------------------------------------------ ειδοποιητήρια
_MONEY_RE = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}")
_NOTICE_FIELDS = [
    # (πεδίο, λέξεις-κλειδιά στη γραμμή — πεζά χωρίς τόνους)
    ("monthly", ("μηνιαια εισφορα", "συνολο μηνιαιας εισφορας")),
    ("credits", ("πιστωσεις",)),
    ("fees", ("επιβαρυνσεις",)),
    ("retro", ("αναδρομικα",)),
    ("charges", ("χρεωσεις",)),
    ("arrears", ("καθυστερουμενες οφειλες",)),
    ("payable", ("πληρωτεο",)),
]


def parse_notice_text(text: str) -> Dict[str, Any]:
    """Ειδοποίηση Πληρωμής Ασφαλιστικών Εισφορών (e-ΕΦΚΑ ή ΤΕΚΑ) — και οι δύο μορφές
    (παλιά: «ΜΗΝΙΑΙΑ ΕΙΣΦΟΡΑ / ΠΙΣΤΩΣΕΙΣ / ΕΠΙΒΑΡΥΝΣΕΙΣ - ΠΡΟΣΘΕΤΑ ΤΕΛΗ / ΠΛΗΡΩΤΕΟ ΠΟΣΟ»,
    νέα 2026: ενότητες Α–Ε). Ποσά σε float· ό,τι λείπει = 0."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    head = " ".join(lines[:6]).upper()
    out: Dict[str, Any] = {
        "kind": "TEKA" if ("ΤΕΚΑ" in head or "TEKA" in head or "ΚΕΦΑΛΑΙΟΠΟΙΗΤΙΚΗΣ" in head) else "EFKA",
        "period": None, "issue_date": None, "due_date": None, "rf": None, "notice_id": None,
        "monthly": 0.0, "credits": 0.0, "fees": 0.0, "retro": 0.0, "charges": 0.0,
        "bar_bills": 0.0, "arrears": 0.0, "payable": None, "afm": None, "amka": None,
    }
    full = "\n".join(lines)
    m = re.search(r"ΗΜΕΡΟΜΗΝΙΑ ΕΚΔΟΣΗΣ\s*:\s*(\d{2}/\d{2}/\d{4})", full, re.I)
    out["issue_date"] = m.group(1) if m else None
    m = re.search(r"ΑΝΑΛΥΣΗ ΜΗΝΙΑΙΑΣ ΕΙΣΦΟΡΑΣ\s*\(?\s*(\d{2}/\d{4})", full, re.I)
    out["period"] = m.group(1) if m else None
    m = re.search(r"ΕΜΠΡΟΘΕΣΜΗ ΠΛΗΡΩΜΗ ΕΩΣ\s*:\s*(\d{2}/\d{2}/\d{4})", full, re.I)
    if m:
        out["due_date"] = m.group(1)
    elif out["period"]:
        m = re.search(re.escape(out["period"]) + r"\s+(\d{2}/\d{2}/\d{4})", full)
        out["due_date"] = m.group(1) if m else None
    m = re.search(r"\bAMKA\b\s*:?\s*(\d{11})|ΑΜΚΑ\s*:?\s*(\d{11})", full)
    out["amka"] = (m.group(1) or m.group(2)) if m else None
    afms = re.findall(r"ΑΦΜ\s*:\s*(\d{9})", full)
    out["afm"] = afms[1] if len(afms) > 1 else (afms[0] if afms else None)  # 1ο = ΑΦΜ φορέα
    for i, l in enumerate(lines):
        if re.match(r"^RF\d{2}[\d ]{10,}$", l.replace(" ", " ")):
            out["rf"] = l.replace(" ", "")
            if i + 1 < len(lines) and re.fullmatch(r"\d{6,12}", lines[i + 1]):
                out["notice_id"] = lines[i + 1]
    seen = set()
    for i, l in enumerate(lines):
        n = _norm(l)
        if "γραμ" in n and "προεισπραξ" in n:
            # Το ποσό είναι στην ίδια γραμμή ή σε γραμμή-συνέχεια («10 αρθρ.35 …»), ΠΟΤΕ σε γραμμή
            # άλλου πεδίου (π.χ. «ΚΑΘΥΣΤΕΡΟΥΜΕΝΕΣ ΟΦΕΙΛΕΣ 187,08 €» αμέσως μετά).
            follow = lines[i + 1] if i + 1 < len(lines) else ""
            fn = _norm(follow)
            if any(k in fn for k in ("καθυστερ", "πληρωτ", "πιστωσ", "υπολοιπ", "συνολο", "επιβαρ", "χρεωσ", "αναδρομ")):
                follow = ""
            nxt = l + " " + follow
            am = _MONEY_RE.findall(re.sub(r"\(.*?\)", " ", nxt))
            if am and "bar_bills" not in seen:
                out["bar_bills"] = abs(to_amount(am[-1]) or 0.0)
                seen.add("bar_bills")
            continue
        for field, keys in _NOTICE_FIELDS:
            if field in seen or not any(k in n for k in keys):
                continue
            if field == "monthly" and "αναλυση" in n:
                continue
            if field == "charges" and "λοιπων" in n:
                continue
            am = _MONEY_RE.findall(l)
            val = to_amount(am[-1]) if am else 0.0
            if field == "credits":
                val = abs(val or 0.0)
            out[field] = _r2(val or 0.0)
            seen.add(field)
            break
    if out["payable"] is None:
        m = re.search(r"ΣΥΝΟΛΟ\s*:\s*(" + _MONEY_RE.pattern + ")", full, re.I)
        out["payable"] = to_amount(m.group(1)) if m else None
    # Έλεγχος ταυτότητας: πληρωτέο = μηνιαία + επιβαρύνσεις + αναδρομικά + χρεώσεις − πιστώσεις − γραμμάτια
    calc = _r2(out["monthly"] + out["fees"] + out["retro"] + out["charges"] - out["credits"] - out["bar_bills"])
    out["payable_calc"] = calc
    out["identity_ok"] = out["payable"] is not None and abs(calc - out["payable"]) <= 0.02
    return out


def parse_notice_pdf(pdf_bytes: bytes) -> Dict[str, Any]:
    import io
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
    res = parse_notice_text(text)
    res["text_excerpt"] = text[:1500]
    return res


def _period_key(p: Optional[str]) -> Optional[Tuple[int, int]]:
    m = re.fullmatch(r"(\d{2})/(\d{4})", p or "")
    return (int(m.group(2)), int(m.group(1))) if m else None


def _paginate_full(http: _Http, url: str, table: Dict[str, Any], vs: str, active_index: str,
                   diag: _Diag, tag: str) -> Tuple[List[List[str]], List[List[str]]]:
    """Όπως _paginate αλλά κρατά και τα button ids ανά γραμμή."""
    rows, buttons = list(table["rows"]), list(table.get("buttons") or [[] for _ in table["rows"]])
    total = table.get("row_count") or 0
    rpp = table.get("rows_per_page") or len(rows) or 10
    src = table["id"]
    first, guard = len(rows), 0
    while total and first < total and guard < 100:
        xml = http.ajax(url, {
            "jakarta.faces.partial.ajax": "true", "jakarta.faces.source": src,
            "jakarta.faces.partial.execute": src, "jakarta.faces.partial.render": src, src: src,
            f"{src}_pagination": "true", f"{src}_first": str(first), f"{src}_rows": str(rpp),
            f"{src}_skipChildren": "true", f"{src}_encodeFeature": "true",
            "form-id": "form-id", f"{OBL_FORM}_activeIndex": active_index, "jakarta.faces.ViewState": vs,
        })["text"]
        if guard == 0:
            diag.dump(f"{tag}_page2.xml", xml)
        got = 0
        for f in re.findall(r"<update[^>]*><!\[CDATA\[([\s\S]*?)\]\]></update>", xml, re.I):
            for tr in re.finditer(r"<tr\b([^>]*)>([\s\S]*?)</tr>", f, re.I):
                if re.search(r'data-ri="\d+"', tr.group(1)):
                    rows.append([_strip_cell(c) for c in re.findall(r"<td\b[^>]*>([\s\S]*?)</td>", tr.group(2), re.I)])
                    buttons.append(re.findall(r'<button\b[^>]*id="([^"]*)"', tr.group(2), re.I))
                    got += 1
        if not got:
            break
        first += got
        guard += 1
    return rows, buttons


def fetch_notices(http: _Http, obl_url: str, year: int, out_dir: Path, diag: _Diag,
                  max_pdfs: int = 60, kinds: Optional[set] = None) -> Dict[str, Any]:
    """«Υποχρεώσεις Ασφάλισης → Μηνιαία Ειδοποιητήρια»: PDF ειδοποιητηρίων e-ΕΦΚΑ/ΤΕΚΑ για
    τις περιόδους 10/(έτος−1) … 12/έτος (οι πληρωμές του έτους αφορούν και ειδοποιητήρια του
    προηγούμενου). Η δομή του tab δεν έχει επιβεβαιωθεί live — όλα σώζονται στο _diag."""
    res: Dict[str, Any] = {"ok": False, "notices": [], "tables": [], "selects": [], "error": None}
    out_dir.mkdir(parents=True, exist_ok=True)
    page = http.follow("GET", obl_url)["text"]
    tabs = _tabs_of(page)
    tab = next((t for t in tabs if "ειδοποιητηρ" in _norm(t["label"]) and "μηνιαι" in _norm(t["label"])), None) \
        or next((t for t in tabs if "history-of-notification" in t["href"]), None)
    if not tab:
        res["error"] = "Δεν βρέθηκε tab «Μηνιαία Ειδοποιητήρια»."
        return res
    vs = cert._view_state(page)
    idx = tab["dataIndex"]

    def tab_change() -> str:
        r = http.follow("POST", obl_url, {
            "jakarta.faces.partial.ajax": "true", "jakarta.faces.source": OBL_FORM,
            "jakarta.faces.partial.execute": OBL_FORM, "jakarta.faces.partial.render": OBL_FORM,
            "jakarta.faces.behavior.event": "tabChange", "jakarta.faces.partial.event": "tabChange",
            f"{OBL_FORM}_contentLoad": "true", f"{OBL_FORM}_newTab": tab["href"].lstrip("#"),
            f"{OBL_FORM}_tabindex": idx, "form-id": "form-id",
            f"{OBL_FORM}_activeIndex": idx, "jakarta.faces.ViewState": vs,
        })
        return r["text"]

    xml = tab_change()
    diag.dump("notices_tab.xml", xml)
    upd = cert._extract_update(xml, OBL_FORM)
    diag.dump("notices_tab.html", upd)
    res["selects"] = _selects_of(upd)
    views = [("default", upd)]
    # Φίλτρο έτους (αν υπάρχει): δοκιμή αλλαγής σε έτος και έτος−1.
    for sel in res["selects"]:
        years = [v for v, t in sel["options"] if re.fullmatch(r"(19|20)\d{2}", (t or v or "").strip())]
        if not years or not sel["name"]:
            continue
        base = sel["name"][:-6] if sel["name"].endswith("_input") else sel["name"]
        for y in (str(year), str(year - 1)):
            val = next((v for v, t in sel["options"] if (t or "").strip() == y or v == y), None)
            if val is None or val == sel.get("selected"):
                continue
            for ev in ("change", "itemSelect", "valueChange"):
                try:
                    x = http.ajax(obl_url, {
                        "jakarta.faces.partial.ajax": "true", "jakarta.faces.source": base,
                        "jakarta.faces.partial.execute": base, "jakarta.faces.partial.render": OBL_FORM,
                        "jakarta.faces.behavior.event": ev, "jakarta.faces.partial.event": ev,
                        sel["name"]: val, "form-id": "form-id", f"{OBL_FORM}_activeIndex": idx,
                        "jakarta.faces.ViewState": vs,
                    })["text"]
                except Exception as exc:
                    logging.warning("[notices] year filter %s/%s: %s", y, ev, exc)
                    continue
                diag.dump(f"notices_filter_{y}_{ev}.xml", x)
                frag = "".join(re.findall(r"<update[^>]*><!\[CDATA\[([\s\S]*?)\]\]></update>", x, re.I))
                if "data-ri" in frag:
                    views.append((f"year{y}", frag))
                    break
    lo, hi = (year - 1, 10), (year, 12)
    wanted: List[Tuple[str, str, List[str], str, Dict[str, str]]] = []  # (kind, period, buttons, table_id, extra form)
    seen_btn = set()

    def collect_rows(vname: str, tables: List[Dict[str, Any]], extra: Dict[str, str], paginate: bool) -> None:
        for t in tables:
            rows, buttons = t["rows"], t["buttons"]
            if paginate:
                try:
                    rows, buttons = _paginate_full(http, obl_url, t, vs, idx, diag, "notices_" + vname)
                except Exception as exc:
                    logging.warning("[notices] pagination %s: %s", t["id"], exc)
            res["tables"].append({"view": vname, "id": t["id"], "headers": t["headers"], "row_count": t["row_count"],
                                  "rows_parsed": len(rows), "sample": rows[:3]})
            tkind = _kind_of(t["id"] + " " + " ".join(t["headers"]), "EFKA")
            for cells, btns in zip(rows, buttons):
                joined = " ".join(cells)
                pk = None
                # «Μήνας Οφειλής» = 01/MM/YYYY (επιβεβαιωμένο live) ή MM/YYYY
                mm = re.search(r"(?<!\d)\d{2}/(\d{2})/(\d{4})(?!\d)", cells[0] if cells else "") \
                    or re.search(r"(?<![/\d])(\d{2})/(\d{4})(?![/\d])", joined)
                if mm:
                    pk = (int(mm.group(2)), int(mm.group(1)))
                kind = _kind_of(joined, tkind)
                if pk and not (lo <= pk <= hi):
                    continue
                if kinds and kind not in kinds:
                    continue
                if not btns or any(b in seen_btn for b in btns):
                    continue
                seen_btn.update(btns)
                wanted.append((kind, f"{pk[1]:02d}/{pk[0]}" if pk else "", btns, t["id"], extra))

    # «Μηνιαία Ειδοποιητήρια» = ένα AccordionPanel ανά έτος (yearAccordionN, τίτλος = έτος). Μόνο το
    # πρώτο (τρέχον έτος) έρχεται φορτωμένο· τα υπόλοιπα ανοίγουν με ajax tabChange που ανανεώνει
    # τον πίνακα του έτους (u:"…:notifications-history-citizen-datatable").
    accs: List[Dict[str, Any]] = []
    for m in re.finditer(r'PrimeFaces\.cw\("AccordionPanel","[^"]*",\{id:"([^"]+)"[\s\S]*?u:"([^"]+)"', upd):
        acc, dt = m.group(1), m.group(2)
        hm = re.search(r'<div\b[^>]*id="(' + re.escape(acc) + r':[^"]*?)_header"([^>]*)>([\s\S]*?)</div>', upd)
        if not hm:
            continue
        ym = re.search(r"(?:19|20)\d{2}", cert._strip_tags(hm.group(3)))
        accs.append({"acc": acc, "dt": dt, "content": hm.group(1), "year": int(ym.group(0)) if ym else None,
                     "active": "ui-state-active" in hm.group(2)})
    res["accordions"] = [{k: x[k] for k in ("year", "active")} for x in accs]
    if accs:
        loaded = {t["id"]: t for t in parse_datatables(upd)}
        for x in accs:
            if x["year"] not in (year, year - 1):
                continue
            extra = {f"{x['acc']}_active": "0"}
            t0 = loaded.get(x["dt"])
            if x["active"] and t0 and t0["rows"]:
                collect_rows(f"y{x['year']}", [t0], {}, True)
                continue
            try:
                ax = http.ajax(obl_url, {
                    "jakarta.faces.partial.ajax": "true", "jakarta.faces.source": x["acc"],
                    "jakarta.faces.partial.execute": x["acc"], "jakarta.faces.partial.render": x["dt"],
                    "jakarta.faces.behavior.event": "tabChange", "jakarta.faces.partial.event": "tabChange",
                    f"{x['acc']}_newTab": x["content"], f"{x['acc']}_tabindex": "0", f"{x['acc']}_active": "0",
                    "form-id": "form-id", f"{OBL_FORM}_activeIndex": idx, "jakarta.faces.ViewState": vs,
                })["text"]
            except Exception as exc:
                logging.warning("[notices] άνοιγμα έτους %s: %s", x["year"], exc)
                continue
            diag.dump(f"notices_year_{x['year']}.xml", ax)
            frag = "".join(re.findall(r"<update[^>]*><!\[CDATA\[([\s\S]*?)\]\]></update>", ax, re.I))
            tables = [t for t in parse_datatables(frag) if t["id"] == x["dt"]] or parse_datatables(frag)
            if not tables:
                # update χωρίς το wrapper div: μόνο γραμμές
                rws, bts = [], []
                for tr in re.finditer(r"<tr\b([^>]*)>([\s\S]*?)</tr>", frag, re.I):
                    if re.search(r'data-ri="\d+"', tr.group(1)):
                        rws.append([_strip_cell(c) for c in re.findall(r"<td\b[^>]*>([\s\S]*?)</td>", tr.group(2), re.I)])
                        bts.append(re.findall(r'<button\b[^>]*id="([^"]*)"', tr.group(2), re.I))
                tables = [{"id": x["dt"], "headers": [], "rows": rws, "buttons": bts, "row_count": 0, "rows_per_page": 0}]
            logging.info("[notices] έτος %s: %d γραμμές", x["year"], sum(len(t["rows"]) for t in tables))
            collect_rows(f"y{x['year']}", tables, extra, False)
    else:
        for vname, html in views:
            collect_rows(vname, parse_datatables(html), {}, vname == "default")
    if len(wanted) > max_pdfs:
        wanted = wanted[:max_pdfs]
    logging.info("[notices] %d ειδοποιητήρια προς λήψη", len(wanted))
    for _ni, (kind, period, btns, tid, extra) in enumerate(wanted, start=1):
        progress(72 + 20 * (_ni - 1) / max(1, len(wanted)),
                 f"Ειδοποιητήριο {'ΤΕΚΑ' if kind == 'TEKA' else 'e-ΕΦΚΑ'} {period} ({_ni}/{len(wanted)})")
        pdf = None
        for b in btns:
            try:
                form = {"form-id": "form-id", b: "", f"{OBL_FORM}_activeIndex": idx, "jakarta.faces.ViewState": vs}
                form.update(extra)
                pdf = http.post_for_pdf(obl_url, form)
            except Exception as exc:
                logging.warning("[notices] pdf %s: %s", b, exc)
            if pdf:
                break
        if not pdf:
            res["notices"].append({"kind": kind, "period": period, "error": "no_pdf", "buttons": btns})
            continue
        try:
            info = parse_notice_pdf(pdf)
        except Exception as exc:
            info = {"kind": kind, "period": period, "error": f"parse: {exc}"}
        name = f"notice_{info.get('kind') or kind}_{(info.get('period') or period or 'x').replace('/', '-')}_{info.get('notice_id') or len(res['notices'])}.pdf"
        (out_dir / name).write_bytes(pdf)
        info["pdf"] = str(out_dir / name)
        info.pop("text_excerpt", None)
        if period and info.get("period") and info["period"] != period:
            info["warning"] = f"Το PDF γράφει περίοδο {info['period']}, ο πίνακας {period}."
        res["notices"].append(info)
    res["ok"] = True
    if not wanted:
        res["error"] = "Δεν βρέθηκαν ειδοποιητήρια για λήψη (δες _diag/notices_tab.html)."
    return res


def load_notices_dir(path: Path) -> Dict[str, Any]:
    """Ειδοποιητήρια από τοπικό φάκελο PDF (offline δοκιμές / χειροκίνητη λήψη)."""
    out: Dict[str, Any] = {"ok": True, "source": "dir", "notices": []}
    seen: set = set()
    for p in sorted(Path(path).glob("*.pdf")):
        try:
            info = parse_notice_pdf(p.read_bytes())
            info.pop("text_excerpt", None)
            info["pdf"] = str(p)
            key = (info.get("kind"), info.get("period"), info.get("notice_id"), info.get("rf"))
            if info.get("period") and key not in seen:
                seen.add(key)
                out["notices"].append(info)
        except Exception as exc:
            logging.warning("notice %s: %s", p, exc)
    return out


def _date_obj(d: Optional[str]) -> Optional[_dt.date]:
    m = _DATE_RE.search(d or "")
    if not m:
        return None
    try:
        return _dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def apply_notices_by_match(rows: List[Dict[str, Any]], notices: List[Dict[str, Any]], tolerance: float = TOLERANCE) -> Dict[str, Any]:
    """(Fallback) Διαχωρισμός κάθε πληρωμής e-ΕΦΚΑ/ΤΕΚΑ σε καθαρό + επιβαρύνσεις μέσω του ειδοποιητηρίου
    της: ίδιος κωδικός RF (όταν υπάρχει), πληρωτέο == ποσό πληρωμής, ειδοποιητήριο εκδομένο
    πριν από την πληρωμή (το πλησιέστερο). Επιβαρύνσεις = «Επιβαρύνσεις – Πρόσθετα Τέλη»."""
    good, _keys = [], set()
    for n in notices or []:
        if n.get("payable") is None or not n.get("period"):
            continue
        key = (n.get("kind"), n.get("period"), n.get("notice_id") or n.get("issue_date"), n.get("rf"))
        if key in _keys:
            continue
        _keys.add(key)
        good.append(n)
    rf_kind = {n["rf"]: n["kind"] for n in good if n.get("rf")}
    used: set = set()
    stats = {"payments": 0, "matched": 0, "matched_by_amount": 0, "matched_by_sequence": 0,
             "unmatched": 0, "notices": len(good)}
    pays: List[Dict[str, Any]] = []
    for r in rows:
        if r.get("source") != "efka_payments":
            continue
        stats["payments"] += 1
        raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
        rf = re.sub(r"\s+", "", str(raw.get("Κωδικός Πληρωμής") or r.get("doc") or ""))
        if rf in rf_kind:
            r["kind"] = rf_kind[rf]
        r["_rf"] = rf
        r["_amount"] = r.get("total") if r.get("total") is not None else r.get("net")
        r["_pd"] = _date_obj(r.get("date"))
        r["notice"], r["fees"], r["split"] = None, 0.0, "unmatched"
        pays.append(r)
    pays.sort(key=lambda r: r["_pd"] or _dt.date(1900, 1, 1))

    def candidates(r):
        for i, n in enumerate(good):
            if i in used or n.get("kind") != r.get("kind"):
                continue
            if r["_rf"] and n.get("rf") and n["rf"] != r["_rf"]:
                continue
            nd = _date_obj(n.get("issue_date"))
            if r["_pd"] and nd and nd > r["_pd"] + _dt.timedelta(days=3):
                continue
            gap = (r["_pd"] - nd).days if (r["_pd"] and nd) else 9999
            yield i, n, gap

    def assign(r, i, how):
        used.add(i)
        n = good[i]
        amount = r["_amount"] or 0.0
        fees = _r2(min(n.get("fees") or 0.0, max(amount, 0.0)))
        r["fees"] = fees
        r["net"] = _r2(amount - fees)
        r["total"] = _r2(amount)
        r["notice"] = {k: n.get(k) for k in ("period", "issue_date", "due_date", "notice_id", "monthly",
                                             "credits", "fees", "retro", "charges", "bar_bills", "payable")}
        r["period"] = n.get("period")
        r["split"] = how
        stats["matched"] += 1
        stats["matched_by_" + ("amount" if how == "notice" else "sequence")] += 1

    # 1ο πέρασμα: πληρωτέο == ποσό πληρωμής (πλησιέστερο προηγούμενο ειδοποιητήριο).
    for r in pays:
        best = None
        for i, n, gap in candidates(r):
            if abs((n["payable"] or 0) - (r["_amount"] or 0)) <= tolerance and (best is None or gap < best[1]):
                best = (i, gap)
        if best:
            assign(r, best[0], "notice")
    # 2ο πέρασμα (πάγια εντολή / μερική πληρωμή, π.χ. σταθερά 250 €): το πιο πρόσφατο
    # αχρησιμοποίητο ειδοποιητήριο του ίδιου RF, εκδομένο έως 60 ημέρες πριν την πληρωμή.
    for r in pays:
        if r["split"] != "unmatched":
            continue
        best = None
        for i, n, gap in candidates(r):
            if 0 <= gap <= 60 + 3 and (best is None or gap < best[1]):
                best = (i, gap)
        if best:
            assign(r, best[0], "notice_sequence")
    for r in pays:
        for k in ("_rf", "_amount", "_pd"):
            r.pop(k, None)
        if r["split"] == "unmatched":
            stats["unmatched"] += 1
    return stats


def _period_start_next(period: str) -> Optional[_dt.date]:
    """Μήνας οφειλής MM/YYYY → 1η του επόμενου μήνα (από τότε είναι απαιτητό το ειδοποιητήριο)."""
    pk = _period_key(period)
    if not pk:
        return None
    y, m = pk
    return _dt.date(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)


def apply_notices(rows: List[Dict[str, Any]], notices: List[Dict[str, Any]], tolerance: float = TOLERANCE) -> Dict[str, Any]:
    """Καρτέλα κινήσεων ανά ΜΗΝΑ ΟΦΕΙΛΗΣ (όχι ανά ημερομηνία έκδοσης), χωριστά για e-ΕΦΚΑ/ΤΕΚΑ.

    Κάθε ειδοποιητήριο (μήνας οφειλής P) γίνεται απαιτητό από την 1η του μήνα μετά το P με
    καθαρή εισφορά (μηνιαία + αναδρομικά + χρεώσεις − πιστώσεις − γραμμάτια) και επιβαρύνσεις
    («Επιβαρύνσεις – Πρόσθετα Τέλη»). Κάθε πληρωμή (χρονολογικά) εξοφλεί ΠΡΩΤΑ τις ανοιχτές
    επιβαρύνσεις και ΜΕΤΑ τις εισφορές, με σειρά μήνα οφειλής (παλαιότερος πρώτα).
    Επαληθευμένο σε live δεδομένα: 163174344/2025 → ΕΦΚΑ 1.864,81 & ΤΕΚΑ 452,79 ακριβώς
    όσο οι βεβαιώσεις (η πληρωμή 22/12/2025 εξοφλεί πρώτα τα 1,33/0,32 του 10/2025· οι
    επιβαρύνσεις του 12/2025 πάνε στις πληρωμές του 2026)."""
    good, _keys = [], set()
    for n in notices or []:
        if n.get("payable") is None or not _period_key(n.get("period")):
            continue
        key = (n.get("kind"), n.get("period"), n.get("rf"))
        if key in _keys:
            continue
        _keys.add(key)
        good.append(n)
    rf_kind = {n["rf"]: n["kind"] for n in good if n.get("rf")}
    stats: Dict[str, Any] = {"payments": 0, "matched": 0, "unmatched": 0, "notices": len(good), "method": "ledger_by_period"}
    ledger: Dict[str, Any] = {}
    pays_all = []
    for r in rows:
        if r.get("source") != "efka_payments":
            continue
        stats["payments"] += 1
        raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
        rf = re.sub(r"\s+", "", str(raw.get("Κωδικός Πληρωμής") or r.get("doc") or ""))
        if rf in rf_kind:
            r["kind"] = rf_kind[rf]
        r["fees"], r["notice"], r["split"], r["alloc"] = 0.0, None, "unmatched", []
        amt = r.get("total") if r.get("total") is not None else r.get("net")
        r["total"] = _r2(amt or 0.0)
        # το net μένει όπως το έδωσε ο πίνακας μέχρι να το διαχωρίσει η καρτέλα
        pays_all.append(r)
    for kind in ("EFKA", "TEKA"):
        ns = sorted([n for n in good if n.get("kind") == kind], key=lambda n: _period_key(n["period"]))
        if not ns:
            continue
        periods: List[Dict[str, Any]] = []
        for n in ns:
            due_c = _r2((n.get("monthly") or 0) + (n.get("retro") or 0) + (n.get("charges") or 0)
                        - (n.get("credits") or 0) - (n.get("bar_bills") or 0))
            periods.append({"period": n["period"], "accrual": _period_start_next(n["period"]),
                            "issue_date": n.get("issue_date"), "due_date": n.get("due_date"),
                            "monthly": _r2(n.get("monthly") or 0), "credits": _r2(n.get("credits") or 0),
                            "fees": _r2(n.get("fees") or 0), "payable": n.get("payable"),
                            "contrib_due": max(due_c, 0.0), "contrib_left": max(due_c, 0.0),
                            "fees_left": _r2(n.get("fees") or 0), "paid": [], "notice_id": n.get("notice_id")})
        # Οφειλές πριν από το 1ο ειδοποιητήριο (καθυστερούμενες) — μπαίνουν πρώτες στη σειρά.
        first = ns[0]
        if (first.get("arrears") or 0) > tolerance:
            periods.insert(0, {"period": "πριν " + first["period"], "accrual": periods[0]["accrual"],
                               "monthly": 0.0, "credits": 0.0, "fees": 0.0, "payable": None,
                               "contrib_due": _r2(first["arrears"]), "contrib_left": _r2(first["arrears"]),
                               "fees_left": 0.0, "paid": [], "arrears_seed": True})
        start = periods[0]["accrual"]
        pays = sorted([r for r in pays_all if r.get("kind") == kind and _date_obj(r.get("date"))],
                      key=lambda r: _date_obj(r["date"]))
        for r in pays:
            pd = _date_obj(r["date"])
            if pd < start:
                r["split"] = "before_notices"
                continue
            open_ = [p for p in periods if p["accrual"] and p["accrual"] <= pd]
            left = r["total"]
            fee_part = 0.0
            for p in open_:                      # 1. επιβαρύνσεις, παλαιότερος μήνας πρώτα
                if left <= 0.004:
                    break
                x = min(p["fees_left"], left)
                if x > 0.004:
                    p["fees_left"] = _r2(p["fees_left"] - x)
                    left = _r2(left - x)
                    fee_part = _r2(fee_part + x)
                    r["alloc"].append({"period": p["period"], "type": "fees", "amount": _r2(x)})
                    p["paid"].append({"date": r["date"], "type": "fees", "amount": _r2(x)})
            for p in open_:                      # 2. καθαρές εισφορές, παλαιότερος μήνας πρώτα
                if left <= 0.004:
                    break
                x = min(p["contrib_left"], left)
                if x > 0.004:
                    p["contrib_left"] = _r2(p["contrib_left"] - x)
                    left = _r2(left - x)
                    r["alloc"].append({"period": p["period"], "type": "contrib", "amount": _r2(x)})
                    p["paid"].append({"date": r["date"], "type": "contrib", "amount": _r2(x)})
            if left > 0.004:
                r["alloc"].append({"period": None, "type": "excess", "amount": _r2(left)})
            r["fees"] = fee_part
            r["net"] = _r2(r["total"] - fee_part)
            r["split"] = "ledger"
            cp = [a["period"] for a in r["alloc"] if a["type"] == "contrib"]
            r["period"] = ", ".join(dict.fromkeys(cp)) if cp else None
            main = next((p for p in periods if cp and p["period"] == cp[0]), None)
            if main:
                r["notice"] = {k: main.get(k) for k in ("period", "issue_date", "due_date", "notice_id", "monthly",
                                                        "credits", "fees", "payable")}
            stats["matched"] += 1
        for p in periods:
            p["accrual"] = p["accrual"].strftime("%d/%m/%Y") if p.get("accrual") else None
        ledger[kind] = periods
    stats["unmatched"] = sum(1 for r in pays_all if r["split"] != "ledger")
    stats["ledger"] = ledger
    return stats


def period_view(year: int, periods: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Ανάλυση ανά μήνα οφειλής του έτους + γέφυρα «ταμειακή βάση ↔ μήνες οφειλής» για τα άκρα."""
    def yr(d):
        return int(d[-4:]) if d else None
    rows = []
    A = B = C = fees_y = 0.0
    for p in periods:
        pk = _period_key(p["period"])
        in_y = bool(pk and pk[0] == year)
        paid_y = _r2(sum(x["amount"] for x in p["paid"] if x["type"] == "contrib" and yr(x["date"]) == year))
        paid_after = _r2(sum(x["amount"] for x in p["paid"] if x["type"] == "contrib" and (yr(x["date"]) or 0) > year))
        fees_paid_y = _r2(sum(x["amount"] for x in p["paid"] if x["type"] == "fees" and yr(x["date"]) == year))
        fees_y += fees_paid_y
        if in_y:
            A += p["contrib_due"]
            C += _r2(p["contrib_due"] - paid_y)
            rows.append(dict(p, paid_in_year=paid_y, paid_after=paid_after, fees_paid_in_year=fees_paid_y,
                             outstanding=_r2(p["contrib_left"])))
        elif paid_y > 0.004 or fees_paid_y > 0.004:
            B += paid_y
            rows.append(dict(p, paid_in_year=paid_y, paid_after=paid_after, fees_paid_in_year=fees_paid_y,
                             outstanding=_r2(p["contrib_left"]), boundary="prev"))
    return {"rows": rows, "contrib_periods_year": _r2(A), "paid_prev_periods": _r2(B),
            "not_paid_in_year": _r2(C), "cash_net_contrib": _r2(A + B - C), "fees_paid_in_year": _r2(fees_y)}


# ------------------------------------------------------------ ΚΕΑΟ (HTTP)
def classify_registry(forea: str) -> str:
    """efka_mh | teka_mh | employer | other_non_employee."""
    name = re.sub(r"^Ληξιπρόθεσμο\s*-\s*", "", (forea or "").strip(), flags=re.I)
    n = _norm(name)
    if name == EFKA_MH_MISTHWTWN_FOREA:
        return "efka_mh"
    if "τεκα" in n:
        return "employer" if ("μισθωτοι" in n and "μη μισθωτοι" not in n) else "teka_mh"
    if "ικα" in n.split() or "οπσ-ικα" in n or "ιδρυμα κοινωνικων ασφαλισεων" in n or "εργοδοτ" in n:
        return "employer"
    return "other_non_employee"


def _keao_picker(http: _Http, keao_url: str) -> Dict[str, Any]:
    land = http.follow("GET", keao_url)["text"]
    fm = re.search(r'<form\b[^>]*id="amoForm"[^>]*>', land, re.I)
    if not fm:
        raise RuntimeError("amoForm_not_found")
    am = re.search(r'action="([^"]*)"', fm.group(0), re.I)
    post_url = urljoin(SVC, cert._decode_html(am.group(1) if am else "").lstrip("/"))
    vs = cert._view_state(land)
    src = "amoForm:dt-table"
    xml = http.ajax(post_url, {
        "jakarta.faces.partial.ajax": "true", "jakarta.faces.source": src,
        "jakarta.faces.partial.execute": src, "jakarta.faces.partial.render": src, src: src,
        f"{src}_pagination": "true", f"{src}_first": "0", f"{src}_rows": "20",
        f"{src}_skipChildren": "true", f"{src}_encodeFeature": "true",
        "amoForm": "amoForm", "jakarta.faces.ViewState": vs,
    })["text"]
    frag = "".join(re.findall(r"<update[^>]*><!\[CDATA\[([\s\S]*?)\]\]></update>", xml, re.I)) or land
    total = 0
    tm = re.search(r'id="amoForm:dt-table_s"[\s\S]{0,4000}?rowCount:\s*(\d+)', land, re.I) or         re.search(r'id:"amoForm:dt-table",paginator:\{[^}]*rowCount:\s*(\d+)', land)
    if tm:
        total = int(tm.group(1))
    first = 20
    while first < total and first < 400:
        x2 = http.ajax(post_url, {
            "jakarta.faces.partial.ajax": "true", "jakarta.faces.source": src,
            "jakarta.faces.partial.execute": src, "jakarta.faces.partial.render": src, src: src,
            f"{src}_pagination": "true", f"{src}_first": str(first), f"{src}_rows": "20",
            f"{src}_skipChildren": "true", f"{src}_encodeFeature": "true",
            "amoForm": "amoForm", "jakarta.faces.ViewState": vs,
        })["text"]
        frag += "".join(re.findall(r"<update[^>]*><!\[CDATA\[([\s\S]*?)\]\]></update>", x2, re.I))
        first += 20
    rows = []
    for tr in re.finditer(r"<tr\b([^>]*)>([\s\S]*?)</tr>", frag, re.I):
        if not re.search(r'data-ri="\d+"', tr.group(1)):
            continue
        tds = re.findall(r"<td\b[^>]*>([\s\S]*?)</td>", tr.group(2), re.I)
        if len(tds) < 5:
            continue
        bm = re.search(r'<button\b[^>]*id="([^"]*)"', tr.group(2), re.I)
        rows.append({"amo": cert._strip_tags(tds[0]), "forea": cert._strip_tags(tds[1]),
                     "am": cert._strip_tags(tds[2]), "epwnymia": cert._strip_tags(tds[3]),
                     "btn": bm.group(1) if bm else ""})
    return {"rows": rows, "vs": vs, "post_url": post_url}


def _cdata(xml: str, id_: str) -> str:
    m = re.search(r'<update id="' + re.escape(id_) + r'"><!\[CDATA\[([\s\S]*?)\]\]></update>', xml, re.I)
    return m.group(1) if m else ""


def _labeled(html: str, label: str) -> str:
    cells = [cert._strip_tags(c) for c in re.findall(
        r'<div\b[^>]*class="[^"]*ui-panelgrid-cell[^"]*"[^>]*>([\s\S]*?)</div>', html, re.I)]
    for i in range(len(cells) - 1):
        if cells[i] == label:
            return cells[i + 1]
    return ""


def _keao_registry_credits(http: _Http, picker: Dict[str, Any], reg: Dict[str, Any], diag: _Diag) -> Dict[str, Any]:
    vs, post_url = picker["vs"], picker["post_url"]
    tag = f"amo{reg['amo']}"
    en = http.ajax(post_url, {
        "jakarta.faces.partial.ajax": "true", "jakarta.faces.source": reg["btn"],
        "jakarta.faces.partial.execute": "@all",
        "jakarta.faces.partial.render": "amoForm sidebarUserInfo MenuForm mainContentPanel",
        reg["btn"]: reg["btn"], "amoForm": "amoForm", "jakarta.faces.ViewState": vs,
    })["text"]
    menu = _cdata(en, "MenuForm")
    if not menu:
        diag.dump(f"keao_{tag}_enable.xml", en)
        return {"status": "menu_form_not_found", "rows": []}
    src = ""
    for m in re.finditer(r'<a\b([^>]*role="menuitem"[^>]*)>([\s\S]*?)</a>', menu, re.I):
        if cert._strip_tags(m.group(2)) == "Κινήσεις Οφειλέτη":
            oc = cert._decode_html((re.search(r'onclick="([^"]*)"', m.group(1)) or [None, ""])[1] or "")
            sm = re.search(r'PrimeFaces\.ab\(\{s:"([^"]+)"', oc)
            src = sm.group(1) if sm else ""
            break
    if not src:
        diag.dump(f"keao_{tag}_menu.html", menu)
        return {"status": "menu_item_not_found", "rows": []}
    trans = http.ajax(post_url, {
        "jakarta.faces.partial.ajax": "true", "jakarta.faces.source": src,
        "jakarta.faces.partial.execute": "@all", "jakarta.faces.partial.render": "mainContentPanel MenuForm",
        src: src, "MenuForm": "MenuForm", "jakarta.faces.ViewState": vs,
    })["text"]
    diag.dump(f"keao_{tag}_trans.xml", trans)
    if "error.xhtml" in trans and "<update" not in trans:
        return {"status": "error_page:trans", "rows": []}
    # Ίδια σειρά με το keao-debts.js (== hyperserver): πρώτα ο πίνακας Χρεώσεων
    # (dt-table-1, tab0), ΜΕΤΑ tabChange στις Πιστώσεις — αλλιώς ο server δίνει error.xhtml.
    src1 = "debtorTransForm:mainTabView:dt-table-1"
    deb = http.ajax(post_url, {
        "jakarta.faces.partial.ajax": "true", "jakarta.faces.source": src1,
        "jakarta.faces.partial.execute": src1, "jakarta.faces.partial.render": src1, src1: src1,
        f"{src1}_pagination": "true", f"{src1}_first": "0", f"{src1}_rows": "20",
        f"{src1}_skipChildren": "true", f"{src1}_encodeFeature": "true",
        "debtorTransForm": "debtorTransForm", "debtorTransForm:mainTabView_activeIndex": "0",
        "jakarta.faces.ViewState": vs,
    })["text"]
    diag.dump(f"keao_{tag}_debits_p1.xml", deb)
    tabx = http.ajax(post_url, {
        "jakarta.faces.partial.ajax": "true", "jakarta.faces.source": "debtorTransForm:mainTabView",
        "jakarta.faces.partial.render": "debtorTransForm:mainTabView",
        "jakarta.faces.partial.execute": "debtorTransForm:mainTabView",
        "jakarta.faces.behavior.event": "tabChange", "jakarta.faces.partial.event": "tabChange",
        "debtorTransForm:mainTabView_contentLoad": "true",
        "debtorTransForm:mainTabView_newTab": "debtorTransForm:mainTabView:tab2",
        "debtorTransForm:mainTabView_tabindex": "1", "debtorTransForm": "debtorTransForm",
        "debtorTransForm:mainTabView_activeIndex": "1", "jakarta.faces.ViewState": vs,
    })["text"]
    diag.dump(f"keao_{tag}_credits_tab.xml", tabx)
    credtab = _cdata(tabx, "debtorTransForm:mainTabView")
    header = {
        "total": to_amount(_labeled(credtab, "Συνολικό Ποσό:")),
        "main_contrib": to_amount(_labeled(credtab, "Κύρια Εισφορά:")),
        "extra_fees": to_amount(_labeled(credtab, "Πρόσθετα Τέλη:")),
        "surcharges": to_amount(_labeled(credtab, "Προσαυξήσεις:")),
    }
    rows: List[Dict[str, Any]] = []
    src2 = "debtorTransForm:mainTabView:dt-table-2"
    rpp, step, pages = 20, 0, 1
    while step < pages and step < 200:
        xml = http.ajax(post_url, {
            "jakarta.faces.partial.ajax": "true", "jakarta.faces.source": src2,
            "jakarta.faces.partial.execute": src2, "jakarta.faces.partial.render": src2, src2: src2,
            f"{src2}_pagination": "true", f"{src2}_first": str(step * rpp), f"{src2}_rows": str(rpp),
            f"{src2}_skipChildren": "true", f"{src2}_encodeFeature": "true",
            "debtorTransForm": "debtorTransForm", "debtorTransForm:mainTabView_activeIndex": "1",
            "jakarta.faces.ViewState": vs,
        })["text"]
        if step == 0:
            diag.dump(f"keao_{tag}_credits_p1.xml", xml)
            if "error.xhtml" in xml and "data-ri" not in xml:
                return {"status": "error_page:credits", "rows": [], "header_totals": header}
            tm = re.search(r'totalRecords(?:&#34;|&quot;|")\s*:\s*(\d+)', xml, re.I)
            if tm:
                pages = max(1, math.ceil(int(tm.group(1)) / rpp))
        frag = "".join(re.findall(r"<update[^>]*><!\[CDATA\[([\s\S]*?)\]\]></update>", xml, re.I))
        for tr in re.finditer(r"<tr\b([^>]*)>([\s\S]*?)</tr>", frag, re.I):
            if not re.search(r'data-ri="\d+"', tr.group(1)):
                continue
            c = [cert._strip_tags(x) for x in re.findall(r"<td\b[^>]*>([\s\S]*?)</td>", tr.group(2), re.I)]
            if len(c) < 8:
                continue
            rows.append(keao_credit_row(c[1], c[2], c[3], c[4], c[5], c[6], c[7], branch=c[0]))
        step += 1
    return {"status": "ok", "rows": rows, "header_totals": header}


def keao_credit_row(date, doc, code, total, main, extra, sur, branch="") -> Dict[str, Any]:
    return {
        "branch": branch, "date": date, "doc": doc, "kod_kin": code,
        "total": _r2(to_amount(total) or 0), "main_contrib": _r2(to_amount(main) or 0),
        "extra_fees": _r2(to_amount(extra) or 0), "surcharges": _r2(to_amount(sur) or 0),
    }


def fetch_keao(http: _Http, keao_url: str, diag: _Diag) -> Dict[str, Any]:
    res: Dict[str, Any] = {"ok": False, "source": "http", "registries": [], "error": None}
    picker = _keao_picker(http, keao_url)
    if not picker["rows"]:
        res["ok"] = True
        res["error"] = "Δεν βρέθηκαν μητρώα στο ΚΕΑΟ."
        return res
    regs = list(picker["rows"])
    for i, reg in enumerate(regs):
        progress(50 + 15 * i / max(1, len(regs)), f"ΚΕΑΟ — μητρώο {i + 1}/{len(regs)} (ΑΜΟ {reg.get('amo')})")
        kind = classify_registry(reg["forea"])
        rec = {k: reg[k] for k in ("amo", "forea", "am", "epwnymia")}
        rec["class"] = kind
        if kind == "employer":
            rec.update({"status": "skipped_employer", "rows": []})
            res["registries"].append(rec)
            continue
        try:
            if i > 0:
                picker = _keao_picker(http, keao_url)  # φρέσκο ViewState ανά μητρώο
                reg = next((r for r in picker["rows"] if r["amo"] == reg["amo"] and r["forea"] == reg["forea"]), reg)
            rec.update(_keao_registry_credits(http, picker, reg, diag))
        except Exception as exc:
            logging.exception("KEAO registry %s", reg.get("amo"))
            rec.update({"status": f"error: {exc}", "rows": []})
        res["registries"].append(rec)
    # ok μόνο αν ΟΛΑ τα μη-εργοδοτικά μητρώα διαβάστηκαν — αλλιώς τρέχει το fallback.
    res["ok"] = all(r.get("status") in ("ok", "skipped_employer") for r in res["registries"])
    bad = [f"ΑΜΟ {r.get('amo')}: {r.get('status')}" for r in res["registries"]
           if r.get("status") not in ("ok", "skipped_employer")]
    if bad:
        res["error"] = "; ".join(bad)
    return res


def fetch_keao_fallback(username: str, password: str, afm: str, amka: str, year: int,
                        diag: _Diag) -> Dict[str, Any]:
    """Fallback: το υπάρχον keao-mistoton.py (Playwright) — μόνο μητρώο ΕΦΚΑ Μη Μισθωτών."""
    res: Dict[str, Any] = {"ok": False, "source": "keao-mistoton.py", "registries": [], "error": None}
    with tempfile.TemporaryDirectory() as tmp:
        out_json = Path(tmp) / "keao.json"
        cmd = [sys.executable, str(_HERE / "keao-mistoton.py"), "--username", username, "--password", password,
               "--afm", afm, "--amka", amka, "--date-from", f"01/01/{year}", "--year", str(year),
               "--output-dir", str(Path(tmp) / "out"), "--output-json", str(out_json), "--headless"]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
            diag.dump("keao_fallback_stderr.txt", (p.stderr or "")[-20000:])
            data = json.loads(out_json.read_text(encoding="utf-8")) if out_json.exists() else {}
        except Exception as exc:
            res["error"] = f"keao-mistoton.py: {exc}"
            return res
    for r in data.get("registries") or []:
        rows = [keao_credit_row(x.get("date", ""), x.get("doc", ""), x.get("kod_kin", ""), x.get("total"),
                                x.get("main_contrib"), x.get("extra_fees"), x.get("surcharges"))
                for x in r.get("rows") or []]
        res["registries"].append({"amo": r.get("amo"), "forea": r.get("forea"), "epwnymia": r.get("epwnymia"),
                                  "class": classify_registry(r.get("forea", "")), "status": r.get("status"),
                                  "rows": rows})
    res["ok"] = str(data.get("status", "")).startswith("ok")
    if not res["ok"]:
        res["error"] = f"keao-mistoton.py status: {data.get('status')}"
    return res


# ------------------------------------------------------------ reconcile
def _sum(rows: List[Dict[str, Any]], key: str) -> float:
    return _r2(sum(float(r.get(key) or 0.0) for r in rows))


KEAO_COMPONENTS = {
    # κατηγορία μητρώου → (κλειδί, ετικέτα, kind)
    "efka_mh": ("keao_efka_mh", "Καταβολές στο ΚΕΑΟ — μητρώο e-ΕΦΚΑ μη μισθωτών", "EFKA"),
    "other_non_employee": ("keao_other", "Καταβολές στο ΚΕΑΟ — λοιπά μητρώα μη μισθωτού (π.χ. ΟΑΕΕ)", "EFKA"),
    "teka_mh": ("keao_teka", "Καταβολές στο ΚΕΑΟ — μητρώο ΤΕΚΑ", "TEKA"),
}
_MAX_COMBO_COMPONENTS = 7


def _components(year: int, kind: str, payments: Dict[str, Any], keao: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Κάθε πηγή καταβολών ΧΩΡΙΣΤΑ (δεν αναμειγνύονται καταβολές e-ΕΦΚΑ με καταβολές ΚΕΑΟ)."""
    comps: List[Dict[str, Any]] = []
    pay_ok = bool((payments or {}).get("ok")) and bool((payments or {}).get("tables"))
    rows_all = [r for r in (payments or {}).get("rows") or [] if r.get("kind", "EFKA") == kind]
    rows_y = [r for r in rows_all if r.get("year") == year]
    for cat, (label, counts) in PAYMENT_CATEGORIES.items():
        rows = [r for r in rows_y if r.get("source", "efka_payments") == cat]
        if cat == "credit_balances":
            # Εναλλακτική βάση έτους: «Ημερομηνία Προέλευσης» (δεν αθροίζεται ΜΑΖΙ με την κύρια).
            alt = [dict(r, date=r.get("date_alt"), year=r.get("year_alt")) for r in rows_all
                   if r.get("source") == cat and r.get("year_alt") == year]
            if alt and sorted(x["net"] for x in alt) != sorted(x["net"] for x in rows):
                comps_alt = {"key": "credit_balances_origin", "label": "Πιστωτικά υπόλοιπα e-ΕΦΚΑ (κατά ημ. προέλευσης)",
                             "origin": "e-ΕΦΚΑ", "counts": True, "available": pay_ok, "alt_of": "credit_balances",
                             "net": _sum(alt, "net"), "extra_fees": 0.0, "surcharges": 0.0, "unmatched": 0,
                             "by_sequence": 0, "total": _sum(alt, "total"), "count": len(alt), "rows": alt,
                             "registries": []}
            else:
                comps_alt = None
        if not rows and cat != "efka_payments":
            if cat == "credit_balances" and comps_alt:
                comps.append(comps_alt)
            continue
        comps.append({
            "key": cat, "label": label, "origin": "e-ΕΦΚΑ", "counts": counts, "available": pay_ok,
            "net": _sum(rows, "net"), "extra_fees": _sum(rows, "fees"), "surcharges": _sum(rows, "surcharge"),
            "unmatched": sum(1 for r in rows if r.get("split") == "unmatched"),
            "by_sequence": sum(1 for r in rows if r.get("split") == "notice_sequence"),
            "total": _sum(rows, "total"), "count": len(rows), "rows": rows, "registries": [],
        })
        if cat == "credit_balances" and comps_alt:
            comps.append(comps_alt)
    keao_ok = bool((keao or {}).get("ok"))
    by_key: Dict[str, Dict[str, Any]] = {}
    for reg in (keao or {}).get("registries") or []:
        spec = KEAO_COMPONENTS.get(reg.get("class"))
        if not spec or spec[2] != kind:
            continue
        key, label, _k = spec
        c = by_key.get(key)
        if not c:
            c = by_key[key] = {"key": key, "label": label, "origin": "ΚΕΑΟ", "counts": True, "available": keao_ok,
                               "rows": [], "registries": []}
        yr = [dict(r, amo=reg.get("amo"), registry=reg.get("forea")) for r in reg.get("rows") or []
              if _year_of(r.get("date", "")) == year]
        c["rows"].extend(yr)
        c["registries"].append({"amo": reg.get("amo"), "forea": reg.get("forea"), "status": reg.get("status")})
    for c in by_key.values():
        c.update({"net": _sum(c["rows"], "main_contrib"), "extra_fees": _sum(c["rows"], "extra_fees"),
                  "surcharges": _sum(c["rows"], "surcharges"), "total": _sum(c["rows"], "total"),
                  "count": len(c["rows"])})
        comps.append(c)
    return comps


def reconcile(year: int, certificates: Dict[str, Any], payments: Dict[str, Any],
              keao: Dict[str, Any], tolerance: float = TOLERANCE,
              ledger_kinds: Optional[set] = None) -> Dict[str, Any]:
    """Καθαρό αλγοριθμικό κομμάτι (χωρίς δίκτυο) — δοκιμάζεται offline.

    Η βεβαίωση περιέχει μόνο καθαρά ποσά. Δοκιμάζονται όλοι οι συνδυασμοί των
    καθαρών ποσών των πηγών (κάθε πηγή χωριστά) και κρατιέται αυτός που
    συμφωνεί· προτεραιότητα στον πληρέστερο συνδυασμό."""
    from itertools import combinations
    out: Dict[str, Any] = {"year": year, "tolerance": tolerance, "kinds": {}, "needs_report": False}
    for kind in ("EFKA", "TEKA"):
        c = (certificates or {}).get(kind) or {}
        cert_amt = c.get("certificate_amount")
        comps = _components(year, kind, payments, keao)
        usable = [x for x in comps if x["counts"] and x["available"] and x["count"] > 0][:_MAX_COMBO_COMPONENTS]
        hyps: List[Dict[str, Any]] = []
        for n in range(len(usable), 0, -1):
            for combo in combinations(usable, n):
                keys = {x["key"] for x in combo}
                if any(x.get("alt_of") in keys for x in combo):
                    continue        # ίδια πηγή με δύο βάσεις ημερομηνίας — ποτέ μαζί
                val = _r2(sum(x["net"] for x in combo))
                hyps.append({"keys": [x["key"] for x in combo],
                             "label": " + ".join(x["label"] for x in combo), "value": val})
        matched = closest = None
        if cert_amt is not None:
            for h in hyps:
                h["diff"] = _r2(cert_amt - h["value"])
                if matched is None and abs(h["diff"]) <= tolerance:
                    matched = h
                if closest is None or abs(h["diff"]) < abs(closest["diff"]) - 0.0001:
                    closest = h
        # Χωρίς βεβαίωση/συμφωνία: βάση = όλες οι πηγές που μετρούν.
        use = matched or (hyps[0] if hyps and cert_amt is None else closest)
        used = set(use["keys"]) if use else set()
        for x in comps:
            x["included"] = x["key"] in used
        inc = [x for x in comps if x["included"]]
        net_used = _r2(sum(x["net"] for x in inc))
        fees = _r2(sum(x["extra_fees"] for x in inc))
        incr = _r2(sum(x["surcharges"] for x in inc))
        surcharges_total = _r2(fees + incr)
        gross_paid = _r2(sum(x["total"] for x in inc))
        any_rows = any(x["count"] for x in comps)
        if cert_amt is None and not any_rows:
            status = "no_data"
        elif cert_amt is None:
            status = "no_certificate"
        elif matched:
            status = "match"
        elif not hyps:
            status = "no_sources"
        else:
            status = "difference"
        diff = _r2(cert_amt - net_used) if cert_amt is not None else None
        hyp_view = sorted(hyps, key=lambda h: abs(h.get("diff", 0.0)))[:6] if cert_amt is not None else hyps[:6]
        kind_res = {
            "certificate_amount": cert_amt,
            "certificate_source": c.get("certificate_amount_source"),
            "certificate_pdf": c.get("pdf"),
            "status": status,
            "matched_keys": matched["keys"] if matched else None,
            "matched_label": matched["label"] if matched else None,
            "hypotheses_tested": len(hyps),
            "hypotheses": hyp_view,
            "components": comps,
            "net_amount": net_used,
            "difference": diff,
            "extra_fees": fees,
            "increments": incr,
            "surcharges_total": surcharges_total,
            "gross_paid": gross_paid,
        }
        if ledger_kinds is None:    # κλήση χωρίς finish(): κρίνεται από το αν έγινε διαχωρισμός στις γραμμές
            kind_res["ledger_used"] = any(r.get("split") in ("ledger", "notice", "notice_sequence")
                                          for x in comps for r in x["rows"])
        else:
            kind_res["ledger_used"] = kind in ledger_kinds
        kind_res["explanation"] = explain(kind, year, kind_res)
        kind_res["needs_report"] = status in ("difference", "no_sources") or (
            status == "match" and surcharges_total > tolerance)
        out["kinds"][kind] = kind_res
        if kind_res["needs_report"]:
            out["needs_report"] = True
    return out


def explain(kind: str, year: int, k: Dict[str, Any]) -> List[str]:
    name = "e-ΕΦΚΑ" if kind == "EFKA" else "ΤΕΚΑ"
    C = k["certificate_amount"]
    inc = [x for x in k["components"] if x.get("included")]
    if k["status"] == "no_data":
        return [f"Δεν βρέθηκε βεβαίωση {name} ούτε καταβολές για το {year}."]
    if k["status"] == "no_certificate":
        return [f"Δεν βρέθηκε φορολογική βεβαίωση {name} για το {year}· καθαρές καταβολές από τις πηγές: "
                f"{fmt_eur(k['net_amount'])}."]
    lines = [f"Η φορολογική βεβαίωση {name} {year} γράφει {fmt_eur(C)} και περιλαμβάνει μόνο καθαρά ποσά "
             f"εισφορών (χωρίς πρόσθετα τέλη και προσαυξήσεις)."]
    if k["status"] == "match":
        lines.append(f"Το ποσό αυτό συμφωνεί με το άθροισμα των καθαρών ποσών: "
                     + "; ".join(f"{x['label']} {fmt_eur(x['net'])}" for x in inc)
                     + f" = {fmt_eur(k['net_amount'])}.")
    elif k["status"] == "difference":
        lines.append(f"Κανένας συνδυασμός των πηγών δεν συμφωνεί ακριβώς ({k['hypotheses_tested']} έλεγχοι). "
                     f"Πλησιέστερος: " + "; ".join(f"{x['label']} {fmt_eur(x['net'])}" for x in inc)
                     + f" = {fmt_eur(k['net_amount'])}, διαφορά {fmt_eur(k['difference'])} (βεβαίωση − καθαρά ποσά).")
    else:
        lines.append("Δεν ήταν διαθέσιμη καμία πηγή καταβολών για σύγκριση.")
    pc = next((x for x in k["components"] if x["key"] == "efka_payments"), None)
    if pc and pc["count"] and not k.get("ledger_used") and k["status"] == "match":
        lines.append("Η βεβαίωση συμφωνεί απευθείας με τις καταβολές· δεν χρειάστηκε διαχωρισμός μέσω ειδοποιητηρίων.")
    alt = next((x for x in k["components"] if x.get("alt_of") and x.get("included")), None)
    if alt:
        lines.append(f"Για τα πιστωτικά υπόλοιπα ταίριαξε η «Ημερομηνία Προέλευσης» ως βάση έτους (όχι η «Ημερομηνία Αναγγελίας»).")
    if pc and pc["count"] and k.get("ledger_used"):
        um = pc.get("unmatched") or 0
        lines.append(f"Οι καταβολές στον {name} διαχωρίστηκαν σε καθαρό και επιβαρύνσεις με καρτέλα ανά μήνα "
                     f"οφειλής από τα μηνιαία ειδοποιητήρια: κάθε πληρωμή εξοφλεί πρώτα τις ανοιχτές επιβαρύνσεις και "
                     f"μετά τις εισφορές του παλαιότερου μήνα ({pc['count'] - um}/{pc['count']} πληρωμές"
                     + (f"· {um} χωρίς ειδοποιητήριο — σε αυτές δεν διαχωρίστηκαν επιβαρύνσεις" if um else "") + ").")
    left = [x for x in k["components"] if not x.get("included") and x["count"] and x["counts"] and not x.get("alt_of")]
    if left and k["status"] == "match":
        lines.append("Δεν περιλαμβάνονται στη βεβαίωση: " + "; ".join(
            f"{x['label']} {fmt_eur(x['net'])}" for x in left) + ".")
    if k["surcharges_total"] > TOLERANCE:
        parts = []
        if k["extra_fees"]:
            parts.append(f"πρόσθετα τέλη {fmt_eur(k['extra_fees'])}")
        if k["increments"]:
            parts.append(f"προσαυξήσεις {fmt_eur(k['increments'])}")
        lines.append(f"Επιπλέον καταβλήθηκαν επιβαρύνσεις {fmt_eur(k['surcharges_total'])} ("
                     + ", ".join(parts) + f") που ΔΕΝ περιλαμβάνονται στη βεβαίωση. Συνολικά πληρώθηκαν "
                     f"στο {year} {fmt_eur(k['gross_paid'])} = καθαρό {fmt_eur(k['net_amount'])} + "
                     f"επιβαρύνσεις {fmt_eur(k['surcharges_total'])}.")
    return lines


# ------------------------------------------------------------ report (HTML → PDF)
_CSS = """
@page { size: A4; margin: 14mm 12mm 16mm 12mm; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", "DejaVu Sans", Arial, sans-serif; font-size: 10.5px; color: #1d2733; margin: 0; }
h1 { font-size: 16px; margin: 0 0 2px; color: #0b3d63; }
h2 { font-size: 12.5px; margin: 16px 0 6px; color: #0b3d63; border-bottom: 1.5px solid #0b3d63; padding-bottom: 3px; }
.sub { color: #5a6876; margin-bottom: 10px; }
.meta { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px 14px; background: #f3f6f9;
        border: 1px solid #dbe3ea; border-radius: 6px; padding: 8px 10px; }
.meta b { display: block; font-size: 9px; color: #5a6876; font-weight: 600; text-transform: uppercase; }
.cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 8px 0; }
.card { border: 1px solid #dbe3ea; border-radius: 6px; padding: 7px 9px; }
.card .l { font-size: 9px; color: #5a6876; } .card .v { font-size: 14px; font-weight: 700; margin-top: 2px; }
.card.ok { border-color: #2e8b57; background: #f0faf4; } .card.warn { border-color: #d08a00; background: #fff8e8; }
.card.bad { border-color: #c0392b; background: #fdf1ef; }
.status { display: inline-block; padding: 2px 8px; border-radius: 10px; font-weight: 700; font-size: 10px; }
.status.match { background: #dff3e7; color: #1e6b40; } .status.difference, .status.no_sources { background: #fbe3df; color: #9b2a1d; }
.status.no_certificate, .status.no_data { background: #eceff2; color: #4a5560; }
.expl { background: #f8fafc; border-left: 3px solid #0b3d63; padding: 6px 10px; margin: 6px 0; }
.expl p { margin: 3px 0; }
table { width: 100%; border-collapse: collapse; margin-top: 4px; }
th { background: #0b3d63; color: #fff; font-weight: 600; text-align: left; padding: 4px 5px; font-size: 9.5px; }
td { padding: 3px 5px; border-bottom: 1px solid #e3e8ed; vertical-align: top; }
td.n, th.n { text-align: right; white-space: nowrap; }
tr.tot td { font-weight: 700; background: #eef3f7; border-top: 1.5px solid #0b3d63; }
tr.hl td { background: #f0faf4; }
.recon td:first-child { width: 62%; }
.muted { color: #6b7783; } .small { font-size: 9px; }
.foot { margin-top: 14px; color: #6b7783; font-size: 8.5px; border-top: 1px solid #dbe3ea; padding-top: 6px; }
.cards, .expl, tr, .meta { break-inside: avoid; page-break-inside: avoid; }
h2 { break-after: avoid; page-break-after: avoid; }
tr.gap td { border: none; height: 10px; padding: 0; background: transparent; }
tr.diff td { font-weight: 700; }
"""

_STATUS_LABEL = {"match": "ΣΥΜΦΩΝΙΑ", "difference": "ΔΙΑΦΟΡΑ", "no_sources": "ΧΩΡΙΣ ΠΗΓΕΣ",
                 "no_certificate": "ΧΩΡΙΣ ΒΕΒΑΙΩΣΗ", "no_data": "ΧΩΡΙΣ ΣΤΟΙΧΕΙΑ"}


def _e(s: Any) -> str:
    return _html.escape("" if s is None else str(s))


def _date_key(d: str) -> Tuple[int, int, int]:
    m = _DATE_RE.search(d or "")
    return (int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else (9999, 99, 99)


def build_report_html(recon: Dict[str, Any], holder: Dict[str, Any], afm: str, amka: str) -> str:
    year = recon["year"]
    now = _dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    name = " ".join(x for x in (holder.get("Επίθετο"), holder.get("Όνομα")) if x) or "—"
    parts = [f"<!doctype html><html lang='el'><head><meta charset='utf-8'><title>Καρτέλα αντιπαραβολής ΕΦΚΑ/ΤΕΚΑ {year}</title>"
             f"<style>{_CSS}</style></head><body>",
             f"<h1>Καρτέλα Αντιπαραβολής Εισφορών e-ΕΦΚΑ / ΤΕΚΑ</h1>"
             f"<div class='sub'>Φορολογικό έτος {year} · Φορολογική βεβαίωση έναντι καταβολών στον e-ΕΦΚΑ και στο ΚΕΑΟ</div>",
             "<div class='meta'>"
             f"<div><b>Ασφαλισμένος</b>{_e(name)}</div><div><b>ΑΦΜ</b>{_e(afm)}</div>"
             f"<div><b>ΑΜΚΑ</b>{_e(amka)}</div><div><b>Ημ/νία καρτέλας</b>{now}</div></div>"]
    for kind in ("EFKA", "TEKA"):
        k = recon["kinds"].get(kind)
        if not k or k["status"] == "no_data":
            continue
        kname = "e-ΕΦΚΑ" if kind == "EFKA" else "ΤΕΚΑ"
        st = k["status"]
        C = k["certificate_amount"]
        parts.append(f"<div class='sec'><h2>{kname} — <span class='status {st}'>{_STATUS_LABEL.get(st, st)}</span></h2>")
        net_cls = "ok" if st == "match" else ("bad" if st in ("difference", "no_sources") else "")
        parts.append("<div class='cards'>"
                     f"<div class='card'><div class='l'>Ποσό φορολογικής βεβαίωσης</div><div class='v'>{fmt_eur(C)}</div></div>"
                     f"<div class='card {net_cls}'><div class='l'>Καθαρό ποσό που {'συμφωνεί' if st == 'match' else 'βρέθηκε'}</div><div class='v'>{fmt_eur(k['net_amount'])}</div></div>"
                     f"<div class='card {'warn' if k['surcharges_total'] > TOLERANCE else ''}'><div class='l'>Επιβαρύνσεις (τέλη + προσαυξήσεις)</div><div class='v'>{fmt_eur(k['surcharges_total'])}</div></div>"
                     f"<div class='card'><div class='l'>Συνολικά καταβληθέντα {year}</div><div class='v'>{fmt_eur(k['gross_paid'])}</div></div>"
                     "</div>")
        parts.append("<div class='expl'>" + "".join(f"<p>{_e(x)}</p>" for x in k["explanation"]) + "</div>")
        # Συμφωνία ανά πηγή — κάθε πηγή σε δική της γραμμή
        parts.append("<table class='recon'><tr><th>Πηγή καταβολών</th><th class='n'>Στη βεβαίωση</th><th class='n'>Καθαρό</th>"
                     "<th class='n'>Πρόσθ. τέλη</th><th class='n'>Προσαυξ.</th><th class='n'>Σύνολο</th></tr>")
        for x in k["components"]:
            if x.get("alt_of") and not x.get("included"):
                continue
            if not x["available"]:
                parts.append(f"<tr><td class='muted'>{_e(x['label'])} — μη διαθέσιμη</td><td></td><td></td><td></td><td></td><td></td></tr>")
                continue
            mark = "✔" if x.get("included") else "—"
            cls = "hl" if x.get("included") else ""
            parts.append(f"<tr class='{cls}'><td>{_e(x['label'])} <span class='muted small'>({x['count']} εγγρ.)</span></td>"
                         f"<td class='n'>{mark}</td><td class='n'>{fmt_eur(x['net'])}</td>"
                         f"<td class='n'>{fmt_eur(x['extra_fees']) if x['extra_fees'] else ''}</td>"
                         f"<td class='n'>{fmt_eur(x['surcharges']) if x['surcharges'] else ''}</td><td class='n'>{fmt_eur(x['total'])}</td></tr>")
            if x["key"] == "keao_other":
                parts.append("<tr class='gap'><td colspan='6'></td></tr>")
        parts.append(f"<tr class='tot'><td>Καθαρό ποσό που αντιστοιχεί στη βεβαίωση</td><td></td><td class='n'>{fmt_eur(k['net_amount'])}</td>"
                     f"<td class='n'>{fmt_eur(k['extra_fees'])}</td><td class='n'>{fmt_eur(k['increments'])}</td><td class='n'>{fmt_eur(k['gross_paid'])}</td></tr>")
        parts.append(f"<tr><td>Ποσό φορολογικής βεβαίωσης</td><td></td><td class='n'>{fmt_eur(C)}</td><td></td><td></td><td></td></tr>")
        if k["difference"] is not None:
            parts.append(f"<tr class='diff {'hl' if st == 'match' else ''}'><td><b>Διαφορά (βεβαίωση − καθαρό)</b></td><td></td>"
                         f"<td class='n'><b>{fmt_eur(k['difference'])}</b></td><td></td><td></td><td></td></tr>")
        parts.append("</table>")
        pv = k.get("periods")
        if pv and pv["rows"]:
            parts.append(f"<h2>{kname} — ανάλυση ανά μήνα οφειλής {year}</h2>"
                         "<table><tr><th>Μήνας οφειλής</th><th class='n'>Μηνιαία</th><th class='n'>Πιστώσεις</th>"
                         "<th class='n'>Επιβαρύνσεις</th><th class='n'>Πληρωτέο</th><th>Εξοφλήθηκε (ημ/νία: ποσό)</th>"
                         f"<th class='n'>Καθαρό πληρ. {year}</th><th class='n'>Ανεξόφλητο</th></tr>")
            for p in pv["rows"]:
                paid = "; ".join(f"{x['date']}: {fmt_eur(x['amount'])}" + (" (επιβ.)" if x["type"] == "fees" else "")
                                 for x in p["paid"]) or "—"
                cls = "hl" if p.get("boundary") == "prev" else ""
                lab = p["period"] + (f" <span class='small muted'>(μήνας {year - 1})</span>" if p.get("boundary") == "prev" else "")
                late = any(int(x["date"][-4:]) > year for x in p["paid"])
                parts.append(f"<tr class='{cls}'><td>{lab}</td><td class='n'>{fmt_eur(p['monthly'])}</td>"
                             f"<td class='n'>{fmt_eur(p['credits']) if p['credits'] else ''}</td>"
                             f"<td class='n'>{fmt_eur(p['fees']) if p['fees'] else ''}</td>"
                             f"<td class='n'>{fmt_eur(p['payable']) if p.get('payable') is not None else ''}</td>"
                             f"<td class='small'>{_e(paid)}{' <b>(' + str(year + 1) + ')</b>' if late else ''}</td>"
                             f"<td class='n'>{fmt_eur(p['paid_in_year'])}</td>"
                             f"<td class='n'>{fmt_eur(p['outstanding']) if p['outstanding'] > 0.004 else ''}</td></tr>")
            parts.append("</table><table class='recon'>"
                         f"<tr><td>Καθαρές εισφορές μηνών οφειλής {year} (από τα ειδοποιητήρια)</td><td class='n'>{fmt_eur(pv['contrib_periods_year'])}</td></tr>"
                         f"<tr><td>+ εισφορές μηνών {year - 1} που πληρώθηκαν μέσα στο {year}</td><td class='n'>{fmt_eur(pv['paid_prev_periods'])}</td></tr>"
                         f"<tr><td>− εισφορές μηνών {year} που ΔΕΝ πληρώθηκαν μέσα στο {year} (πληρώθηκαν {year + 1} ή ανεξόφλητες)</td><td class='n'>{fmt_eur(pv['not_paid_in_year'])}</td></tr>"
                         f"<tr class='tot'><td>= Καθαρές εισφορές που καταβλήθηκαν το {year} (ταμειακή βάση, όπως η βεβαίωση)</td><td class='n'>{fmt_eur(pv['cash_net_contrib'])}</td></tr>"
                         f"<tr><td>Επιβαρύνσεις που εξοφλήθηκαν μέσα στο {year}</td><td class='n'>{fmt_eur(pv['fees_paid_in_year'])}</td></tr></table>")
        if st == "difference" and k["hypotheses"]:
            parts.append("<p class='small muted'>Πλησιέστεροι συνδυασμοί: " + "; ".join(
                f"{_e(h['label'])} = {fmt_eur(h['value'])} (διαφορά {fmt_eur(h.get('diff'))})" for h in k["hypotheses"][:4]) + "</p>")
        parts.append("</div>")
        # Κινήσεις — ΧΩΡΙΣΤΟΣ πίνακας ανά πηγή
        for x in k["components"]:
            if not x["rows"] or (x.get("alt_of") and not x.get("included")):
                continue
            tag = "" if x.get("included") else " <span class='muted small'>(δεν περιλαμβάνεται στη βεβαίωση)</span>"
            parts.append(f"<h2>{_e(x['label'])} — {year}{tag}</h2>")
            if x["origin"] == "ΚΕΑΟ":
                regs = "; ".join(f"ΑΜΟ {_e(r['amo'])} — {_e(re.sub(r'^Ληξιπρόθεσμο - ', '', r['forea'] or ''))}" for r in x["registries"])
                parts.append(f"<p class='small muted'>{regs}</p>")
                parts.append("<table><tr><th>Ημ/νία</th><th>ΑΜΟ</th><th>Παραστατικό</th><th>Κωδ. κίν.</th>"
                             "<th class='n'>Κύρια εισφορά</th><th class='n'>Πρόσθ. τέλη</th><th class='n'>Προσαυξ.</th><th class='n'>Σύνολο</th></tr>")
                for r in sorted(x["rows"], key=lambda r: _date_key(r.get("date") or "")):
                    parts.append(f"<tr><td>{_e(r.get('date'))}</td><td>{_e(r.get('amo'))}</td><td>{_e(r.get('doc'))}</td><td>{_e(r.get('kod_kin'))}</td>"
                                 f"<td class='n'>{fmt_eur(r['main_contrib'])}</td><td class='n'>{fmt_eur(r['extra_fees'])}</td>"
                                 f"<td class='n'>{fmt_eur(r['surcharges'])}</td><td class='n'>{fmt_eur(r['total'])}</td></tr>")
                parts.append(f"<tr class='tot'><td colspan='4'>Σύνολα</td><td class='n'>{fmt_eur(x['net'])}</td>"
                             f"<td class='n'>{fmt_eur(x['extra_fees'])}</td><td class='n'>{fmt_eur(x['surcharges'])}</td>"
                             f"<td class='n'>{fmt_eur(x['total'])}</td></tr></table>")
            else:
                if x["key"] == "efka_payments":
                    parts.append("<table><tr><th>Ημ/νία</th><th>Εξόφληση (μήνας οφειλής)</th><th>Κωδικός πληρωμής</th><th class='n'>Ποσό πληρωμής</th>"
                                 "<th class='n'>Επιβαρ./Πρόσθ. τέλη</th><th class='n'>Καθαρό</th></tr>")
                    for r in sorted(x["rows"], key=lambda r: _date_key(r.get("date") or "")):
                        n = r.get("notice") or {}
                        if r.get("alloc"):
                            nt = "<br>".join(_e(("επιβ. " if a["type"] == "fees" else ("πλεόνασμα" if a["type"] == "excess" else ""))
                                                + (a["period"] or "") + " " + fmt_eur(a["amount"])) for a in r["alloc"])
                        else:
                            nt = (_e(f"{n.get('period')} (έκδ. {n.get('issue_date')})") if n else
                                  "<span class='muted'>χωρίς ειδοποιητήριο</span>")
                        if n and r.get("split") == "notice_sequence":
                            nt += f"<br><span class='small muted'>πληρωτέο {fmt_eur(n.get('payable'))} ≠ πληρωμή</span>"
                        parts.append(f"<tr><td>{_e(r.get('date'))}</td><td>{nt}</td><td class='small'>{_e(r.get('doc'))}</td>"
                                     f"<td class='n'>{fmt_eur(r.get('total'))}</td><td class='n'>{fmt_eur(r.get('fees')) if r.get('fees') else ''}</td>"
                                     f"<td class='n'>{fmt_eur(r['net'])}</td></tr>")
                    parts.append(f"<tr class='tot'><td colspan='3'>Σύνολα</td><td class='n'>{fmt_eur(x['total'])}</td>"
                                 f"<td class='n'>{fmt_eur(x['extra_fees'])}</td><td class='n'>{fmt_eur(x['net'])}</td></tr></table>")
                    if x.get("unmatched"):
                        parts.append(f"<p class='small muted'>{x['unmatched']} πληρωμή(-ές) χωρίς ειδοποιητήριο με ίδιο πληρωτέο — το ποσό τους "
                                     f"λογίζεται ολόκληρο ως καθαρό (επιβαρύνσεις δεν διαχωρίστηκαν).</p>")
                    continue
                parts.append("<table><tr><th>Ημ/νία</th><th>Είδος</th><th>Κωδικός / Αιτιολογία</th><th class='n'>Ποσό</th></tr>")
                for r in sorted(x["rows"], key=lambda r: _date_key(r.get("date") or "")):
                    raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
                    desc = raw.get("Είδος Καταβολής") or raw.get("Προέλευση") or r.get("period") or ""
                    ref = " · ".join(v for v in (r.get("doc"), raw.get("Αιτιολογία")) if v)
                    parts.append(f"<tr><td>{_e(r.get('date'))}</td><td>{_e(desc)}</td><td class='small'>{_e(ref)}</td>"
                                 f"<td class='n'>{fmt_eur(r['net'])}</td></tr>")
                parts.append(f"<tr class='tot'><td colspan='3'>Σύνολο</td><td class='n'>{fmt_eur(x['net'])}</td></tr></table>")
    cv = recon.get("compare")
    if cv and cv.get("rows"):
        parts.append("<div class='sec'><h2>Αντιπαραβολή με τον πίνακα Ε3 (myDATA / εισαγόμενο ισοζύγιο)</h2>")
        if cv.get("scope_label"):
            parts.append(f"<p class='small muted'>{_e(cv['scope_label'])}</p>")
        parts.append("<table class='cmp'><tr><th style='width:9%'>Κωδ. Ε3</th><th style='width:35%'>Περιγραφή</th><th class='n'>Βεβαιώσεις / καρτέλα</th>"
                     "<th class='n'>myDATA</th><th class='n'>Ισοζύγιο</th><th class='n'>Διαφ. myDATA</th><th class='n'>Διαφ. ισοζυγίου</th></tr>")
        def _dc(d):
            if d is None:
                return "<td class='n muted'>—</td>"
            flag = "✓ " if abs(d) <= TOLERANCE else "⚠ "
            return f"<td class='n'><b>{flag}{fmt_eur(d)}</b></td>" if abs(d) > TOLERANCE else f"<td class='n'>{flag}{fmt_eur(d)}</td>"
        for r in cv["rows"]:
            parts.append(f"<tr><td>{_e(r.get('code'))}</td><td>{_e(r.get('desc'))}</td><td class='n'>{fmt_eur(r.get('value'))}</td>"
                         f"<td class='n'>{fmt_eur(r.get('mydata')) if r.get('mydata') is not None else '—'}</td>"
                         f"<td class='n'>{fmt_eur(r.get('isozygio')) if r.get('isozygio') is not None else '—'}</td>"
                         + _dc(r.get("mydata_diff")) + _dc(r.get("isozygio_diff")) + "</tr>")
        parts.append("</table>")
        note = cv.get("note") or ("το 585.007 του Ε3 πρέπει να ισούται με το καθαρό ποσό των φορολογικών "
                                  "βεβαιώσεων· οι επιβαρύνσεις (πρόσθετα τέλη, προσαυξήσεις) δεν είναι εισφορές και "
                                  "αντιπαραβάλλονται με τον κωδικό 588.")
        parts.append(f"<p class='small'><b>Σημείωση:</b> {_e(note)}</p></div>")
    parts.append("<div class='foot'>Πηγές: e-ΕΦΚΑ «Δικαιώματα Ασφάλισης → Φορολογικές Βεβαιώσεις»· «Υποχρεώσεις Ασφάλισης → "
                 "Καταβολές» (Ιστορικό Πληρωμών, Γραμμάτια, Πιστωτικά Υπόλοιπα — έτος κατά ημερομηνία καταβολής/προέλευσης)· "
                 "«Μηνιαία Ειδοποιητήρια» (καρτέλα ανά μήνα οφειλής: κάθε πληρωμή εξοφλεί πρώτα τις ανοιχτές «Επιβαρύνσεις – "
                 "Πρόσθετα Τέλη» και μετά τις εισφορές του παλαιότερου μήνα· μόνο όταν η βεβαίωση δεν συμφωνεί ήδη)· "
                 "ΚΕΑΟ «Κινήσεις Οφειλέτη → Πιστώσεις» ανά μητρώο (κύρια εισφορά / πρόσθετα τέλη / προσαυξήσεις, έτος κατά "
                 "ημερομηνία έκδοσης). Μητρώα εργοδότη δεν συμμετέχουν. Παράγεται αυτόματα από το ScanmyData.</div></body></html>")
    return "".join(parts)


def render_pdf(html: str, pdf_path: Path) -> Optional[str]:
    """HTML → PDF με Playwright Chromium. Επιστρέφει μήνυμα σφάλματος ή None."""
    try:
        from playwright.sync_api import sync_playwright
        try:
            from e3.checks import chromium_launch_args
        except Exception:
            try:
                from __init__ import chromium_launch_args  # type: ignore
            except Exception:
                def chromium_launch_args(extra=None):
                    return ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True, args=chromium_launch_args())
            try:
                pg = b.new_page()
                pg.set_content(html, wait_until="load")
                pg.pdf(path=str(pdf_path), format="A4", print_background=True,
                       display_header_footer=True, header_template="<span></span>",
                       footer_template="<div style='font-size:8px;width:100%;text-align:center;color:#888'>"
                                       "Σελίδα <span class='pageNumber'></span> από <span class='totalPages'></span></div>",
                       margin={"top": "14mm", "bottom": "16mm", "left": "12mm", "right": "12mm"})
            finally:
                b.close()
        return None
    except Exception as exc:
        return str(exc)


# ------------------------------------------------------------ orchestration
def precheck(year: int, certificates: Dict[str, Any], payments: Dict[str, Any], keao: Dict[str, Any]) -> Dict[str, Any]:
    """Αντιπαραβολή ΧΩΡΙΣ ειδοποιητήρια. Ό,τι ήδη συμφωνεί δεν χρειάζεται διαχωρισμό."""
    r = reconcile(year, certificates or {}, payments or {}, keao or {})
    statuses = {k: v["status"] for k, v in r["kinds"].items()}
    needed = [k for k, v in r["kinds"].items() if v["certificate_amount"] is not None and v["status"] != "match"]
    return {"statuses": statuses, "kinds_needed": needed}


def collect(username: str, password: str, afm: str, amka: str, year: int, out_dir: Path,
            keao_fallback: bool = True, notices_mode: str = "auto") -> Dict[str, Any]:
    diag = _Diag(out_dir / "_diag")
    raw: Dict[str, Any] = {"afm": afm, "amka": amka, "year": year,
                           "retrieved_at": _dt.datetime.now().isoformat(timespec="seconds"),
                           "login": None, "certificates": {}, "payments": {}, "keao": {}}
    http = _Http()
    progress(3, "Σύνδεση e-ΕΦΚΑ (TAXISnet)")
    L = cert.efka_non_employee_login(http, username, password, afm, amka)
    if not L.get("ok"):
        raw["login"] = {"ok": False, "reason": L.get("reason")}
        return raw
    raw["login"] = {"ok": True}
    diag.dump("landing.html", L.get("landing") or "")
    links, landing = L.get("links") or {}, L.get("landing") or ""
    roy = _find_link(links, landing, "Δικαιώματα Ασφάλισης", r"insuranceRoyalties\.xhtml", cert.ROY_PATH_FALLBACK)
    obl = _find_link(links, landing, "Υποχρεώσεις Ασφάλισης", r"insuranceObligations\.xhtml", OBL_PATH_FALLBACK)
    keao_url = _find_link(links, landing, "Ηλεκτρονική Πλατφόρμα Οφειλετών", r"ssp\.efka\.keao", KEAO_PATH_FALLBACK)

    pdf_dir = out_dir / "certificates"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    progress(12, f"Φορολογικές βεβαιώσεις {year}")
    raw["certificates"] = fetch_certificates(http, roy, afm, str(year), pdf_dir, diag)
    progress(28, "Καταβολές e-ΕΦΚΑ")
    try:
        raw["payments"] = fetch_payments(http, obl, diag)
    except Exception as exc:
        logging.exception("payments failed")
        raw["payments"] = {"ok": False, "error": str(exc), "rows": [], "tables": []}
    try:
        progress(48, "ΚΕΑΟ — πιστώσεις ανά μητρώο")
        raw["keao"] = fetch_keao(http, keao_url, diag)
    except Exception as exc:
        logging.exception("KEAO http failed")
        raw["keao"] = {"ok": False, "source": "http", "error": str(exc), "registries": []}
    if not raw["keao"].get("ok") and keao_fallback:
        logging.info("[keao] HTTP απέτυχε (%s) → fallback keao-mistoton.py", raw["keao"].get("error"))
        fb = fetch_keao_fallback(username, password, afm, amka, year, diag)
        fb["http_error"] = raw["keao"].get("error")
        if fb.get("ok"):
            raw["keao"] = fb
        else:
            raw["keao"]["fallback_error"] = fb.get("error")
    # Ειδοποιητήρια μόνο όταν χρειάζονται (ή αν ζητηθούν ρητά με --notices always).
    progress(68, "Προέλεγχος βεβαίωσης έναντι καταβολών")
    pc = precheck(year, raw["certificates"], raw["payments"], raw["keao"])
    raw["precheck"] = pc
    kinds = set(pc["kinds_needed"]) if notices_mode == "auto" else {"EFKA", "TEKA"}
    if notices_mode == "never" or not kinds:
        why = "ζητήθηκε χωρίς ειδοποιητήρια" if notices_mode == "never" else \
            "η βεβαίωση συμφωνεί ήδη με τις καταβολές — δεν χρειάζονται ειδοποιητήρια"
        logging.info("[notices] παράλειψη: %s", why)
        raw["notices"] = {"ok": True, "skipped": why, "notices": []}
    else:
        try:
            raw["notices"] = fetch_notices(http, obl, year, out_dir / "notices", diag, kinds=kinds)
        except Exception as exc:
            logging.exception("notices failed")
            raw["notices"] = {"ok": False, "error": str(exc), "notices": []}
    return raw


def compare_view(recon: Dict[str, Any], cmp: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Αντιπαραβολή με τα ποσά του Ε3 (myDATA / εισαγόμενο ισοζύγιο). `cmp` έρχεται από το e3_brain:
    {scope_label, rows:[{code, desc, value, mydata, isozygio}], note}. Επιστρέφει τις γραμμές με
    διαφορές και `mismatch` όταν κάποια διαθέσιμη πηγή διαφέρει πάνω από το όριο."""
    if not cmp or not cmp.get("rows"):
        return None
    rows, mismatch = [], False
    for r in cmp["rows"]:
        v = r.get("value")
        out = dict(r)
        for src in ("mydata", "isozygio"):
            o = r.get(src)
            if v is None or o is None:
                out[src + "_diff"] = None
                continue
            d = _r2(float(v) - float(o))
            out[src + "_diff"] = d
            if abs(d) > TOLERANCE:
                mismatch = True
        rows.append(out)
    return {"scope_label": cmp.get("scope_label") or "", "rows": rows, "note": cmp.get("note") or "",
            "mismatch": mismatch}


def finish(raw: Dict[str, Any], out_dir: Path, always_report: bool = False,
           compare: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    year = int(raw["year"])
    login = raw.get("login") or {}
    if login and not login.get("ok"):
        return {"ok": False, "afm": raw.get("afm"), "year": year, "login": login,
                "error": f"Αποτυχία σύνδεσης: {login.get('reason')}", "needs_report": False, "report_pdf": None}
    pay = raw.get("payments") or {}
    if pay.get("tables") and all("raw_rows" in t for t in pay["tables"]):
        # Ξαναδιάβασμα από τους αποθηκευμένους πίνακες (οι διορθώσεις parser ισχύουν και σε --from-raw).
        rows: List[Dict[str, Any]] = []
        for t in pay["tables"]:
            t["category"] = table_category(t.get("id", ""))
            for r in normalize_payment_rows({"id": t.get("id", ""), "headers": t.get("headers") or [],
                                             "rows": t.get("raw_rows") or []}):
                r["source"] = t["category"]
                rows.append(r)
        pay = dict(pay, rows=rows)
        raw["payments"] = pay
    notices = (raw.get("notices") or {}).get("notices") or []
    pc = precheck(year, raw.get("certificates") or {}, pay, raw.get("keao") or {})
    ledger_kinds = set(pc["kinds_needed"])
    notices_used = [n for n in notices if n.get("kind") in ledger_kinds]
    try:
        notice_stats = apply_notices(pay.get("rows") or [], notices_used)
    except Exception as exc:
        logging.exception("ledger ανά μήνα οφειλής απέτυχε — fallback αντιστοίχιση")
        notice_stats = apply_notices_by_match(pay.get("rows") or [], notices_used)
        notice_stats["ledger_error"] = str(exc)
    ledger = notice_stats.pop("ledger", {}) or {}
    recon = reconcile(year, raw.get("certificates") or {}, pay, raw.get("keao") or {},
                      ledger_kinds={k for k in ledger_kinds if any(n.get("kind") == k for n in notices_used)})
    recon["notice_stats"] = notice_stats
    recon["precheck"] = pc
    cv = compare_view(recon, compare)
    if cv:
        recon["compare"] = cv
        if cv["mismatch"]:
            recon["needs_report"] = True
    for kind, periods in ledger.items():
        if kind in recon["kinds"] and kind in ledger_kinds:
            pv = period_view(year, periods)
            recon["kinds"][kind]["periods"] = pv
            recon["kinds"][kind]["explanation"].append(
                f"Ανάλυση ανά μήνα οφειλής {year}: εισφορές μηνών {year} {fmt_eur(pv['contrib_periods_year'])}"
                f" + πληρωμένες το {year} εισφορές μηνών {year - 1} {fmt_eur(pv['paid_prev_periods'])}"
                f" − εισφορές μηνών {year} που δεν πληρώθηκαν μέσα στο {year} {fmt_eur(pv['not_paid_in_year'])}"
                f" = {fmt_eur(pv['cash_net_contrib'])} καθαρές εισφορές που καταβλήθηκαν το {year}.")
    holder: Dict[str, Any] = {}
    for kind in ("EFKA", "TEKA"):
        holder = holder or ((raw.get("certificates") or {}).get(kind) or {}).get("holder") or {}
    summary: Dict[str, Any] = {
        "ok": bool((raw.get("login") or {}).get("ok", True)), "afm": raw.get("afm"), "year": year,
        "login": raw.get("login"),
        "sources": {
            "certificates": {k: {"ok": (v or {}).get("ok"), "amount": (v or {}).get("certificate_amount"),
                                 "source": (v or {}).get("certificate_amount_source"), "error": (v or {}).get("error"),
                                 "years_available": (v or {}).get("years_available")}
                             for k, v in (raw.get("certificates") or {}).items()},
            "payments": {"ok": (raw.get("payments") or {}).get("ok"), "error": (raw.get("payments") or {}).get("error"),
                         "tabs": (raw.get("payments") or {}).get("tabs"),
                         "tables": [{k: t.get(k) for k in ("id", "headers", "roles", "row_count", "rows_parsed")}
                                    for t in (raw.get("payments") or {}).get("tables") or []],
                         "rows_total": len((raw.get("payments") or {}).get("rows") or [])},
            "keao": {"ok": (raw.get("keao") or {}).get("ok"), "source": (raw.get("keao") or {}).get("source"),
                     "error": (raw.get("keao") or {}).get("error"),
                     "registries": [{k: r.get(k) for k in ("amo", "forea", "class", "status")} | {"rows": len(r.get("rows") or [])}
                                    for r in (raw.get("keao") or {}).get("registries") or []]},
        },
        "reconciliation": {k: {kk: v[kk] for kk in ("status", "certificate_amount", "matched_label", "net_amount",
                                                    "difference", "extra_fees", "increments", "surcharges_total",
                                                    "gross_paid", "hypotheses", "explanation", "needs_report")}
                              | {"components": [{kk: x[kk] for kk in ("key", "label", "included", "count", "net",
                                                                      "extra_fees", "surcharges", "total")}
                                                for x in v["components"]]}
                           for k, v in recon["kinds"].items()},
        "periods": {k: {kk: v["periods"][kk] for kk in ("contrib_periods_year", "paid_prev_periods", "not_paid_in_year",
                                                       "cash_net_contrib", "fees_paid_in_year")}
                    for k, v in recon["kinds"].items() if v.get("periods")},
        "precheck": pc,
        "notices": {"ok": (raw.get("notices") or {}).get("ok"), "error": (raw.get("notices") or {}).get("error"),
                    "skipped": (raw.get("notices") or {}).get("skipped"), "used_for": sorted(ledger_kinds),
                    "downloaded": len(notices), "stats": notice_stats,
                    "list": [{k: n.get(k) for k in ("kind", "period", "issue_date", "payable", "fees", "credits",
                                                    "monthly", "identity_ok", "error")} for n in notices]},
        "compare": recon.get("compare"),
        "needs_report": recon["needs_report"], "report_pdf": None, "report_html": None, "report_error": None,
    }
    if not summary["ok"]:
        return summary
    afm = str(raw.get("afm") or "")
    if recon["needs_report"] or always_report:
        html = build_report_html(recon, holder, afm, str(raw.get("amka") or ""))
        hp = out_dir / f"efka_teka_recon_{afm}_{year}.html"
        hp.write_text(html, encoding="utf-8")
        summary["report_html"] = str(hp)
        pp = out_dir / f"efka_teka_recon_{afm}_{year}.pdf"
        progress(96, "Δημιουργία καρτέλας PDF")
        err = render_pdf(html, pp)
        if err:
            summary["report_error"] = err
        else:
            summary["report_pdf"] = str(pp)
    (out_dir / f"efka_teka_recon_full_{afm}_{year}.json").write_text(
        json.dumps(recon, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Αντιπαραβολή φορολογικής βεβαίωσης ΕΦΚΑ/ΤΕΚΑ με καταβολές e-ΕΦΚΑ και ΚΕΑΟ.")
    ap.add_argument("--username", default=os.getenv("EFKA_USER", ""))
    ap.add_argument("--password", default=os.getenv("EFKA_PASS", ""))
    ap.add_argument("--afm", default=os.getenv("EFKA_AFM", ""))
    ap.add_argument("--amka", default=os.getenv("EFKA_AMKA", ""))
    ap.add_argument("--year", type=int, default=_dt.date.today().year - 1)
    ap.add_argument("--out-dir", default=os.getenv("EFKA_RECON_OUT", "efka_recon_out"))
    ap.add_argument("--from-raw", default="", help="Αποθηκευμένο raw JSON: μόνο αντιπαραβολή + καρτέλα, χωρίς login.")
    ap.add_argument("--always-report", action="store_true", help="Καρτέλα PDF ακόμη κι αν όλα συμφωνούν.")
    ap.add_argument("--notices-dir", default="", help="Επιπλέον ειδοποιητήρια (PDF) από τοπικό φάκελο.")
    ap.add_argument("--notices", choices=("auto", "always", "never"), default="auto",
                    help="auto: ειδοποιητήρια μόνο αν η βεβαίωση δεν συμφωνεί ήδη· always: λήψη PDF πάντα· never: ποτέ.")
    ap.add_argument("--no-keao-fallback", action="store_true")
    ap.add_argument("--compare-json", default="", help="Ποσά Ε3 (myDATA/ισοζύγιο) για αντιπαραβολή μέσα στην καρτέλα.")
    ap.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--headed", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()

    out_dir = Path(a.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if a.from_raw:
        raw = json.loads(Path(a.from_raw).read_text(encoding="utf-8"))
    else:
        if not a.username or not a.afm or not a.amka:
            print("ERROR: --username, --afm, --amka είναι υποχρεωτικά.", file=sys.stderr)
            return 2
        password = a.password or getpass.getpass("Κωδικός TAXISnet: ")
        raw = collect(a.username, password, a.afm, a.amka, a.year, out_dir, keao_fallback=not a.no_keao_fallback,
                      notices_mode=a.notices)
        (out_dir / f"efka_recon_raw_{a.afm}_{a.year}.json").write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    if a.notices_dir:
        extra = load_notices_dir(Path(a.notices_dir))
        have = {(n.get("kind"), n.get("period"), n.get("notice_id")) for n in (raw.get("notices") or {}).get("notices") or []}
        merged = list((raw.get("notices") or {}).get("notices") or [])
        merged += [n for n in extra["notices"] if (n.get("kind"), n.get("period"), n.get("notice_id")) not in have]
        raw["notices"] = dict(raw.get("notices") or {"ok": True}, notices=merged)
    compare = None
    if a.compare_json:
        try:
            compare = json.loads(Path(a.compare_json).read_text(encoding="utf-8"))
        except Exception as exc:
            logging.warning("compare-json: %s", exc)
    summary = finish(raw, out_dir, always_report=a.always_report, compare=compare)
    (out_dir / "efka_teka_recon_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
