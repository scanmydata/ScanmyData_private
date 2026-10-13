# -*- coding: utf-8 -*-
"""
invoice_store.py — αποθήκευση των παραστατικών της ομάδας σε SQLite αντί για μεγάλα JSON αρχεία.

Τα τρία είδη αρχείων ανά πελάτη (ΑΦΜ):
    <ομάδα>/<ΑΦΜ>_invoices.json            kind = "invoices"   (πλήρη παραστατικά της λήψης)
    <ομάδα>/<ΑΦΜ>_summary.json             kind = "summary"    (summaries λήψης/αποδείξεων)
    <ομάδα>/epsilon/<ΑΦΜ>_epsilon_invoices.json   kind = "epsilon"   (καταχωρημένα στη λίστα, με γραμμές/κατηγορίες)

Σχεδιασμός
  * ΜΙΑ βάση ανά ομάδα: <ομάδα>/invoices.db (WAL). Πίνακας `docs`: μία γραμμή ανά παραστατικό (το πλήρες JSON της
    εγγραφής + εξαγμένες στήλες με ευρετήρια: MARK, εκδότης, αντισυμβαλλόμενος, ημερομηνία, είδος, χαρακτηρισμός,
    σύνολο). Πίνακας `stores`: μία γραμμή ανά (kind, ΑΦΜ) με τη σειρά/έκδοση και την υπογραφή του αρχείου.
  * Η ΒΑΣΗ είναι η πηγή αλήθειας για τις αναγνώσεις/εγγραφές της εφαρμογής. Το JSON αρχείο συνεχίζει να γράφεται
    ΣΥΓΧΡΟΝΑ από τη βάση (atomic) ως «export»: το Drive, τα αντίγραφα ασφαλείας και τα εξωτερικά εργαλεία (γέφυρες
    epsilon, scripts) δεν αλλάζουν καθόλου. Αν ένα αρχείο αλλάξει ΕΞΩΤΕΡΙΚΑ (Drive pull, χειροκίνητη επεξεργασία,
    script) η αλλαγή εντοπίζεται από την υπογραφή του (mtime, μέγεθος) και εισάγεται στη βάση πριν από την ανάγνωση.
  * Ημιτελές export: αν η εγγραφή στη βάση πέτυχε αλλά το αρχείο δεν γράφτηκε, η γραμμή `stores` μένει με
    export_dirty=1 και στην επόμενη ανάγνωση κερδίζει η βάση (το αρχείο ξαναγράφεται) — δεν «γυρνάμε πίσω» ποτέ.
  * Εγγραφή με διαφορές (diff): αλλάζουν μόνο οι γραμμές που διαφέρουν (πρόσθεση = ένα INSERT).
  * Ό,τι δεν είναι λίστα (π.χ. dict ρίζα στα παλιά epsilon) αποθηκεύεται αυτούσιο στη στήλη stores.raw.
  * Απενεργοποίηση: INVOICE_STORE=0 (ή χωρίς configure()) -> όλα συμπεριφέρονται όπως πριν (μόνο αρχεία).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

DB_NAME = "invoices.db"
# Παραστατικά πελάτη (ανά ΑΦΜ) + «έγγραφα ομάδας»: ό,τι άλλο JSON της ομάδας ταξιδεύει επίσης μέσα στη βάση.
DOC_KINDS = ("invoices", "summary", "epsilon")
SINGLETON_FILES = {                         # αρχείο ρίζας ομάδας -> kind (vat = "_")
    "credentials.json": "credentials",
    "fiscal_meta.json": "fiscal_meta",
    "e3_company_credentials_store.json": "e3_credentials",
    "support_tickets.json": "support_tickets",
}
KINDS = DOC_KINDS + ("exported_marks",) + tuple(SINGLETON_FILES.values())
SINGLETON_VAT = "_"
SCHEMA_VERSION = 2

_RE_INV = re.compile(r"^([0-9A-Za-z\-]+)_invoices\.json$")
_RE_SUM = re.compile(r"^([0-9A-Za-z\-]+)_summary\.json$")
_RE_EPS = re.compile(r"^([0-9A-Za-z\-]+)_epsilon_invoices\.json$")
_RE_EXM = re.compile(r"^([0-9A-Za-z\-]+)_exported_marks\.json$")
_VAT_TOKEN_RE = r"[0-9A-Za-z_\-]+"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    kind          TEXT NOT NULL,
    vat           TEXT NOT NULL,
    shape         TEXT NOT NULL DEFAULT 'list',
    raw           TEXT,
    n             INTEGER NOT NULL DEFAULT 0,
    version       INTEGER NOT NULL DEFAULT 0,
    updated_at    REAL NOT NULL DEFAULT 0,
    file_mtime_ns INTEGER NOT NULL DEFAULT 0,
    file_size     INTEGER NOT NULL DEFAULT -1,
    export_dirty  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (kind, vat)
);
CREATE TABLE IF NOT EXISTS docs (
    id              INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL,
    vat             TEXT NOT NULL,
    pos             INTEGER NOT NULL,
    mark            TEXT,
    issue_date      TEXT,
    issue_iso       TEXT,
    issuer_vat      TEXT,
    issuer_name     TEXT,
    counterpart_vat TEXT,
    aa              TEXT,
    doc_type        TEXT,
    classification  TEXT,
    class_l         TEXT,
    total           REAL,
    is_receipt      INTEGER NOT NULL DEFAULT 0,
    h               TEXT NOT NULL,
    data            TEXT NOT NULL
);
-- (rowid πίνακας + μοναδικό ευρετήριο θέσης: το WITHOUT ROWID στέλνει στις overflow σελίδες κάθε γραμμή >~1KB
--  και διπλασίαζε το μέγεθος της βάσης)
CREATE UNIQUE INDEX IF NOT EXISTS idx_docs_pos ON docs(kind, vat, pos);
CREATE INDEX IF NOT EXISTS idx_docs_mark ON docs(mark);
CREATE INDEX IF NOT EXISTS idx_docs_issuer ON docs(issuer_vat, issue_iso);
CREATE INDEX IF NOT EXISTS idx_docs_kv_class ON docs(kind, vat, class_l);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
-- «Ταφόπλακες»: σβησμένα store (π.χ. διαγραφή δεδομένων πελάτη). Ταξιδεύουν με το snapshot ώστε η συγχώνευση με το
-- Drive να μην αναστήσει δεδομένα που σβήστηκαν εσκεμμένα.
CREATE TABLE IF NOT EXISTS tombstones (
    kind       TEXT NOT NULL,
    vat        TEXT NOT NULL,
    deleted_at REAL NOT NULL,
    PRIMARY KEY (kind, vat)
);
"""

_ROOT: Optional[str] = None
_ENABLED = os.getenv("INVOICE_STORE", "1").strip().lower() not in {"0", "false", "no", "off"}

_LOCKS: Dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_SCHEMA_DONE: Dict[str, float] = {}
_SYNC_AT: Dict[str, float] = {}

