"""
Λήψη του PDF ενός παραστατικού από τον πάροχο ηλεκτρονικής τιμολόγησης.

Το myDATA (RequestDocs) δίνει για κάθε παραστατικό παρόχου ένα
`downloadingInvoiceUrl` (fetch.py). Είναι capability URL: περιέχει
μη-μαντεύσιμο token και δεν θέλει αυθεντικοποίηση. Εδώ το μετατρέπουμε στο
endpoint που σερβίρει το PDF και ελέγχουμε ότι αυτό που ήρθε είναι όντως PDF.

Ίδιοι κανόνες με τον Timologio Downloader (download/provider.py):
  * γενικά ο πάροχος δίνει PDF στο `<url>/pdf` — το σκέτο URL είναι σελίδα
    προβολής (HTML)·
  * η Epsilon δεν δέχεται το `/pdf` αλλά έχει άμεσο `filedocument/getfile`·
  * η ΕΣΚΑΠ δεν έχει καθόλου αρχείο PDF, μόνο εκτυπώσιμη σελίδα.
Ό,τι δεν δίνει PDF αναφέρεται ως ViewerOnly, ώστε ο χρήστης να ανοίξει τον
σύνδεσμο στον browser.

ΑΣΦΑΛΕΙΑ: το αίτημα φεύγει χωρίς κανένα header αυθεντικοποίησης — τα κλειδιά
ΑΑΔΕ δεν περνούν ποτέ από εδώ.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

import requests

PDF_MAGIC = b"%PDF"
PDF_SUFFIX = "/pdf"
_FORMAT_SUFFIXES = ("/pdf", "/myDATA", "/EN16931")

_EPSILON_HOST = "epsilonnet.gr"
_ESKAP_HOST = "eskap.gr"
# Πάροχοι χωρίς αρχείο PDF: δεν γίνεται καν αίτημα, ανοίγει η σελίδα τους.
VIEWER_ONLY_HOSTS = (_ESKAP_HOST,)

# (connect, read). Το αίτημα τρέχει μέσα σε request του χρήστη.
DEFAULT_TIMEOUT = (8, 25)
MAX_PDF_BYTES = 30 * 1024 * 1024


class PdfError(Exception):
    """Το PDF δεν ελήφθη. `reason` = σύντομος κωδικός, `message_el` = για τον χρήστη."""
    reason = "provider_error"
    message_el = "Σφάλμα παρόχου"


class ViewerOnly(PdfError):
    reason = "viewer_only"
    message_el = "Ο πάροχος το δείχνει μόνο online, χωρίς αρχείο PDF"


class NotFound(PdfError):
    reason = "not_found"
    message_el = "Το PDF δεν βρέθηκε στον πάροχο"


class Unavailable(PdfError):
    reason = "unavailable"
    message_el = "Ο πάροχος δεν αποκρίνεται"


class UnsafeUrl(PdfError):
    reason = "unsafe_url"
    message_el = "Μη αποδεκτός σύνδεσμος"


def _host_matches(netloc: str, host: str) -> bool:
    netloc = (netloc or "").lower().split("@")[-1].split(":")[0]
    return netloc == host or netloc.endswith("." + host)


def is_viewer_only(url: str) -> bool:
    netloc = urlparse(url or "").netloc
    return any(_host_matches(netloc, h) for h in VIEWER_ONLY_HOSTS)


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


def fetch_pdf(url: str, timeout=DEFAULT_TIMEOUT) -> bytes:
    """Τα bytes του PDF του παραστατικού, αλλιώς PdfError."""
    url = str(url or "").strip()
    if not is_safe_url(url):
        raise UnsafeUrl(url[:80])
    if is_viewer_only(url):
        raise ViewerOnly("χωρίς αρχείο PDF")
    eps = epsilon_pdf_url(url)
    target = eps or pdf_url(url)
    try:
        resp = requests.get(
            target, timeout=timeout, allow_redirects=True, stream=True,
            headers={"Accept": "application/pdf,*/*"},
        )
    except requests.RequestException as exc:
        raise Unavailable(str(exc)[:200]) from exc
    try:
        if resp.status_code in (404, 410):
            if eps:
                raise ViewerOnly(f"HTTP {resp.status_code}: χωρίς server PDF")
            raise NotFound(f"HTTP {resp.status_code}")
        if resp.status_code == 429 or resp.status_code >= 500:
            raise Unavailable(f"HTTP {resp.status_code}")
        if resp.status_code != 200:
            raise PdfError(f"HTTP {resp.status_code}")
        chunks = []
        size = 0
        try:
            for chunk in resp.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    raise PdfError("πολύ μεγάλο αρχείο")
                chunks.append(chunk)
        except requests.RequestException as exc:
            raise Unavailable(str(exc)[:200]) from exc
        payload = b"".join(chunks)
    finally:
        resp.close()
    # Ο έλεγχος γίνεται στα bytes, όχι στο Content-Type.
    if not payload.startswith(PDF_MAGIC):
        raise ViewerOnly(f"content-type={resp.headers.get('Content-Type', '?')}")
    return payload
