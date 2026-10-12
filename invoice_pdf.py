"""
Λήψη του PDF ενός παραστατικού από τον πάροχο ηλεκτρονικής τιμολόγησης.

Το myDATA (RequestDocs) δίνει για κάθε παραστατικό παρόχου ένα
`downloadingInvoiceUrl` (fetch.py). Είναι capability URL: περιέχει
μη-μαντεύσιμο token και δεν θέλει αυθεντικοποίηση. Εδώ παίρνουμε από αυτό το
PDF, με τα ίδια περάσματα που κάνει ο Timologio Downloader:

1. Άμεση λήψη αρχείου (download/provider.py):
   * γενικά ο πάροχος δίνει PDF στο `<url>/pdf` — το σκέτο URL είναι σελίδα
     προβολής (HTML). Ισχύει και για συνδέσμους με query (Megasoft)·
   * η Epsilon δεν δέχεται το `/pdf` αλλά έχει άμεσο `filedocument/getfile`·
   * η ΕΣΚΑΠ δεν έχει καθόλου αρχείο PDF, μόνο εκτυπώσιμη σελίδα·
   * ευγένεια ανά πάροχο (λίγα ταυτόχρονα αιτήματα), μία επανάληψη στα
     παροδικά σφάλματα, και παύση προς έναν πάροχο που σταμάτησε να απαντά:
     κάποιοι (π.χ. Impact) κόβουν προσωρινά την IP που τους στέλνει πολλά
     αυτόματα αιτήματα, και η επιμονή μόνο παρατείνει τον αποκλεισμό.
2. Απόδοση της σελίδας σε PDF με αόρατο Chromium (download/headless.py), για
   όσους παρόχους δείχνουν το παραστατικό μόνο ως σελίδα. Η σελίδα γίνεται PDF
   μόνο αν στοιχειοθετήθηκε πραγματικά (αρκετό κείμενο, όχι σελίδα σφάλματος,
   σύνδεσης ή ελέγχου «είστε άνθρωπος»).

Ό,τι δεν δίνει PDF ούτε έτσι αναφέρεται ως ViewerOnly, ώστε ο χρήστης να
ανοίξει τον σύνδεσμο στον δικό του browser. ΔΕΝ παρακάμπτουμε κανέναν έλεγχο
«είστε άνθρωπος» (π.χ. Epsilon DocViewer πίσω από Cloudflare): ο Downloader
τον αφήνει στον χρήστη σε ορατό παράθυρο, κάτι που σε server δεν υπάρχει.

ΑΣΦΑΛΕΙΑ: τα αιτήματα φεύγουν χωρίς κανένα header αυθεντικοποίησης — τα κλειδιά
ΑΑΔΕ δεν περνούν ποτέ από εδώ.
"""

from __future__ import annotations

import importlib.util
import ipaddress
import logging
import os
import queue
import re
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF"
PDF_SUFFIX = "/pdf"
_FORMAT_SUFFIXES = ("/pdf", "/myDATA", "/EN16931")

_EPSILON_HOST = "epsilonnet.gr"
_ESKAP_HOST = "eskap.gr"
# Πάροχοι χωρίς αρχείο PDF: δεν γίνεται άμεσο αίτημα, μόνο απόδοση της σελίδας.
VIEWER_ONLY_HOSTS = (_ESKAP_HOST,)
# Πάροχοι που η σελίδα τους δεν στοιχειοθετείται σε αόρατο browser (έλεγχος
# «είστε άνθρωπος»): δεν χάνουμε χρόνο να το δοκιμάσουμε.
RENDER_SKIP_HOSTS = (_EPSILON_HOST,)

_ESKAP_PRINT_RE = re.compile(r"invoice_print\.php\?authentication_code=([A-Za-z0-9]+)", re.I)
_ESKAP_CODE_RE = re.compile(r"[?&]authentication_code=([A-Za-z0-9]+)", re.I)