# Κρυφή μνήμη κειμένου των λιστών (path -> (έκδοση, κείμενο JSON)): η πλήρης ανάγνωση γίνεται json.loads πάνω σε
# έτοιμο κείμενο (όσο και η ανάγνωση αρχείου) αντί για SELECT χιλιάδων γραμμών. Ισχύει όσο η έκδοση του store και η
# υπογραφή του αρχείου δεν έχουν αλλάξει (άλλη διεργασία που γράφει αλλάζει και τα δύο).
from collections import OrderedDict as _OD
_TEXT_CACHE: "_OD[str, Tuple[int, str, Optional[Tuple[int, int]]]]" = _OD()
_TEXT_CACHE_BYTES = 0
_TEXT_CACHE_MAX = int(os.getenv("INVOICE_STORE_CACHE_MB", "96")) * 1024 * 1024
_TEXT_CACHE_LOCK = threading.Lock()


def _cache_put(path: str, version: int, text: str, sig: Optional[Tuple[int, int]] = None) -> None:
    global _TEXT_CACHE_BYTES
    size = len(text)
    if size > _TEXT_CACHE_MAX // 2:
        return
    with _TEXT_CACHE_LOCK:
        old = _TEXT_CACHE.pop(path, None)
        if old is not None:
            _TEXT_CACHE_BYTES -= len(old[1])
        _TEXT_CACHE[path] = (version, text, sig)
        _TEXT_CACHE_BYTES += size
        while _TEXT_CACHE_BYTES > _TEXT_CACHE_MAX and _TEXT_CACHE:
            _k, (_v, _t, _s) = _TEXT_CACHE.popitem(last=False)
            _TEXT_CACHE_BYTES -= len(_t)


def _cache_get(path: str, version: int) -> Optional[str]:
    with _TEXT_CACHE_LOCK:
        ent = _TEXT_CACHE.get(path)
        if ent is not None and ent[0] == version:
            _TEXT_CACHE.move_to_end(path)
            return ent[1]
    return None


def _cache_get_by_sig(path: str, sig: Optional[Tuple[int, int]]) -> Optional[str]:
    """Γρήγορη διαδρομή χωρίς βάση: το αρχείο-export έχει την ίδια υπογραφή με τη στιγμή που φορτώθηκε/γράφτηκε
    το κείμενο (κάθε εγγραφή, από οποιαδήποτε διεργασία, αλλάζει την υπογραφή)."""
    if sig is None:
        return None
    with _TEXT_CACHE_LOCK:
        ent = _TEXT_CACHE.get(path)
        if ent is not None and ent[2] == sig:
            _TEXT_CACHE.move_to_end(path)
            return ent[1]
    return None


def _cache_drop(path: str) -> None:
    global _TEXT_CACHE_BYTES
    with _TEXT_CACHE_LOCK:
        old = _TEXT_CACHE.pop(path, None)
        if old is not None:
            _TEXT_CACHE_BYTES -= len(old[1])


# --------------------------------------------------------------------------- configuration / paths
def configure(data_root: Optional[str], enabled: Optional[bool] = None) -> None:
    """Ορίζει τον φάκελο data/ (οι ομάδες είναι άμεσα παιδιά του). Χωρίς configure() το store είναι ανενεργό."""
    global _ROOT, _ENABLED
    _ROOT = os.path.normcase(os.path.abspath(data_root)) if data_root else None
    if enabled is not None:
        _ENABLED = bool(enabled)


def is_enabled() -> bool:
    return bool(_ENABLED and _ROOT)


def db_path(group_dir: str) -> str:
    return os.path.join(group_dir, DB_NAME)


def classify(path: Any, _root: Optional[str] = None) -> Optional[Tuple[str, str, str]]:
    """path -> (group_dir, kind, vat) όταν το αρχείο διαχειρίζεται το store, αλλιώς None.
    _root: ρητός φάκελος data/ (για το συγχρονισμό Drive από scripts όπου δεν έχει γίνει configure)."""
    root = os.path.normcase(os.path.abspath(_root)) if _root else _ROOT
    if not _ENABLED or not root or not path:
        return None
    try:
        p = os.path.abspath(str(path))
    except Exception:
        return None
    base = os.path.basename(p)
    folder = os.path.dirname(p)
    kind = ""
    token = ""
    if os.path.basename(folder).lower() == "epsilon":
        m = _RE_EPS.match(base)
        if m:
            kind = "epsilon"
        else:
            m = _RE_EXM.match(base)
            if m:
                kind = "exported_marks"
        if not m:
            return None
        token = m.group(1)
        group_dir = os.path.dirname(folder)
    else:
        group_dir = folder
        if base in SINGLETON_FILES:
            kind, token = SINGLETON_FILES[base], SINGLETON_VAT
        else:
            m = _RE_INV.match(base)
            if m:
                kind = "invoices"
            else:
                m = _RE_SUM.match(base)
                if m:
                    kind = "summary"
            if not m:
                return None
            token = m.group(1)
            if token.lower() == "epsilon":      # το legacy epsilon_invoices.json (σκιά ομάδας)
                return None
    if os.path.normcase(os.path.dirname(group_dir)) != root:
        return None
    gname = os.path.basename(group_dir)
    if not gname or gname.startswith((".", "_")):
        return None
    return group_dir, kind, token


def is_managed(path: Any) -> bool:
    return classify(path) is not None


def file_path(group_dir: str, kind: str, vat: str) -> str:
    for fname, k in SINGLETON_FILES.items():
        if k == kind:
            return os.path.join(group_dir, fname)
    if kind == "exported_marks":
        return os.path.join(group_dir, "epsilon", f"{vat}_exported_marks.json")
    if kind == "epsilon":
        return os.path.join(group_dir, "epsilon", f"{vat}_epsilon_invoices.json")
    if kind == "summary":
        return os.path.join(group_dir, f"{vat}_summary.json")
    return os.path.join(group_dir, f"{vat}_invoices.json")


def _lock_for(group_dir: str) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(group_dir))
    with _LOCKS_GUARD:
        lk = _LOCKS.get(key)
        if lk is None:
            lk = _LOCKS[key] = threading.RLock()
        return lk


def _connect_once(group_dir: str) -> sqlite3.Connection:
    os.makedirs(group_dir, exist_ok=True)
    conn = sqlite3.connect(db_path(group_dir), timeout=30, isolation_level=None)
    try:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError:
            raise
        except Exception:
            pass
        key = os.path.normcase(os.path.abspath(group_dir))
        # Το σχήμα δημιουργείται μία φορά ανά διεργασία/αρχείο (και ξανά αν το αρχείο της βάσης χάθηκε).
        if key not in _SCHEMA_DONE or not os.path.exists(db_path(group_dir)):
            conn.executescript(_SCHEMA)
            _SCHEMA_DONE[key] = time.time()
        else:
            try:
                conn.execute("SELECT 1 FROM stores LIMIT 1")
            except sqlite3.OperationalError:
                conn.executescript(_SCHEMA)
        return conn
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        raise


def _connect(group_dir: str) -> sqlite3.Connection:
    try:
        return _connect_once(group_dir)
    except sqlite3.DatabaseError as e:
        msg = str(e).lower()
        if not any(k in msg for k in ("not a database", "malformed", "corrupt", "disk image")):
            raise
        # Φθαρμένη βάση: μπαίνει στην άκρη και ξαναχτίζεται από τα JSON (τα αρχεία-export είναι πάντα συγχρονισμένα).
        stamp = time.strftime("%Y%m%dT%H%M%S")
        base = db_path(group_dir)
        for suffix in ("", "-wal", "-shm"):
            src = base + suffix
            try:
                if os.path.exists(src):
                    os.replace(src, f"{base}.corrupt-{stamp}{suffix}")
            except OSError:
                pass
        _SCHEMA_DONE.pop(os.path.normcase(os.path.abspath(group_dir)), None)
        log.error("invoice_store: φθαρμένη βάση %s (%s) — μπήκε στην άκρη, ξαναχτίζεται από τα JSON", base, e)
        return _connect_once(group_dir)


