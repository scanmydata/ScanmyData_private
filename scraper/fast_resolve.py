"""Γρήγορη, γενική επίλυση URL παρόχων ηλεκτρονικής τιμολόγησης.

Τρία επίπεδα, από το φθηνότερο:

1. ``repair_scanned_url``: διορθώνει URL που «χάλασαν» στο σκανάρισμα
   (barcode scanner σε λειτουργία πληκτρολογίου χάνει χαρακτήρες και το Shift:
   ``https:voice.impact.gr/p/el0815…`` αντί για ``https://einvoice.impact.gr/p/EL0815…``).
   Κάθε κανόνας εφαρμόζεται μόνο όταν λείπει το κανονικό πρόθεμα του παρόχου.

2. ``extract_url_ids`` + ``find_cached_invoice``: πολλοί πάροχοι βάζουν μέσα στο
   URL το **UID της ΑΑΔΕ** (impact ``/p/EL<ΑΦΜ>/<UID>/…``, vs.gr ``…~<UID>~…``,
   megasoft ``QrCode=base64("<UID>-<ΑΦΜ>-…")``) ή το ίδιο το MARK (primer).
   UID = SHA-1("ΑΦΜ-ΕΕΕΕ-ΜΜ-ΗΗ-εγκατάσταση-τύπος-σειρά-ΑΑ"), άρα ταιριάζει με
   τα παραστατικά που έχει ήδη κατεβάσει η «Λήψη» (``<vat>_invoices.json``)
   χωρίς ΚΑΝΕΝΑ αίτημα δικτύου.

3. ``resolve_megasoft_mydatapi``: το InvoiceLink της Megasoft είναι Blazor Server
   (όλα μέσω SignalR, χωρίς REST API) — το link myDATA βγαίνει μόνο με κλικ.
   Αντί για τον γενικό browser fallback (~14s) πατάμε το κουμπί μόλις εμφανιστεί
   και πιάνουμε το ``window.open`` (~4s).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
import time
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, quote, urlparse, urlunparse

MYDATAPI_RE = re.compile(r"https?://mydatapi\.aade\.gr/[^\s\"'<>]*TimologioQR/QRInfo\?q=[^\s\"'<>]+", re.I)
_HEX40_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{40})(?![0-9a-f])", re.I)
_MARK_RE = re.compile(r"(?<!\d)(\d{15})(?!\d)")
_UUID_TOKEN_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})[-_]([0-9a-z]{4,16})(?:[/?#]|$)",
    re.I,
)


def is_mydatapi_url(url: str) -> bool:
    try:
        p = urlparse(str(url or ""))
    except Exception:
        return False
    return "mydatapi.aade.gr" in (p.netloc or "").lower() and "timologioqr/qrinfo" in (p.path or "").lower()


# --------------------------------------------------------------------------- #
# 1) Διόρθωση URL από scanner
# --------------------------------------------------------------------------- #
def repair_scanned_url(url: str) -> str:
    """Επιστρέφει το URL διορθωμένο (ή αμετάβλητο όταν δεν αναγνωρίζεται)."""
    if not url:
        return url
    s = re.sub(r"\s+", "", str(url))
    # https:/x, https:x, https:///x -> https://x   (όχι όταν είναι ήδη σωστό)
    s = re.sub(r"^(https?):/*(?=[^/])", r"\1://", s, flags=re.I)
    if not re.match(r"^https?://", s, re.I):
        return s
    try:
        p = urlparse(s)
    except Exception:
        return s
    host = (p.netloc or "").lower()
    path = p.path or ""
    query = p.query or ""

    # Impact: ο host χάνει το «ein» και το path θέλει ΚΕΦΑΛΑΙΑ (με πεζά ο server
    # δίνει κενή σελίδα χωρίς στοιχεία).
    if host.endswith("impact.gr") and re.match(r"^/[pv]/", path, re.I):
        if host in ("voice.impact.gr", "invoice.impact.gr", "nvoice.impact.gr", "impact.gr", "www.impact.gr"):
            host = "einvoice.impact.gr"
        path = "/" + path[1].lower() + path[2:].upper()

    # e-invoicing.gr (Entersoft): /edocuments/ViewInvoice/-1/<uuid>_<token>
    elif host.endswith("e-invoicing.gr") and not path.lower().startswith("/edocuments/viewinvoice"):
        m = _UUID_TOKEN_RE.search(path + "/")
        m_prefix = re.search(r"/(-?\d+)/[0-9a-f]{8}-", path, re.I)
        if m:
            prefix = m_prefix.group(1) if m_prefix else "-1"
            path = f"/edocuments/ViewInvoice/{prefix}/{m.group(1).lower()}_{m.group(2)}"
    elif host.endswith("e-invoicing.gr"):
        # σωστό path αλλά «-» αντί «_» πριν το token (χαμένο Shift)
        m = re.search(r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-([0-9a-z]{4,16})$", path, re.I)
        if m:
            path = path[: m.start()] + f"/{m.group(1)}_{m.group(2)}"

    # Megasoft InvoiceLink: /invoiceinspect/qr?QrCode=...
    elif ("megasoft" in host or "invoicelink" in host) and re.search(r"(?:^|&)qrcode=", query, re.I):
        if not path.lower().startswith("/invoiceinspect/"):
            path = "/invoiceinspect/qr"

    # vs.gr (ILYDA): /iv/invoice/download/<pfx>/<pfx>~k1~<UID>~<token>
    elif host.endswith("vs.gr") and not path.lower().startswith("/iv/invoice/"):
        m = re.search(r"/([a-z0-9_]+)/(\1~[^/?#]+)", path, re.I)
        if m:
            path = f"/iv/invoice/download/{m.group(1)}/{m.group(2)}"

    return urlunparse((p.scheme.lower(), host, path, p.params, query, p.fragment))


# --------------------------------------------------------------------------- #
# 2) Αναγνωριστικά μέσα στο URL + αναζήτηση στα ήδη ληφθέντα παραστατικά
# --------------------------------------------------------------------------- #
def _b64_text(value: str) -> str:
    v = str(value or "").strip()
    if not v:
        return ""
    v = v.replace(" ", "+")
    for candidate in (v, v.replace("-", "+").replace("_", "/")):
        try:
            raw = base64.b64decode(candidate + "=" * (-len(candidate) % 4), validate=False)
            txt = raw.decode("utf-8", errors="ignore")
            if txt:
                return txt
        except (binascii.Error, ValueError):
            continue
    return ""


def extract_url_ids(url: str) -> Dict[str, Optional[str]]:
    """UID / MARK / ΑΦΜ εκδότη που «κουβαλάει» το ίδιο το URL (χωρίς δίκτυο)."""
    out: Dict[str, Optional[str]] = {"uid": None, "mark": None, "issuer_vat": None}
    try:
        p = urlparse(str(url or ""))
    except Exception:
        return out
    path = p.path or ""
    qs = parse_qs(p.query or "")

    # Megasoft: QrCode = base64("<UID>-<ΑΦΜ εκδότη>-<token>")
    for key in ("QrCode", "qrcode", "qrCode"):
        if qs.get(key):
            txt = _b64_text(qs[key][0])
            m = re.match(r"\s*([0-9a-f]{40})-(\d{9})\b", txt, re.I)
            if m:
                out["uid"], out["issuer_vat"] = m.group(1).upper(), m.group(2)
            break

    # Impact: /p/EL<ΑΦΜ>/<UID>/…
    m = re.search(r"/p/EL(\d{9})/([0-9a-f]{40})\b", path, re.I)
    if m:
        out["issuer_vat"] = out["issuer_vat"] or m.group(1)
        out["uid"] = out["uid"] or m.group(2).upper()

    if not out["uid"]:
        m = _HEX40_RE.search(path) or _HEX40_RE.search(p.query or "")
        if m:
            out["uid"] = m.group(1).upper()

    m = _MARK_RE.search(path) or _MARK_RE.search(p.query or "")
    if m and not is_mydatapi_url(url):
        out["mark"] = m.group(1)
    return out


def aade_uid(issuer_vat: str, issue_date: str, branch: Any, doc_type: str, series: str, aa: str) -> str:
    """UID παραστατικού κατά ΑΑΔΕ: SHA-1 (κεφαλαία hex) του
    «ΑΦΜ-ΕΕΕΕ-ΜΜ-ΗΗ-εγκατάσταση-τύπος-σειρά-ΑΑ»."""
    raw = "-".join([str(issuer_vat), str(issue_date), str(branch), str(doc_type), str(series), str(aa)])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest().upper()


def _iso_date(value: Any) -> str:
    s = str(value or "").strip()
    m = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    return s


def _series_variants(value: Any) -> List[str]:
    s = str(value if value is not None else "").strip()
    s = s.strip('"').strip("'").strip()
    variants = [s]
    if s == "":
        variants.append("0")
    return variants


def _doc_uid_candidates(doc: Dict[str, Any], branches: Iterable[int]) -> Iterable[str]:
    vat = str(doc.get("AFM_issuer") or doc.get("issuer_vat") or "").strip()
    date = _iso_date(doc.get("issueDate") or doc.get("issue_date"))
    dtype = str(doc.get("type") or doc.get("invoiceType") or "").strip()
    aa = str(doc.get("aa") or doc.get("AA") or "").strip()
    if not (vat and date and dtype and aa):
        return
    explicit_branch = doc.get("branch")
    branch_list = [explicit_branch] if explicit_branch not in (None, "") else list(branches)
    for series in _series_variants(doc.get("series")):
        for br in branch_list:
            yield aade_uid(vat, date, br, dtype, series, aa)


def find_cached_invoice(docs: Iterable[Dict[str, Any]], ids: Dict[str, Optional[str]],
                        max_branch: int = 20) -> Optional[Dict[str, Any]]:
    """Βρίσκει στα ήδη ληφθέντα παραστατικά αυτό που δείχνει το URL (MARK ή UID)."""
    if not ids:
        return None
    docs = [d for d in (docs or []) if isinstance(d, dict)]
    mark = str(ids.get("mark") or "").strip()
    if mark:
        for d in docs:
            if str(d.get("mark") or d.get("MARK") or "").strip() == mark:
                return d
    uid = str(ids.get("uid") or "").strip().upper()
    if not uid:
        return None
    vat = str(ids.get("issuer_vat") or "").strip()
    pool = [d for d in docs if not vat or str(d.get("AFM_issuer") or "").strip() == vat]
    branches = range(0, max_branch + 1)
    for d in pool:
        stored_uid = str(d.get("uid") or d.get("UID") or "").strip().upper()
        if stored_uid and stored_uid == uid:
            return d
        for cand in _doc_uid_candidates(d, branches):
            if cand == uid:
                return d
    return None


def find_cached_invoice_for_url(url: str, docs: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return find_cached_invoice(docs, extract_url_ids(repair_scanned_url(url)))


# --------------------------------------------------------------------------- #
# 3) Megasoft (Blazor Server): γρήγορο κλικ + σύλληψη window.open
# --------------------------------------------------------------------------- #
_CAPTURE_OPEN_JS = """
(() => {
  window.__smdOpenedUrl = null;
  window.open = function(u){ try { window.__smdOpenedUrl = String(u || ''); } catch (e) {} return null; };
})();
"""


def resolve_megasoft_mydatapi(url: str, budget_s: float = 15.0, debug: bool = False) -> Optional[str]:
    """Ανοίγει τη σελίδα InvoiceLink, πατά «Προβολή μέσω MyData» μόλις εμφανιστεί
    και επιστρέφει το URL που θα άνοιγε (mydatapi QRInfo). None αν αποτύχει."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    t0 = time.time()
    deadline = t0 + max(5.0, float(budget_s))
    found: Dict[str, Optional[str]] = {"url": None}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context()
                ctx.add_init_script(_CAPTURE_OPEN_JS)
                page = ctx.new_page()

                def _route(route):
                    try:
                        if route.request.resource_type in ("image", "media", "font", "stylesheet"):
                            route.abort()
                        else:
                            route.continue_()
                    except Exception:
                        pass

                try:
                    page.route("**/*", _route)
                except Exception:
                    pass
                page.on("request", lambda req: found.__setitem__("url", req.url) if is_mydatapi_url(req.url) else None)

                remaining_ms = lambda: max(1000, int((deadline - time.time()) * 1000))  # noqa: E731
                page.goto(url, wait_until="domcontentloaded", timeout=remaining_ms())
                btn = page.locator("button:has-text('MyData'), a:has-text('MyData')").first
                btn.wait_for(state="visible", timeout=remaining_ms())
                while time.time() < deadline and not found["url"]:
                    try:
                        btn.click(timeout=min(3000, remaining_ms()))
                    except Exception:
                        pass
                    for _ in range(20):
                        opened = page.evaluate("window.__smdOpenedUrl")
                        if opened and is_mydatapi_url(opened):
                            found["url"] = opened
                        if found["url"] or time.time() >= deadline:
                            break
                        page.wait_for_timeout(100)
            finally:
                browser.close()
    except Exception as exc:
        if debug:
            print("megasoft fast resolve error:", exc)
    if debug:
        print(f"megasoft fast resolve: {time.time() - t0:.2f}s -> {found['url']}")
    return found["url"]