# UA σαν browser για τα απλά αιτήματα: κάποιοι πάροχοι γυρίζουν 403/άδειο χωρίς αυτό.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# (connect, read). Το αίτημα τρέχει μέσα σε request του χρήστη.
DEFAULT_TIMEOUT = (8, 25)
MAX_PDF_BYTES = 30 * 1024 * 1024
# Συνολικός χρόνος για ένα παραστατικό (όλα τα περάσματα): κάτω από τα 100s του proxy.
TOTAL_BUDGET = 75.0
MAX_PER_HOST = 2
RETRY_PAUSE = 1.5
# Πάροχος που δεν δέχεται καν σύνδεση: τόση παύση πριν ξαναδοκιμαστεί.
HOST_COOLDOWN = 300.0

# Απόδοση σελίδας
RENDER_MIN_TEXT = 300          # λιγότερο ορατό κείμενο = η σελίδα δεν στοιχειοθετήθηκε
RENDER_TIMEOUT = 25.0
RENDER_BLANK_GIVEUP = 10.0     # επίμονα κενή μετά από τόσο -> δεν θα στοιχειοθετηθεί
RENDER_IDLE_SECONDS = 90.0     # ο browser κλείνει όταν μείνει τόσο χωρίς δουλειά
RENDER_QUEUE_BUDGET = 45.0     # πόσο περιμένει ένα παραστατικό τη σειρά του
RENDER_CHALLENGE_TTL = 30 * 60


@dataclass
class PdfResult:
    payload: bytes
    source: str   # "provider" = αρχείο του παρόχου, "rendered" = εκτύπωση της σελίδας του


class PdfError(Exception):
    """Το PDF δεν ελήφθη. `reason` = σύντομος κωδικός, `message_el` = για τον χρήστη."""
    reason = "provider_error"
    message_el = "Σφάλμα παρόχου"
    retryable = False


class ViewerOnly(PdfError):
    reason = "viewer_only"
    message_el = "Ο πάροχος το δείχνει μόνο online, χωρίς αρχείο PDF"


class NotFound(PdfError):
    reason = "not_found"
    message_el = "Το PDF δεν βρέθηκε στον πάροχο"


class AuthRequired(PdfError):
    reason = "auth_required"
    message_el = "Ο πάροχος ζητά σύνδεση"


class Unavailable(PdfError):
    reason = "unavailable"
    message_el = "Ο πάροχος δεν αποκρίνεται"
    retryable = True
    network = False   # True όταν δεν έγινε καν σύνδεση (όχι απλώς HTTP 5xx)


class RateLimited(PdfError):
    reason = "rate_limited"
    message_el = "Προσωρινός περιορισμός από τον πάροχο"
    retryable = True

    def __init__(self, retry_after: Optional[float] = None) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


class UnsafeUrl(PdfError):
    reason = "unsafe_url"
    message_el = "Μη αποδεκτός σύνδεσμος"


def _scrub(text: Any) -> str:
    """Μήνυμα σφάλματος χωρίς συνδέσμους: το URL του παραστατικού είναι το
    «κλειδί» του και δεν πρέπει να καταλήγει σε logs."""
    out = re.sub(r"https?://\S+", "<url>", str(text))
    return re.sub(r"with url: \S+", "with url: <path>", out)[:200]


# ---------------------------------------------------------------------------
# Σύνδεσμοι ανά πάροχο
# ---------------------------------------------------------------------------

def _host_matches(netloc: str, host: str) -> bool:
    netloc = (netloc or "").lower().split("@")[-1].split(":")[0]
    return netloc == host or netloc.endswith("." + host)


def _in_hosts(url: str, hosts) -> bool:
    netloc = urlparse(url or "").netloc
    return any(_host_matches(netloc, h) for h in hosts)


def is_viewer_only(url: str) -> bool:
    """Ο πάροχος δεν έχει αρχείο PDF (μόνο σελίδα)."""
    return _in_hosts(url, VIEWER_ONLY_HOSTS)


def pdf_url(base: str) -> str:
    """Προσθέτει /pdf αν δεν υπάρχει ήδη μορφότυπος (και σε συνδέσμους με query)."""
    trimmed = (base or "").rstrip("/")
    if trimmed.endswith(_FORMAT_SUFFIXES):
        return trimmed
    return trimmed + PDF_SUFFIX