# --------------------------------------------------------------------------- extraction
def _s(v: Any) -> str:
    try:
        return str(v).strip() if v is not None else ""
    except Exception:
        return ""


def _first(d: Dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return _s(v)
    return ""


def _money(v: Any) -> Optional[float]:
    s = _s(v).replace(" ", "")
    if not s:
        return None
    try:
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", ".")
        return float(s)
    except Exception:
        return None


def _iso_date(v: Any) -> str:
    s = _s(v)
    m = re.match(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    return m.group(0) if m else ""


def _cols(doc: Any) -> Tuple:
    """(mark, issue_date, issue_iso, issuer_vat, issuer_name, counterpart_vat, aa, doc_type, classification,
    class_l, total, is_receipt) — class_l: πεζά με Python (το lower() της SQLite αφορά μόνο ASCII)"""
    if not isinstance(doc, dict):
        return ("", "", "", "", "", "", "", "", "", "", None, 0)
    issue = _first(doc, "issueDate", "issue_date", "date")
    typ = _first(doc, "type_code", "type", "invoiceType")
    is_rc = 1 if (doc.get("is_receipt") or doc.get("_is_receipt") or typ.startswith("8")
                  or _first(doc, "docType", "doc_type").lower().startswith("receipt")) else 0
    return (
        _first(doc, "mark", "MARK", "Mark"),
        issue,
        _iso_date(issue),
        _first(doc, "AFM_issuer", "issuer_vat", "AFM"),
        _first(doc, "Name_issuer", "Name"),
        _first(doc, "AFM_counterpart", "counterpart_vat"),
        _first(doc, "AA", "aa", "number", "progressive_aa"),
        typ,
        _first(doc, "classification"),
        _first(doc, "classification").lower(),
        _money(_first(doc, "totalValue", "total")),
        is_rc,
    )


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _h(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:24]


# --------------------------------------------------------------------------- file export / import
def _file_sig(path: str) -> Optional[Tuple[int, int]]:
    try:
        st = os.stat(path)
        return (int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return None


def _replace_with_retry(src: str, dst: str, retries: int = 5, base_delay: float = 0.08) -> None:
    import errno
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))
        except OSError as e:
            if e.errno in (errno.EBUSY, errno.EACCES, errno.ETXTBSY) and attempt < retries - 1:
                time.sleep(base_delay * (2 ** attempt))
            else:
                raise


def _export_text(texts: List[str], raw: Optional[str]) -> str:
    if raw is not None:
        return raw
    if not texts:
        return "[]"
    return "[\n" + ",\n".join(texts) + "\n]"


def _write_file(path: str, text: str) -> Tuple[int, int]:
    dirp = os.path.dirname(path) or "."
    os.makedirs(dirp, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_json_", dir=dirp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except Exception:
                pass
        _replace_with_retry(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise
    sig = _file_sig(path)
    return sig if sig else (0, len(text.encode("utf-8")))


# --------------------------------------------------------------------------- DB primitives
def _store_row(conn: sqlite3.Connection, kind: str, vat: str):
    return conn.execute(
        "SELECT shape, raw, n, version, file_mtime_ns, file_size, export_dirty FROM stores WHERE kind=? AND vat=?",
        (kind, vat)).fetchone()


TOMBSTONE_TTL = 180 * 86400


def _drop_store(conn: sqlite3.Connection, kind: str, vat: str, when: Optional[float] = None) -> None:
    """Σβήνει ένα store από τη βάση και γράφει ταφόπλακα (ώστε να μην αναστηθεί από συγχώνευση με το Drive)."""
    now = float(when) if when is not None else time.time()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM docs WHERE kind=? AND vat=?", (kind, vat))
        conn.execute("DELETE FROM stores WHERE kind=? AND vat=?", (kind, vat))
        conn.execute("INSERT OR REPLACE INTO tombstones (kind, vat, deleted_at) VALUES (?,?,?)", (kind, vat, now))
        conn.execute("DELETE FROM tombstones WHERE deleted_at < ?", (time.time() - TOMBSTONE_TTL,))
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise


class ExportFailed(Exception):
    """Η εγγραφή του αρχείου-export απέτυχε (η συναλλαγή της βάσης ακυρώθηκε — δεν άλλαξε τίποτα)."""


def _db_save(conn: sqlite3.Connection, kind: str, vat: str, obj: Any, texts: List[str], raw: Optional[str],
             *, dirty: int, sig: Optional[Tuple[int, int]] = None, export=None,
             updated_at: Optional[float] = None) -> int:
    """Αποθηκεύει (με diff) το store. dirty=1: το αρχείο πρέπει ακόμη να γραφτεί/ξαναγραφτεί από τη βάση.
    export: callable που γράφει το αρχείο ΜΕΣΑ στη συναλλαγή (κρατά το write lock της SQLite, άρα και οι άλλες
    διεργασίες περιμένουν — βάση και αρχείο δεν αποκλίνουν). Αποτυχία του -> ROLLBACK + ExportFailed."""
    now = float(updated_at) if updated_at is not None else time.time()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if raw is not None:
            conn.execute("DELETE FROM docs WHERE kind=? AND vat=?", (kind, vat))
            n = 0
        else:
            old = {pos: h for pos, h in conn.execute("SELECT pos, h FROM docs WHERE kind=? AND vat=?", (kind, vat))}
            rows = []
            for i, t in enumerate(texts):
                h = _h(t)
                if old.get(i) == h:
                    continue
                rows.append((kind, vat, i) + _cols(obj[i]) + (h, t))
            if rows:
                conn.executemany(
                    "INSERT OR REPLACE INTO docs (kind,vat,pos,mark,issue_date,issue_iso,issuer_vat,issuer_name,"
                    "counterpart_vat,aa,doc_type,classification,class_l,total,is_receipt,h,data) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            if len(old) > len(texts):
                conn.execute("DELETE FROM docs WHERE kind=? AND vat=? AND pos>=?", (kind, vat, len(texts)))
            n = len(texts)
        prev = _store_row(conn, kind, vat)
        version = (prev[3] if prev else 0) + 1
        if export is not None:
            try:
                sig = export()
            except Exception as e:
                raise ExportFailed(str(e)) from e
            dirty = 0
        fm, fs = (sig if sig else ((prev[4], prev[5]) if prev else (0, -1)))
        conn.execute(
            "INSERT OR REPLACE INTO stores (kind,vat,shape,raw,n,version,updated_at,file_mtime_ns,file_size,export_dirty) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (kind, vat, "raw" if raw is not None else "list", raw, n, version, now, fm, fs, int(dirty)))
        conn.execute("DELETE FROM tombstones WHERE kind=? AND vat=?", (kind, vat))
        conn.execute("COMMIT")
        return version
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise


def _db_load(conn: sqlite3.Connection, kind: str, vat: str, row=None) -> Any:
    row = row or _store_row(conn, kind, vat)
    if not row:
        return None
    if row[0] == "raw":
        return json.loads(row[1]) if row[1] is not None else None
    parts = [r[0] for r in conn.execute("SELECT data FROM docs WHERE kind=? AND vat=? ORDER BY pos", (kind, vat))]
    if not parts:
        return []
    return json.loads("[" + ",".join(parts) + "]")


def _load_cached(conn: sqlite3.Connection, path: str, kind: str, vat: str, row,
                 sig: Optional[Tuple[int, int]] = None) -> Any:
    """Πλήρης ανάγνωση store με κρυφή μνήμη κειμένου (λίστες)."""
    if row[0] == "raw":
        return _db_load(conn, kind, vat, row)
    text = _cache_get(path, row[3])
    if text is None:
        parts = [r[0] for r in conn.execute("SELECT data FROM docs WHERE kind=? AND vat=? ORDER BY pos", (kind, vat))]
        text = "[" + ",".join(parts) + "]"
    _cache_put(path, row[3], text, sig)
    return json.loads(text)


def _serialize(obj: Any) -> Tuple[List[str], Optional[str]]:
    if isinstance(obj, list):
        return [_dump(x) for x in obj], None
    return [], json.dumps(obj, ensure_ascii=False, indent=2)


def _parse_file(path: str) -> Tuple[bool, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            txt = fh.read()
        if not txt or not txt.strip():
            return False, None
        return True, json.loads(txt)
    except Exception as e:
        log.warning("invoice_store: δεν διαβάστηκε το %s: %s", path, e)
        return False, None


def _import_file(conn: sqlite3.Connection, path: str, kind: str, vat: str) -> Tuple[bool, Any]:
    """Εισάγει το αρχείο στη βάση (δεν αλλάζει το αρχείο). Επιστρέφει (ok, αντικείμενο)."""
    sig = _file_sig(path)
    ok, obj = _parse_file(path)
    if not ok or sig is None:
        return False, None
    texts, raw = _serialize(obj)
    # updated_at = ώρα του ΠΕΡΙΕΧΟΜΕΝΟΥ (mtime αρχείου), όχι της εισαγωγής: στη συγχώνευση με το Drive κερδίζει
    # η πραγματικά νεότερη έκδοση και όχι αυτή που απλώς εισήχθηκε αργότερα.
    _db_save(conn, kind, vat, obj, texts, raw, dirty=0, sig=sig, updated_at=sig[0] / 1e9)
    return True, obj


# --------------------------------------------------------------------------- public API: read / write
def read(path: Any) -> Tuple[bool, Any]:
    """(found, object). found=False -> δεν διαχειρίζεται το store / δεν υπάρχει / δεν διαβάζεται: ο καλών
    συνεχίζει με την κανονική ανάγνωση αρχείου."""
    info = classify(path)
    if not info:
        return False, None
    group_dir, kind, vat = info
    p = os.path.abspath(str(path))
    try:
        # Γρήγορη διαδρομή: κείμενο στη μνήμη με την ίδια υπογραφή αρχείου -> καθόλου πρόσβαση στη βάση.
        _txt = _cache_get_by_sig(p, _file_sig(p))
        if _txt is not None:
            return True, json.loads(_txt)
        with _lock_for(group_dir):
            sig = _file_sig(p)
            if sig is None and not os.path.exists(db_path(group_dir)):
                return False, None      # ούτε αρχείο ούτε βάση: δεν δημιουργούμε κενή βάση
            conn = _connect(group_dir)
            try:
                row = _store_row(conn, kind, vat)
                if row is None:
                    if sig is None:
                        return False, None
                    ok, obj = _import_file(conn, p, kind, vat)
                    return (True, obj) if ok else (False, None)
                dirty = bool(row[6])
                if sig is None:
                    # Λείπει το αρχείο-export (σβήστηκε/χάθηκε ή δεν ολοκληρώθηκε το export): ξαναγράφεται από τη
                    # βάση — ΠΟΤΕ σιωπηλή διαγραφή δεδομένων. Εσκεμμένη διαγραφή = forget() (ταφόπλακα).
                    obj = _db_load(conn, kind, vat, row)
                    texts, raw = _serialize(obj)
                    s2 = _write_file(p, _export_text(texts, raw))
                    conn.execute("UPDATE stores SET file_mtime_ns=?, file_size=?, export_dirty=0 WHERE kind=? AND vat=?",
                                 (s2[0], s2[1], kind, vat))
                    return True, obj
                if sig == (row[4], row[5]) and not dirty:
                    return True, _load_cached(conn, p, kind, vat, row, sig)
                if dirty:
                    log.warning("invoice_store: %s έχει εκκρεμές export — κερδίζει η βάση", p)
                    obj = _db_load(conn, kind, vat, row)
                    texts, raw = _serialize(obj)
                    s2 = _write_file(p, _export_text(texts, raw))
                    conn.execute("UPDATE stores SET file_mtime_ns=?, file_size=?, export_dirty=0 WHERE kind=? AND vat=?",
                                 (s2[0], s2[1], kind, vat))
                    return True, obj
                # Το αρχείο άλλαξε εξωτερικά (Drive pull / εργαλείο): εισαγωγή.
                ok, obj = _import_file(conn, p, kind, vat)
                if ok:
                    return True, obj
                # Αδιάβαστο/κενό αρχείο: σερβίρουμε τα δεδομένα της βάσης (τελευταία έγκυρη κατάσταση).
                return True, _db_load(conn, kind, vat, row)
            finally:
                conn.close()
    except Exception:
        log.exception("invoice_store.read failed for %s", path)
        return False, None


def write(path: Any, obj: Any) -> bool:
    """Γράφει στη βάση και μετά (συγχρονα) το αρχείο-export. False -> δεν διαχειρίζεται το store ή απέτυχε η
    βάση πριν γραφτεί κάτι (ο καλών γράφει όπως πριν). Εξαίρεση -> η βάση γράφτηκε αλλά το αρχείο όχι."""
    info = classify(path)
    if not info:
        return False
    group_dir, kind, vat = info
    p = os.path.abspath(str(path))
    with _lock_for(group_dir):
        try:
            conn = _connect(group_dir)
        except Exception:
            log.exception("invoice_store.write: connect failed (%s)", group_dir)
            return False
        try:
            try:
                texts, raw = _serialize(obj)
                text = _export_text(texts, raw)
                _cache_drop(p)
                ver = _db_save(conn, kind, vat, obj, texts, raw, dirty=1, export=lambda: _write_file(p, text))
            except ExportFailed as e:
                raise OSError(f"invoice_store: αποτυχία εγγραφής {p}: {e}") from e
            except Exception:
                log.exception("invoice_store.write: DB write failed for %s", p)
                return False
            if raw is None:
                # Το κείμενο του export είναι έγκυρο JSON (λίστα) -> ίδιο με ό,τι θα έδινε το SELECT.
                _cache_put(p, ver, "[" + ",".join(texts) + "]", _file_sig(p))
            return True
        finally:
            conn.close()


def forget(path: Any) -> bool:
    """Σβήσιμο store (μετά από ΕΣΚΕΜΜΕΝΗ διαγραφή του αρχείου, π.χ. «διαγραφή δεδομένων πελάτη»): ταφόπλακα ώστε
    ο συγχρονισμός με το Drive να μην το αναστήσει. Δεν αγγίζει το αρχείο."""
    info = classify(path)
    if not info:
        return False
    group_dir, kind, vat = info
    if not os.path.exists(db_path(group_dir)):
        return False
    try:
        with _lock_for(group_dir):
            conn = _connect(group_dir)
            try:
                _drop_store(conn, kind, vat)
            finally:
                conn.close()
        _cache_drop(os.path.abspath(str(path)))
        return True
    except Exception:
        log.exception("invoice_store.forget failed for %s", path)
        return False


def version(path: Any) -> Optional[int]:
    info = classify(path)
    if not info:
        return None
    try:
        conn = _connect(info[0])
        try:
            row = _store_row(conn, info[1], info[2])
            return int(row[3]) if row else None
        finally:
            conn.close()
    except Exception:
        return None


# --------------------------------------------------------------------------- group sync / migration
def _managed_files(group_dir: str, _root: Optional[str] = None) -> List[Tuple[str, str, str]]:
    out: List[Tuple[str, str, str]] = []
    try:
        for fn in os.listdir(group_dir):
            full = os.path.join(group_dir, fn)
            if not os.path.isfile(full):
                continue
            c = classify(full, _root)
            if c:
                out.append((full, c[1], c[2]))
    except OSError:
        pass
    eps = os.path.join(group_dir, "epsilon")
    try:
        for fn in os.listdir(eps):
            full = os.path.join(eps, fn)
            if not os.path.isfile(full):
                continue
            c = classify(full, _root)
            if c:
                out.append((full, c[1], c[2]))
    except OSError:
        pass
    return out


def sync_group(group_dir: str, *, force: bool = False, min_interval: float = 2.0,
               _root: Optional[str] = None) -> Dict[str, int]:
    """Φέρνει τη βάση σε συμφωνία με τα αρχεία της ομάδας (νέα/αλλαγμένα/σβησμένα). Φτηνό (stat ανά αρχείο).
    _root: ρητός φάκελος data/ όταν δεν έχει γίνει configure (scripts/Drive)."""
    stats = {"files": 0, "imported": 0, "unchanged": 0, "removed": 0, "failed": 0, "exported": 0}
    if not _ENABLED or not (_root or _ROOT) or not os.path.isdir(group_dir):
        return stats
    key = os.path.normcase(os.path.abspath(group_dir))
    now = time.monotonic()
    if not force and (now - _SYNC_AT.get(key, 0.0)) < min_interval:
        return stats
    with _lock_for(group_dir):
        _SYNC_AT[key] = time.monotonic()
        if not os.path.exists(db_path(group_dir)) and not _managed_files(group_dir, _root):
            return stats          # ομάδα χωρίς παραστατικά: δεν δημιουργείται κενή βάση
        try:
            conn = _connect(group_dir)
        except Exception:
            log.exception("invoice_store.sync_group: connect failed")
            return stats
        try:
            known = {(k, v): (fm, fs, d) for k, v, fm, fs, d in conn.execute(
                "SELECT kind, vat, file_mtime_ns, file_size, export_dirty FROM stores")}
            seen = set()
            for full, kind, vat in _managed_files(group_dir, _root):
                stats["files"] += 1
                seen.add((kind, vat))
                sig = _file_sig(full)
                cur = known.get((kind, vat))
                if cur is not None and sig == (cur[0], cur[1]) and not cur[2]:
                    stats["unchanged"] += 1
                    continue
                if cur is not None and cur[2]:
                    # εκκρεμές export: η βάση κερδίζει
                    obj = _db_load(conn, kind, vat)
                    texts, raw = _serialize(obj)
                    s2 = _write_file(full, _export_text(texts, raw))
                    conn.execute("UPDATE stores SET file_mtime_ns=?, file_size=?, export_dirty=0 WHERE kind=? AND vat=?",
                                 (s2[0], s2[1], kind, vat))
                    stats["exported"] += 1
                    continue
                ok, _obj = _import_file(conn, full, kind, vat)
                if ok:
                    stats["imported"] += 1
                else:
                    stats["failed"] += 1
            for (kind, vat), cur in known.items():
                if (kind, vat) in seen:
                    continue
                # Το αρχείο λείπει αλλά το store υπάρχει: ξαναγράφεται από τη βάση (όχι σιωπηλή διαγραφή).
                obj = _db_load(conn, kind, vat)
                texts, raw = _serialize(obj)
                p = file_path(group_dir, kind, vat)
                s2 = _write_file(p, _export_text(texts, raw))
                conn.execute("UPDATE stores SET file_mtime_ns=?, file_size=?, export_dirty=0 WHERE kind=? AND vat=?",
                             (s2[0], s2[1], kind, vat))
                stats["exported"] += 1
        finally:
            conn.close()
    return stats


def migrate_all(data_root: Optional[str] = None, progress=None) -> Dict[str, Any]:
    """Εισάγει τα JSON όλων των ομάδων στη βάση τους (idempotent, μη καταστροφικά)."""
    root = data_root or (_ROOT or "")
    out: Dict[str, Any] = {"groups": {}, "files": 0, "imported": 0, "failed": 0}
    if not root or not os.path.isdir(root):
        return out
    for g in sorted(os.listdir(root)):
        gdir = os.path.join(root, g)
        if not os.path.isdir(gdir) or g.startswith((".", "_")):
            continue
        st = sync_group(gdir, force=True)
        out["groups"][g] = st
        out["files"] += st["files"]
        out["imported"] += st["imported"]
        out["failed"] += st["failed"]
        if progress:
            try:
                progress(g, st)
            except Exception:
                pass
    return out


def verify_group(group_dir: str) -> List[str]:
    """Συγκρίνει τα αρχεία με τη βάση. Επιστρέφει λίστα προβλημάτων (κενή = όλα συμφωνούν)."""
    problems: List[str] = []
    if not is_enabled():
        return ["store disabled"]
    if not os.path.exists(db_path(group_dir)) and not _managed_files(group_dir):
        return problems
    # Πρώτα εισάγονται οι αλλαγές αρχείων που έγιναν «απ' έξω» (άμεσες εγγραφές credentials.json κ.λπ.) —
    # ο έλεγχος αφορά διαφορές που ΔΕΝ εξηγούνται από αυτές (φθορά/ελλιπής εγγραφή).
    try:
        sync_group(group_dir, force=True)
    except Exception:
        pass
    conn = _connect(group_dir)
    try:
        for full, kind, vat in _managed_files(group_dir):
            row = _store_row(conn, kind, vat)
            if row is None:
                problems.append(f"{kind}/{vat}: λείπει από τη βάση")
                continue
            ok, fobj = _parse_file(full)
            if not ok:
                problems.append(f"{kind}/{vat}: αδιάβαστο αρχείο")
                continue
            dobj = _db_load(conn, kind, vat, row)
            if _dump(fobj) != _dump(dobj):
                problems.append(f"{kind}/{vat}: διαφορά αρχείου/βάσης")
        files = {(k, v) for _p, k, v in _managed_files(group_dir)}
        for k, v in conn.execute("SELECT kind, vat FROM stores"):
            if (k, v) not in files:
                problems.append(f"{k}/{v}: στη βάση χωρίς αρχείο")
    finally:
        conn.close()
    return problems


def stats(group_dir: str) -> Dict[str, Any]:
    out = {"stores": 0, "docs": 0, "by_kind": {}}
    if not os.path.exists(db_path(group_dir)):
        return out
    try:
        conn = _connect(group_dir)
        try:
            for k, n, c in conn.execute("SELECT kind, COUNT(*), COALESCE(SUM(n),0) FROM stores GROUP BY kind"):
                out["by_kind"][k] = {"stores": n, "docs": c}
                out["stores"] += n
                out["docs"] += c
        finally:
            conn.close()
    except Exception:
        pass
    return out


# --------------------------------------------------------------------------- queries
def _doc(row_data: str) -> Dict[str, Any]:
    try:
        return json.loads(row_data)
    except Exception:
        return {}


def ensure_fresh(group_dir: str, kind: str, vat: str) -> bool:
    """Φέρνει ΕΝΑ store σε συμφωνία με το αρχείο του (χωρίς φόρτωση). True αν υπάρχει στη βάση μετά τον έλεγχο."""
    if not is_enabled():
        return False
    p = file_path(group_dir, kind, vat)
    try:
        with _lock_for(group_dir):
            sig = _file_sig(p)
            if sig is None and not os.path.exists(db_path(group_dir)):
                return False
            conn = _connect(group_dir)
            try:
                row = _store_row(conn, kind, vat)
                if row is None:
                    if sig is None:
                        return False
                    ok, _o = _import_file(conn, p, kind, vat)
                    return ok
                if sig is None:
                    obj = _db_load(conn, kind, vat, row)
                    texts, raw = _serialize(obj)
                    s2 = _write_file(p, _export_text(texts, raw))
                    conn.execute("UPDATE stores SET file_mtime_ns=?, file_size=?, export_dirty=0 WHERE kind=? AND vat=?",
                                 (s2[0], s2[1], kind, vat))
                    return True
                if sig == (row[4], row[5]) and not row[6]:
                    return True
                if row[6]:
                    obj = _db_load(conn, kind, vat, row)
                    texts, raw = _serialize(obj)
                    s2 = _write_file(p, _export_text(texts, raw))
                    conn.execute("UPDATE stores SET file_mtime_ns=?, file_size=?, export_dirty=0 WHERE kind=? AND vat=?",
                                 (s2[0], s2[1], kind, vat))
                    return True
                _import_file(conn, p, kind, vat)
                return True       # αδιάβαστο αρχείο: μένουν τα δεδομένα της βάσης
            finally:
                conn.close()
    except Exception:
        log.exception("invoice_store.ensure_fresh failed (%s/%s)", kind, vat)
        return False


def _prepare_one(group_dir: str, kind: str, vat: str) -> Optional[sqlite3.Connection]:
    if not is_enabled() or not os.path.isdir(group_dir):
        return None
    ensure_fresh(group_dir, kind, vat)
    try:
        return _connect(group_dir)
    except Exception:
        log.exception("invoice_store: connect failed (%s)", group_dir)
        return None


def _prepare(group_dir: str) -> Optional[sqlite3.Connection]:
    if not is_enabled() or not os.path.isdir(group_dir):
        return None
    sync_group(group_dir)
    try:
        return _connect(group_dir)
    except Exception:
        log.exception("invoice_store: connect failed (%s)", group_dir)
        return None


def find_by_mark(group_dir: str, mark: str, kind: Optional[str] = None, vat: Optional[str] = None) -> List[Dict[str, Any]]:
    """Όλες οι εγγραφές με το δοσμένο MARK (όλων των πελατών/ειδών ή περιορισμένες)."""
    mark = _s(mark)
    conn = _prepare(group_dir)
    if conn is None or not mark:
        return []
    try:
        sql = "SELECT kind, vat, pos, data FROM docs WHERE mark=?"
        args: List[Any] = [mark]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if vat:
            sql += " AND vat=?"
            args.append(vat)
        sql += " ORDER BY kind, vat, pos"
        return [{"kind": k, "vat": v, "pos": p, "doc": _doc(d)} for k, v, p, d in conn.execute(sql, args)]
    finally:
        conn.close()


def find_by_issuer(group_dir: str, issuer_vat: str, kind: Optional[str] = None, vat: Optional[str] = None,
                   date_from: str = "", date_to: str = "", limit: int = 0) -> List[Dict[str, Any]]:
    """Όλα τα παραστατικά ενός προμηθευτή/εκδότη (προαιρετικά ανά είδος/πελάτη/εύρος ημερομηνιών ISO)."""
    issuer_vat = _s(issuer_vat)
    conn = _prepare(group_dir)
    if conn is None or not issuer_vat:
        return []
    try:
        sql = "SELECT kind, vat, pos, data FROM docs WHERE issuer_vat=?"
        args: List[Any] = [issuer_vat]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if vat:
            sql += " AND vat=?"
            args.append(vat)
        if date_from:
            sql += " AND issue_iso>=?"
            args.append(date_from)
        if date_to:
            sql += " AND issue_iso<=?"
            args.append(date_to)
        sql += " ORDER BY issue_iso, kind, vat, pos"
        if limit and limit > 0:
            sql += f" LIMIT {int(limit)}"
        return [{"kind": k, "vat": v, "pos": p, "doc": _doc(d)} for k, v, p, d in conn.execute(sql, args)]
    finally:
        conn.close()


def marks_of(group_dir: str, kind: str, vat: str) -> List[str]:
    """Τα MARK ενός store (χωρίς φόρτωση των εγγραφών). None αν το store δεν είναι διαθέσιμο."""
    conn = _prepare_one(group_dir, kind, vat)
    if conn is None:
        return None  # type: ignore[return-value]
    try:
        if _store_row(conn, kind, vat) is None:
            return None  # type: ignore[return-value]
        return [r[0] for r in conn.execute(
            "SELECT mark FROM docs WHERE kind=? AND vat=? AND mark<>'' ORDER BY pos", (kind, vat))]
    finally:
        conn.close()


def docs_where(group_dir: str, kind: str, vat: str, *, classification: Optional[str] = None,
               marks: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """Εγγραφές ενός store με φίλτρο χαρακτηρισμού (κεφαλαία/μικρά αδιάφορα) και/ή λίστα MARK, με τη σειρά του αρχείου.
    Επιστρέφει None αν το store δεν είναι διαθέσιμο (ο καλών κάνει την κανονική ανάγνωση)."""
    conn = _prepare_one(group_dir, kind, vat)
    if conn is None:
        return None  # type: ignore[return-value]
    try:
        if _store_row(conn, kind, vat) is None:
            return None  # type: ignore[return-value]
        sql = "SELECT data FROM docs WHERE kind=? AND vat=?"
        args: List[Any] = [kind, vat]
        if classification is not None:
            sql += " AND class_l=?"
            args.append(_s(classification).lower())
        if marks is not None:
            want = sorted({_s(m) for m in marks if _s(m)})
            out: List[Dict[str, Any]] = []
            for i in range(0, len(want), 500):
                chunk = want[i:i + 500]
                q = sql + " AND mark IN (" + ",".join("?" * len(chunk)) + ") ORDER BY pos"
                out.extend(_doc(r[0]) for r in conn.execute(q, args + chunk))
            return out
        sql += " ORDER BY pos"
        return [_doc(r[0]) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def issuer_for(group_dir: str, mark: str = "", aa: str = "", kind: str = "epsilon") -> str:
    """ΑΦΜ εκδότη για MARK ή ΑΑ (αντικαθιστά το σάρωμα όλων των epsilon αρχείων)."""
    conn = _prepare(group_dir)
    if conn is None:
        return ""
    try:
        mark, aa = _s(mark), _s(aa)
        if mark:
            r = conn.execute("SELECT issuer_vat FROM docs WHERE kind=? AND mark=? AND issuer_vat<>'' "
                             "ORDER BY vat, pos LIMIT 1", (kind, mark)).fetchone()
            if r:
                return r[0]
        if aa:
            r = conn.execute("SELECT issuer_vat FROM docs WHERE kind=? AND aa=? AND issuer_vat<>'' "
                             "ORDER BY vat, pos LIMIT 1", (kind, aa)).fetchone()
            if r:
                return r[0]
    finally:
        conn.close()
    return ""


def suppliers(group_dir: str, vat: Optional[str] = None, kind: str = "summary") -> List[Dict[str, Any]]:
    """Προμηθευτές με πλήθος και σύνολο (για επισκόπηση «τι έχει ο κάθε προμηθευτής»)."""
    conn = _prepare(group_dir)
    if conn is None:
        return []
    try:
        sql = ("SELECT issuer_vat, MAX(issuer_name), COUNT(*), COALESCE(SUM(total),0) FROM docs "
               "WHERE kind=? AND issuer_vat<>''")
        args: List[Any] = [kind]
        if vat:
            sql += " AND vat=?"
            args.append(vat)
        sql += " GROUP BY issuer_vat ORDER BY COUNT(*) DESC"
        return [{"issuer_vat": a, "name": b or "", "count": c, "total": round(float(d or 0), 2)}
                for a, b, c, d in conn.execute(sql, args)]
    finally:
        conn.close()


# --------------------------------------------------------------------------- Drive: snapshot / merge
SNAPSHOT_MAGIC = b"SMDINV1\n"
_META_TABLE_OK = "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)"


def drive_enabled() -> bool:
    """Το Drive κουβαλά την κρυπτογραφημένη invoices.db αντί για τα JSON (INVOICE_DRIVE_DB=0 -> όπως παλιά)."""
    return bool(_ENABLED and os.getenv("INVOICE_DRIVE_DB", "1").strip().lower() not in {"0", "false", "no", "off"})


def is_managed_rel(rel_path: str) -> bool:
    """Σχετική διαδρομή μέσα στην ομάδα (Drive/zip) που αντιστοιχεί σε αρχείο παραστατικών της βάσης."""
    rel = str(rel_path or "").replace("\\", "/")
    base = os.path.basename(rel)
    if rel.lower().startswith("epsilon/") and rel.count("/") == 1:
        return bool(_RE_EPS.match(base) or _RE_EXM.match(base))
    if "/" in rel:
        return False
    if base in SINGLETON_FILES:
        return True
    m = _RE_INV.match(base) or _RE_SUM.match(base)
    return bool(m and m.group(1).lower() != "epsilon")


def rel_key(rel_path: str) -> Optional[Tuple[str, str]]:
    """Σχετική διαδρομή (Drive/zip) -> (kind, vat) του store, ή None αν δεν διαχειρίζεται."""
    if not is_managed_rel(rel_path):
        return None
    rel = str(rel_path or "").replace("\\", "/")
    base = os.path.basename(rel)
    if rel.lower().startswith("epsilon/"):
        m = _RE_EPS.match(base)
        if m:
            return "epsilon", m.group(1)
        m = _RE_EXM.match(base)
        return ("exported_marks", m.group(1)) if m else None
    if base in SINGLETON_FILES:
        return SINGLETON_FILES[base], SINGLETON_VAT
    m = _RE_INV.match(base)
    if m:
        return "invoices", m.group(1)
    m = _RE_SUM.match(base)
    return ("summary", m.group(1)) if m else None


def store_updated_at(group_dir: str, kind: str, vat: str) -> float:
    """Ώρα του περιεχομένου ενός store της τοπικής βάσης (0 αν δεν υπάρχει)."""
    if not os.path.exists(db_path(group_dir)):
        return 0.0
    try:
        conn = _connect(group_dir)
        try:
            r = conn.execute("SELECT updated_at FROM stores WHERE kind=? AND vat=?", (kind, vat)).fetchone()
            return float(r[0] or 0) if r else 0.0
        finally:
            conn.close()
    except Exception:
        return 0.0


def snapshot_store_map(blob: bytes) -> Dict[Tuple[str, str], float]:
    """{(kind, vat): updated_at} ενός snapshot (bytes, όπως ανεβαίνει στο Drive) — τι ΠΕΡΙΕΧΕΙ η απομακρυσμένη βάση."""
    fd, tmp = tempfile.mkstemp(prefix="invoices_map_", suffix=".db")
    os.close(fd)
    try:
        extract_snapshot(blob, tmp)
        c = sqlite3.connect(tmp)
        try:
            return {(k, v): float(u or 0) for k, v, u in c.execute("SELECT kind, vat, updated_at FROM stores")}
        finally:
            c.close()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def get_meta(group_dir: str, key: str, default: str = "") -> str:
    if not os.path.exists(db_path(group_dir)):
        return default
    try:
        conn = _connect(group_dir)
        try:
            conn.execute(_META_TABLE_OK)
            r = conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
            return r[0] if r else default
        finally:
            conn.close()
    except Exception:
        return default


def set_meta(group_dir: str, key: str, value: str) -> None:
    try:
        conn = _connect(group_dir)
        try:
            conn.execute(_META_TABLE_OK)
            conn.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (key, str(value)))
        finally:
            conn.close()
    except Exception:
        log.exception("invoice_store.set_meta failed (%s)", key)


def fingerprint(group_dir: str) -> str:
    """Δακτυλικό αποτύπωμα του περιεχομένου (stores: πλήθος/έκδοση/ώρα) — αλλάζει με κάθε εγγραφή."""
    if not os.path.exists(db_path(group_dir)):
        return ""
    try:
        conn = _connect(group_dir)
        try:
            r = conn.execute("SELECT COUNT(*), COALESCE(SUM(version),0), COALESCE(MAX(updated_at),0), "
                             "COALESCE(SUM(n),0) FROM stores").fetchone()
            t = conn.execute("SELECT COUNT(*), COALESCE(MAX(deleted_at),0) FROM tombstones").fetchone()
            if not r or not (r[0] or (t and t[0])):
                return ""
            return f"{r[0]}:{r[1]}:{r[2]:.6f}:{r[3]}:{t[0]}:{t[1]:.6f}"
        finally:
            conn.close()
    except Exception:
        return ""


def push_due(group_dir: str) -> bool:
    """Υπάρχουν αλλαγές στη βάση που δεν έχουν ανέβει στο Drive;"""
    fp = fingerprint(group_dir)
    return bool(fp) and fp != get_meta(group_dir, "drive_pushed_fp", "")


def mark_pushed(group_dir: str, fp: str, mtime: float) -> None:
    set_meta(group_dir, "drive_pushed_fp", fp)
    set_meta(group_dir, "drive_merged_remote_mtime", f"{mtime:.6f}")


def last_modified(group_dir: str) -> float:
    try:
        conn = _connect(group_dir)
        try:
            r = conn.execute("SELECT COALESCE(MAX(updated_at),0) FROM stores").fetchone()
            return float(r[0] or 0)
        finally:
            conn.close()
    except Exception:
        return 0.0


def snapshot_bytes(group_dir: str) -> bytes:
    """Συνεπές snapshot της βάσης για το Drive: χωρίς ευρετήρια (ξαναφτιάχνονται από τον παραλήπτη), VACUUM και
    συμπίεση zlib (τα JSON συμπιέζονται ~8x). Μορφή: SNAPSHOT_MAGIC + zlib(sqlite)."""
    import zlib
    fd, tmp = tempfile.mkstemp(prefix="invoices_snap_", suffix=".db")
    os.close(fd)
    try:
        with _lock_for(group_dir):
            src = _connect(group_dir)
            try:
                dst = sqlite3.connect(tmp, isolation_level=None)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
        c = sqlite3.connect(tmp, isolation_level=None)
        try:
            for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL").fetchall():
                c.execute(f'DROP INDEX IF EXISTS "{name}"')
            c.execute("DELETE FROM meta")
            c.execute("UPDATE stores SET file_mtime_ns=0, file_size=-1, export_dirty=0")
            c.execute("PRAGMA journal_mode=DELETE")
            c.execute("VACUUM")
        finally:
            c.close()
        with open(tmp, "rb") as fh:
            return SNAPSHOT_MAGIC + zlib.compress(fh.read(), 6)
    finally:
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(tmp + suffix)
            except OSError:
                pass


def extract_snapshot(blob: bytes, dest_path: str) -> None:
    """Αντίστροφη του snapshot_bytes (δέχεται και σκέτη SQLite βάση)."""
    import zlib
    data = blob[len(SNAPSHOT_MAGIC):] if blob.startswith(SNAPSHOT_MAGIC) else None
    raw = zlib.decompress(data) if data is not None else blob
    if not raw.startswith(b"SQLite format 3"):
        raise ValueError("invoice_store: το snapshot δεν είναι βάση SQLite")
    with open(dest_path, "wb") as fh:
        fh.write(raw)


def merge_from(group_dir: str, other_db: str) -> Dict[str, int]:
    """Συγχωνεύει απομακρυσμένη βάση (από Drive) στην τοπική ΑΝΑ STORE: κερδίζει το νεότερο `updated_at`
    (ίδια σημασιολογία με το «τελευταίο αρχείο κερδίζει» των JSON). Τα store που αλλάζουν ξαναγράφονται και ως JSON
    export. Δεν σβήνει ποτέ τοπικά store που λείπουν από το Drive. Επιστρέφει στατιστικά."""
    st = {"remote": 0, "created": 0, "replaced": 0, "kept_local": 0, "failed": 0}
    parent = os.path.dirname(os.path.abspath(group_dir))
    # Πρώτα φέρνουμε τη βάση σε συμφωνία με τα τοπικά αρχεία (π.χ. JSON που άλλαξαν εξωτερικά).
    try:
        sync_group(group_dir, force=True, _root=parent)
    except Exception:
        log.exception("invoice_store.merge_from: pre-sync failed")
    other = sqlite3.connect(f"file:{other_db}?mode=ro", uri=True)
    try:
        remote = other.execute(
            "SELECT kind, vat, shape, raw, n, version, updated_at FROM stores").fetchall()
        try:
            r_tombs = {(k, v): float(d or 0) for k, v, d in other.execute("SELECT kind, vat, deleted_at FROM tombstones")}
        except sqlite3.OperationalError:
            r_tombs = {}              # snapshot παλαιότερης έκδοσης: χωρίς ταφόπλακες
        st["deleted"] = 0
        with _lock_for(group_dir):
            conn = _connect(group_dir)
            try:
                l_tombs = {(k, v): float(d or 0) for k, v, d in conn.execute("SELECT kind, vat, deleted_at FROM tombstones")}
                # Ταφόπλακες του Drive: σβήνουν τοπικό store που δεν έχει αλλάξει μετά τη διαγραφή.
                for (kind, vat), d in r_tombs.items():
                    if kind not in KINDS or not re.fullmatch(_VAT_TOKEN_RE, str(vat or "")):
                        continue
                    row = conn.execute("SELECT updated_at FROM stores WHERE kind=? AND vat=?", (kind, vat)).fetchone()
                    try:
                        if row is not None and float(row[0] or 0) <= d + 1e-6:
                            fp_ = file_path(group_dir, kind, vat)
                            _cache_drop(os.path.abspath(fp_))
                            try:
                                os.remove(fp_)
                            except FileNotFoundError:
                                pass
                            _drop_store(conn, kind, vat, when=d)
                            l_tombs[(kind, vat)] = d
                            st["deleted"] += 1
                        elif row is None and l_tombs.get((kind, vat), 0.0) < d:
                            conn.execute("INSERT OR REPLACE INTO tombstones (kind, vat, deleted_at) VALUES (?,?,?)",
                                         (kind, vat, d))
                            l_tombs[(kind, vat)] = d
                    except Exception:
                        log.exception("invoice_store.merge_from: tombstone %s/%s failed", kind, vat)
                        st["failed"] += 1
                for kind, vat, shape, raw, _n, _ver, r_upd in remote:
                    if kind not in KINDS or not re.fullmatch(_VAT_TOKEN_RE, str(vat or "")):
                        continue
                    st["remote"] += 1
                    _tomb = max(l_tombs.get((kind, vat), 0.0), r_tombs.get((kind, vat), 0.0))
                    if _tomb and _tomb >= float(r_upd or 0) - 1e-6:
                        st["kept_local"] += 1          # σβησμένο μετά την τελευταία αλλαγή του
                        continue
                    row = conn.execute("SELECT updated_at FROM stores WHERE kind=? AND vat=?", (kind, vat)).fetchone()
                    if row is not None and float(r_upd or 0) <= float(row[0] or 0) + 1e-6:
                        st["kept_local"] += 1
                        continue
                    try:
                        if shape == "raw":
                            obj = json.loads(raw) if raw is not None else None
                            texts, raw_text = [], (raw if raw is not None else "null")
                        else:
                            parts = [r[0] for r in other.execute(
                                "SELECT data FROM docs WHERE kind=? AND vat=? ORDER BY pos", (kind, vat))]
                            texts = parts
                            obj = [json.loads(t) for t in parts]
                            raw_text = None
                        path = file_path(group_dir, kind, vat)
                        text = _export_text(texts, raw_text)
                        _cache_drop(os.path.abspath(path))
                        _db_save(conn, kind, vat, obj, texts, raw_text, dirty=1,
                                 export=lambda p=path, t=text: _write_file(p, t), updated_at=float(r_upd or 0))
                        st["replaced" if row is not None else "created"] += 1
                    except Exception:
                        log.exception("invoice_store.merge_from: store %s/%s failed", kind, vat)
                        st["failed"] += 1
            finally:
                conn.close()
    finally:
        other.close()
    return st