def epsilon_pdf_url(url: str):
    """Άμεσο PDF endpoint της Epsilon, ή None αν το URL δεν είναι Epsilon.

    Δέχεται `…/DocViewer/<uuid>` και `…/fd/<32-hex>:<n>` (32 hex -> UUID).
    Tenants χωρίς server PDF γυρίζουν 404 — τότε το παραστατικό είναι «μόνο online».
    """
    p = urlparse(url or "")
    if not _host_matches(p.netloc, _EPSILON_HOST):
        return None
    docid = None
    if "/DocViewer/" in p.path:
        docid = p.path.split("/DocViewer/")[-1].strip("/") or None
    else:
        m = re.search(r"/fd/([^/?#]+)", p.path, re.I)
        if m:
            hexonly = re.sub(r"[^0-9a-fA-F]", "", m.group(1).split(":")[0])
            if len(hexonly) == 32:
                docid = f"{hexonly[0:8]}-{hexonly[8:12]}-{hexonly[12:16]}-{hexonly[16:20]}-{hexonly[20:32]}"
    if not docid:
        return None
    return f"{p.scheme}://{p.netloc}/filedocument/getfile?fileType=2&documentId={docid}"


def eskap_print_url(url: str, timeout: float = 15.0):
    """Το URL της καθαρής εκτυπώσιμης σελίδας της ΕΣΚΑΠ, ή None.

    Η σελίδα `/invoice/<token>` έχει μενού και κουμπιά· η καθαρή μορφή είναι το
    `/invoice_print.php?authentication_code=…` (το κουμπί «Εκτύπωση»). Αυτή
    τυπώνεται σε PDF, ώστε το αρχείο να είναι μόνο το τιμολόγιο.
    """
    if not _in_hosts(url, (_ESKAP_HOST,)):
        return None
    p0 = urlparse(url)
    m = _ESKAP_CODE_RE.search(url)
    if m and "invoice_print.php" in p0.path.lower():
        return url
    if m:
        return f"{p0.scheme}://{p0.netloc}/invoice_print.php?authentication_code={m.group(1)}"
    try:
        resp = requests.get(url, timeout=timeout, allow_redirects=True, headers={"User-Agent": _BROWSER_UA})
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    m = _ESKAP_PRINT_RE.search(resp.text)
    if not m:
        return None
    p = urlparse(str(resp.url) or url)
    return f"{p.scheme}://{p.netloc}/invoice_print.php?authentication_code={m.group(1)}"


def is_safe_url(url: str) -> bool:
    """Μόνο http(s) προς δημόσιο host: ο σύνδεσμος έρχεται από αποθηκευμένα
    δεδομένα και το αίτημα γίνεται από τον server."""
    try:
        p = urlparse(url or "")
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        host = p.hostname
        if host.lower() == "localhost":
            return False
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
        if not infos:
            return False
        for info in infos:
            if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
                return False
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 1) Άμεση λήψη αρχείου
# ---------------------------------------------------------------------------

_host_sems: Dict[str, threading.BoundedSemaphore] = {}
_host_sems_lock = threading.Lock()
_host_down_until: Dict[str, float] = {}


def _host_of(url: str) -> str:
    return (urlparse(url or "").hostname or "").lower()


def host_paused(url: str) -> bool:
    """Ο πάροχος σταμάτησε να δέχεται συνδέσεις και είναι σε παύση."""
    return time.time() < _host_down_until.get(_host_of(url), 0.0)


@contextmanager
def _host_slot(url: str, wait: float = 30.0):
    """Ευγένεια ανά πάροχο: λίγα ταυτόχρονα αιτήματα προς τον ίδιο host."""
    host = _host_of(url)
    with _host_sems_lock:
        sem = _host_sems.get(host)
        if sem is None:
            sem = _host_sems[host] = threading.BoundedSemaphore(MAX_PER_HOST)
    if not sem.acquire(timeout=max(0.1, wait)):
        raise Unavailable("πολλά ταυτόχρονα αιτήματα προς τον πάροχο")
    try:
        yield
    finally:
        sem.release()


def _get_pdf(target: str, timeout, *, missing_is_viewer: bool) -> bytes:
    """Ένα αίτημα· τα bytes μόνο αν είναι πραγματικά PDF."""
    try:
        resp = requests.get(
            target, timeout=timeout, allow_redirects=True, stream=True,
            headers={"Accept": "application/pdf,*/*"},
        )
    except requests.RequestException as exc:
        error = Unavailable(_scrub(exc))
        error.network = isinstance(exc, (requests.ConnectionError, requests.Timeout))
        raise error from exc
    try:
        code = resp.status_code
        if code in (404, 410):
            if missing_is_viewer:
                raise ViewerOnly(f"HTTP {code}: χωρίς server PDF")
            raise NotFound(f"HTTP {code}")
        if code in (401, 403):
            raise AuthRequired(f"HTTP {code}")
        if code == 429:
            ra = resp.headers.get("Retry-After")
            raise RateLimited(float(ra) if ra and ra.isdigit() else None)
        if code >= 500:
            raise Unavailable(f"HTTP {code}")
        if code != 200:
            raise PdfError(f"HTTP {code}")
        chunks = []
        size = 0
        try:
            for chunk in resp.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    raise PdfError("πολύ μεγάλο αρχείο")
                chunks.append(chunk)
        except requests.RequestException as exc:
            raise Unavailable(_scrub(exc)) from exc
        payload = b"".join(chunks)
        content_type = resp.headers.get("Content-Type", "?")
        declared = resp.headers.get("Content-Length")
        encoded = resp.headers.get("Content-Encoding")
    finally:
        resp.close()
    # Ο έλεγχος γίνεται στα bytes, όχι στο Content-Type.
    if not payload.startswith(PDF_MAGIC):
        raise ViewerOnly(f"content-type={content_type}")
    if declared and declared.isdigit() and not encoded and int(declared) != len(payload):
        raise Unavailable(f"ατελής λήψη: {len(payload)} από {declared} bytes")
    return payload


def _direct(target: str, timeout, deadline: float, *, missing_is_viewer: bool) -> bytes:
    """Αίτημα με ευγένεια ανά host και μία επανάληψη στα παροδικά σφάλματα."""
    host = _host_of(target)
    attempt = 0
    while True:
        attempt += 1
        if host_paused(target):
            raise Unavailable("ο πάροχος δεν δέχεται συνδέσεις· νέα δοκιμή σε λίγα λεπτά")
        try:
            with _host_slot(target, wait=min(30.0, max(0.1, deadline - time.time()))):
                payload = _get_pdf(target, timeout, missing_is_viewer=missing_is_viewer)
            _host_down_until.pop(host, None)
            return payload
        except (Unavailable, RateLimited) as exc:
            pause = RETRY_PAUSE
            if isinstance(exc, RateLimited):
                if exc.retry_after is not None and exc.retry_after > 5:
                    raise
                pause = max(RETRY_PAUSE, exc.retry_after or 0)
            if attempt >= 2 or time.time() + pause + 5 > deadline:
                if getattr(exc, "network", False):
                    _host_down_until[host] = time.time() + HOST_COOLDOWN
                raise
            time.sleep(pause)


def _fetch_direct(url: str, timeout, deadline: float) -> bytes:
    if is_viewer_only(url):
        raise ViewerOnly("χωρίς αρχείο PDF")
    eps = epsilon_pdf_url(url)
    if eps:
        return _direct(eps, timeout, deadline, missing_is_viewer=True)
    target = pdf_url(url)
    try:
        return _direct(target, timeout, deadline, missing_is_viewer=False)
    except (ViewerOnly, Unavailable, RateLimited, UnsafeUrl):
        raise
    except PdfError as first:
        # Ο πάροχος δεν αναγνωρίζει το «/pdf» (404/403…). Η προδιαγραφή του myDATA
        # λέει ότι το σκέτο downloadingInvoiceUrl επιστρέφει PDF: δοκίμασέ το.
        plain = url.rstrip("/")
        if plain != target and time.time() + 5 < deadline:
            try:
                return _direct(plain, timeout, deadline, missing_is_viewer=False)
            except ViewerOnly as page:
                raise page from first     # υπάρχει σελίδα προβολής, όχι αρχείο
            except PdfError:
                pass
        raise first


# ---------------------------------------------------------------------------
# 2) Απόδοση της σελίδας του παρόχου σε PDF (αόρατο Chromium μέσω Playwright)
# ---------------------------------------------------------------------------

# Τι βλέπει ο browser στη σελίδα. Σελίδα ελέγχου «είστε άνθρωπος» ή σύνδεσης δεν
# είναι παραστατικό, όσο κείμενο κι αν έχει.
_PAGE_FACTS_JS = """() => ({
  text: document.body ? document.body.innerText.length : 0,
  challenge: !!document.querySelector(
    'iframe[src*="challenges.cloudflare.com"], script[src*="challenge-platform"], #challenge-form, ' +
    '.cf-turnstile, [name="cf-turnstile-response"], iframe[src*="hcaptcha.com"], iframe[title*="recaptcha" i]'
  ) || /just a moment|attention required|verify you are human/i.test(document.title || ''),
  login: !!document.querySelector('input[type="password"]')
})"""

_render_available: Optional[bool] = None
_render_broken_until = 0.0
_challenge_hosts: Dict[str, float] = {}


def render_available() -> bool:
    """Υπάρχει browser για απόδοση σελίδων; (Playwright· INVOICE_PDF_RENDER=0 το κλείνει.)"""
    global _render_available
    if str(os.getenv("INVOICE_PDF_RENDER", "1")).strip().lower() in ("0", "false", "no", "off"):
        return False
    if _render_available is None:
        try:
            _render_available = importlib.util.find_spec("playwright") is not None
        except Exception:
            _render_available = False
    return bool(_render_available) and time.time() >= _render_broken_until


def can_render(url: str) -> bool:
    """Αξίζει να δοκιμαστεί απόδοση της σελίδας αυτού του παρόχου;"""
    if not render_available() or _in_hosts(url, RENDER_SKIP_HOSTS) or host_paused(url):
        return False
    host = _host_of(url)
    seen = _challenge_hosts.get(host)
    return not (seen and time.time() - seen < RENDER_CHALLENGE_TTL)


def expects_pdf(url: str) -> bool:
    """False όταν είναι ήδη γνωστό ότι το παραστατικό ανοίγει μόνο ως σύνδεσμος."""
    if not url:
        return False
    return (not is_viewer_only(url)) or can_render(url)


class _Renderer:
    """Ένα Chromium, που αποδίδει τις σελίδες σειριακά από δικό του thread.

    Σκόπιμα ένας browser και σειριακά: κάθε Chromium είναι βαρύς, και τα
    αντικείμενα του Playwright ανήκουν στο thread που τα έφτιαξε. Ο browser
    ανοίγει στην πρώτη δουλειά και κλείνει όταν μείνει αδρανής.
    """

    def __init__(self) -> None:
        self._jobs: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def render(self, url: str, timeout: float) -> Dict[str, Any]:
        job: Dict[str, Any] = {
            "url": url, "timeout": timeout, "expires": time.time() + RENDER_QUEUE_BUDGET,
            "done": threading.Event(), "pdf": None, "reason": "unavailable",
        }
        with self._lock:
            self._jobs.put(job)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="invoice-pdf-render", daemon=True)
                self._thread.start()
        if not job["done"].wait(RENDER_QUEUE_BUDGET + timeout + 15):
            job["expires"] = 0.0   # αν δεν ξεκίνησε ακόμα, να μην ξεκινήσει
            return {"pdf": None, "reason": "busy"}
        return job

    def _fail_pending(self) -> None:
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                return
            job["done"].set()

    def _run(self) -> None:
        global _render_broken_until
        launched = False
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                launched = True
                try:
                    while True:
                        try:
                            job = self._jobs.get(timeout=RENDER_IDLE_SECONDS)
                        except queue.Empty:
                            with self._lock:
                                if self._jobs.empty():
                                    self._thread = None
                                    return
                            continue
                        try:
                            if time.time() > job["expires"]:
                                job["reason"] = "busy"
                            else:
                                self._render_one(browser, job)
                        except Exception as exc:
                            log.warning("invoice_pdf: render failed: %s", _scrub(exc))
                            job["reason"] = "unavailable"
                        finally:
                            job["done"].set()
                        if not browser.is_connected():
                            raise RuntimeError("browser disconnected")
                finally:
                    try:
                        browser.close()
                    except Exception:
                        pass
        except Exception as exc:
            log.warning("invoice_pdf: renderer stopped: %s", _scrub(exc))
            if not launched:
                # Δεν άνοιξε καθόλου browser: μη ξαναδοκιμάζεις σε κάθε παραστατικό.
                _render_broken_until = time.time() + 300
            with self._lock:
                self._thread = None
                self._fail_pending()

    @staticmethod
    def _render_one(browser, job: Dict[str, Any]) -> None:
        timeout = float(job["timeout"])
        started = time.time()
        context = browser.new_context(locale="el-GR", accept_downloads=False)
        try:
            page = context.new_page()
            response = page.goto(job["url"], wait_until="domcontentloaded", timeout=timeout * 1000)
            if response is not None and response.status >= 400:
                job["reason"] = "http"
                return
            # Περίμενε να σταθεροποιηθεί το κείμενο της σελίδας.
            facts = {"text": 0, "challenge": False, "login": False}
            last, stable = -1, 0
            while time.time() - started < timeout:
                try:
                    facts = page.evaluate(_PAGE_FACTS_JS)
                except Exception:
                    facts = {"text": 0, "challenge": False, "login": False}
                text = int(facts.get("text") or 0)
                if text == last:
                    stable += 1
                    if text >= RENDER_MIN_TEXT and stable >= 3:
                        break
                    if text < RENDER_MIN_TEXT and stable >= 4 and time.time() - started > RENDER_BLANK_GIVEUP:
                        break
                else:
                    stable, last = 0, text
                time.sleep(0.5)
            if facts.get("challenge"):
                job["reason"] = "challenge"
                return
            if facts.get("login"):
                job["reason"] = "login"
                return
            if int(facts.get("text") or 0) < RENDER_MIN_TEXT:
                job["reason"] = "blank"
                return
            pdf = page.pdf(
                format="A4", print_background=True, prefer_css_page_size=True,
                margin={"top": "0.3in", "bottom": "0.3in", "left": "0.3in", "right": "0.3in"},
            )
            if pdf and pdf.startswith(PDF_MAGIC):
                job["pdf"] = pdf
                job["reason"] = "ok"
            else:
                job["reason"] = "blank"
        finally:
            try:
                context.close()
            except Exception:
                pass


_renderer = _Renderer()


def render_pdf(url: str, timeout: float = RENDER_TIMEOUT) -> Dict[str, Any]:
    """Τυπώνει τη σελίδα του παραστατικού σε PDF. Επιστρέφει {"pdf", "reason"} με
    reason: ok | blank | challenge | login | http | busy | unavailable | skipped."""
    if not can_render(url):
        return {"pdf": None, "reason": "skipped"}
    target = eskap_print_url(url) or url
    if not is_safe_url(target):
        return {"pdf": None, "reason": "skipped"}
    outcome = _renderer.render(target, timeout)
    if outcome.get("reason") == "challenge":
        _challenge_hosts[_host_of(url)] = time.time()
    return {"pdf": outcome.get("pdf"), "reason": outcome.get("reason") or "unavailable"}


# ---------------------------------------------------------------------------
# Όλα τα περάσματα
# ---------------------------------------------------------------------------

def fetch_pdf(url: str, timeout=DEFAULT_TIMEOUT, allow_render: bool = True) -> PdfResult:
    """Το PDF του παραστατικού, αλλιώς PdfError."""
    url = str(url or "").strip()
    if not is_safe_url(url):
        raise UnsafeUrl(url[:80])
    deadline = time.time() + TOTAL_BUDGET
    try:
        return PdfResult(_fetch_direct(url, timeout, deadline), "provider")
    except (Unavailable, RateLimited, UnsafeUrl):
        raise
    except PdfError as exc:
        direct_error = exc

    if not allow_render or time.time() + RENDER_BLANK_GIVEUP > deadline:
        raise direct_error
    outcome = render_pdf(url, min(RENDER_TIMEOUT, max(RENDER_BLANK_GIVEUP, deadline - time.time() - 5)))
    if outcome["pdf"]:
        return PdfResult(outcome["pdf"], "rendered")
    if outcome["reason"] in ("blank", "challenge", "login") and not isinstance(direct_error, ViewerOnly):
        # Η σελίδα υπάρχει αλλά θέλει άνθρωπο: να ανοίξει ως σύνδεσμος.
        raise ViewerOnly(f"σελίδα: {outcome['reason']}") from direct_error
    raise direct_error
