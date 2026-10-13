# -*- coding: utf-8 -*-
"""
app_log_store.py — τα logs της ΕΦΑΡΜΟΓΗΣ (Python logging) σε SQLite αντί για `data/activity.log` / `data/error.log`.

  * SQLiteLogHandler: logging.Handler που βάζει τις εγγραφές σε ουρά και ένα background thread τις γράφει σε
    batches στο data/app_log.db (WAL). Ποτέ δεν μπλοκάρει το αίτημα και δεν πετά εξαιρέσεις.
  * Ευρετήρια σε χρόνο/επίπεδο, άρα «τελευταία σφάλματα», γραφήματα ανά ώρα κ.λπ. είναι άμεσα (όχι ανάγνωση
    αρχείου των MB).
  * Retention: purge_older_than(days) (προεπιλογή 90 ημέρες) — τρέχει στην εκκίνηση και μία φορά το 24ωρο.
  * Μετάβαση: import_text_logs() εισάγει (idempotent) τα παλιά αρχεία κειμένου (και τα περιστρεφόμενα .1, .2…).
  * Το Console handler μένει πάντα (stdout/docker logs) ως δεύτερο δίκτυ ασφαλείας.
"""
from __future__ import annotations

import hashlib
import logging
import os
import queue
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

_DB_NAME = "app_log.db"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    uid       TEXT UNIQUE,
    ts        TEXT,
    ts_epoch  REAL NOT NULL,
    level     TEXT,
    levelno   INTEGER,
    logger    TEXT,
    message   TEXT,
    exc_text  TEXT,
    grp       TEXT DEFAULT '',
    source    TEXT DEFAULT 'app'
);
CREATE INDEX IF NOT EXISTS idx_app_log_ts ON app_log(ts_epoch);
CREATE INDEX IF NOT EXISTS idx_app_log_level ON app_log(levelno, ts_epoch);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""
_LEVELNO = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "WARN": 30, "ERROR": 40, "CRITICAL": 50, "FATAL": 50}
_LINE_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:[+-]\d{2}:?\d{2}|Z)?)\s+(?P<lvl>[A-Z]+)\s+(?P<msg>.*)$")

_internal = logging.getLogger("app_log_store")
_internal.propagate = False
_internal.addHandler(logging.NullHandler())


def default_db_path(data_dir: str) -> str:
    return os.path.join(data_dir, _DB_NAME)


def _connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    conn.executescript(_SCHEMA)
    return conn


def _parse_ts(ts: str) -> float:
    s = (ts or "").strip().replace(",", ".")
    if not s:
        return 0.0
    s = s.replace(" ", "T", 1) if "T" not in s else s
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)       # +0300 -> +03:00
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# --------------------------------------------------------------------------- handler
class SQLiteLogHandler(logging.Handler):
    """Εγγραφή logging records σε SQLite μέσω ουράς + background writer."""

    def __init__(self, db_path: str, level: int = logging.INFO, tz=None, flush_every: float = 0.5, max_queue: int = 20000):
        super().__init__(level)
        self.db_path = db_path
        self._tz = tz
        self._q: "queue.Queue[tuple]" = queue.Queue(maxsize=max_queue)
        self._flush_every = flush_every
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="app-log-writer", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            exc = ""
            if record.exc_info:
                exc = logging.Formatter().formatException(record.exc_info)
            elif record.exc_text:
                exc = record.exc_text
            tz = self._tz or timezone.utc
            ts = datetime.fromtimestamp(record.created, tz).isoformat(timespec="milliseconds")
            self._q.put_nowait((ts, float(record.created), record.levelname, int(record.levelno),
                                record.name, msg, exc))
        except queue.Full:
            pass
        except Exception:
            pass

    def _run(self) -> None:
        conn = None
        while True:
            batch: List[tuple] = []
            try:
                batch.append(self._q.get(timeout=self._flush_every))
                while len(batch) < 500:
                    batch.append(self._q.get_nowait())
            except queue.Empty:
                pass
            if batch:
                try:
                    if conn is None:
                        conn = _connect(self.db_path)
                    conn.execute("BEGIN")
                    conn.executemany(
                        "INSERT INTO app_log(uid, ts, ts_epoch, level, levelno, logger, message, exc_text) VALUES (NULL,?,?,?,?,?,?,?)",
                        batch)
                    conn.execute("COMMIT")
                except Exception:
                    try:
                        if conn is not None:
                            conn.execute("ROLLBACK")
                    except Exception:
                        pass
                    try:
                        conn = None   # επανασύνδεση στην επόμενη παρτίδα
                    except Exception:
                        pass
            if self._stop.is_set() and self._q.empty():
                break

    def close(self) -> None:
        self._stop.set()
        try:
            self._thread.join(timeout=2)
        except Exception:
            pass
        super().close()


# --------------------------------------------------------------------------- queries / maintenance
def recent(db_path: str, limit: int = 200, min_level: str = "INFO", since_epoch: Optional[float] = None,
           contains: Optional[str] = None) -> List[Dict[str, Any]]:
    if not os.path.exists(db_path):
        return []
    out: List[Dict[str, Any]] = []
    conn = _connect(db_path)
    try:
        q = "SELECT ts, level, logger, message, exc_text, grp, source FROM app_log WHERE levelno>=?"
        args: List[Any] = [_LEVELNO.get(min_level.upper(), 20)]
        if since_epoch:
            q += " AND ts_epoch>=?"; args.append(float(since_epoch))
        if contains:
            q += " AND message LIKE ?"; args.append("%" + contains + "%")
        q += " ORDER BY ts_epoch DESC, id DESC LIMIT ?"; args.append(int(limit))
        for ts, lvl, lg, msg, exc, grp, src in conn.execute(q, args):
            out.append({"ts": ts, "level": lvl, "logger": lg, "message": msg, "exc": exc, "group": grp, "source": src})
    finally:
        conn.close()
    return out


def firebase_read_hourly(db_path: str, start_epoch: Optional[float] = None, end_epoch: Optional[float] = None) -> Dict[str, int]:
    """Άθροισμα size=<bytes> των γραμμών «firebase.read» ανά ώρα (UTC) — για το admin γράφημα."""
    buckets: Dict[str, int] = {}
    if not os.path.exists(db_path):
        return buckets
    conn = _connect(db_path)
    try:
        q = "SELECT ts_epoch, message FROM app_log WHERE message LIKE '%firebase.read%'"
        args: List[Any] = []
        if start_epoch:
            q += " AND ts_epoch>=?"; args.append(float(start_epoch))
        if end_epoch:
            q += " AND ts_epoch<=?"; args.append(float(end_epoch))
        for ep, msg in conn.execute(q, args):
            m = re.search(r"size=(\d+)", msg or "")
            if not m:
                continue
            b = datetime.fromtimestamp(ep, timezone.utc).strftime("%Y-%m-%d %H:00")
            buckets[b] = buckets.get(b, 0) + int(m.group(1))
    finally:
        conn.close()
    return buckets


def purge_older_than(db_path: str, days: int = 90) -> int:
    if not os.path.exists(db_path):
        return 0
    cutoff = time.time() - days * 86400
    conn = _connect(db_path)
    try:
        cur = conn.execute("DELETE FROM app_log WHERE ts_epoch < ? AND ts_epoch > 0", (cutoff,))
        n = max(int(cur.rowcount or 0), 0)
        if n > 10000:
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
        return n
    finally:
        conn.close()


def import_text_logs(db_path: str, paths: Iterable[str], source: str = "app", grp: str = "") -> Dict[str, Any]:
    """Εισάγει αρχεία κειμένου logging (μορφή «YYYY-MM-DD HH:MM:SS+0300 LEVEL μήνυμα», με συνέχεια traceback)
    στο app_log. Idempotent: μοναδικό uid ανά (πηγή, περιεχόμενο γραμμής, αριθμός εμφάνισης)."""
    stats = {"files": 0, "lines": 0, "inserted": 0, "unparsed": 0}
    conn = _connect(db_path)
    try:
        for path in paths:
            if not os.path.isfile(path):
                continue
            stats["files"] += 1
            seen: Dict[str, int] = {}
            batch: List[tuple] = []
            cur_rec: Optional[List[Any]] = None   # [ts, ts_epoch, level, levelno, logger, message, exc]

            def flush_rec():
                nonlocal cur_rec
                if cur_rec is None:
                    return
                ts, ep, lvl, lno, lg, msg, exc = cur_rec
                key = f"{source}|{grp}|{ts}|{lvl}|{msg}"
                n = seen.get(key, 0); seen[key] = n + 1
                uid = hashlib.sha1(f"{key}#{n}".encode("utf-8", "ignore")).hexdigest()
                batch.append((uid, ts, ep, lvl, lno, lg, msg, exc, grp, source))
                cur_rec = None

            with open(path, "rb") as fh:
                for raw in fh:
                    line = raw.decode("utf-8", "ignore").rstrip("\r\n")
                    stats["lines"] += 1
                    m = _LINE_RE.match(line)
                    if m and m.group("lvl") in _LEVELNO:
                        flush_rec()
                        ts = m.group("ts")
                        cur_rec = [ts, _parse_ts(ts), m.group("lvl"), _LEVELNO[m.group("lvl")], "app", m.group("msg"), ""]
                    elif cur_rec is not None:
                        cur_rec[6] = (cur_rec[6] + "\n" + line) if cur_rec[6] else line
                    elif line.strip():
                        stats["unparsed"] += 1
                    if len(batch) >= 5000:
                        flush_rec()
                        conn.execute("BEGIN")
                        cur = conn.executemany(
                            "INSERT OR IGNORE INTO app_log(uid, ts, ts_epoch, level, levelno, logger, message, exc_text, grp, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            batch)
                        stats["inserted"] += max(int(cur.rowcount or 0), 0)
                        conn.execute("COMMIT")
                        batch = []
                flush_rec()
            if batch:
                conn.execute("BEGIN")
                cur = conn.executemany(
                    "INSERT OR IGNORE INTO app_log(uid, ts, ts_epoch, level, levelno, logger, message, exc_text, grp, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    batch)
                stats["inserted"] += max(int(cur.rowcount or 0), 0)
                conn.execute("COMMIT")
    finally:
        conn.close()
    return stats


def import_if_changed(db_path: str, paths: Iterable[str], source: str = "app", grp: str = "") -> Dict[str, Any]:
    """import_text_logs μόνο για αρχεία που άλλαξαν από την τελευταία εισαγωγή (μέγεθος+mtime στον πίνακα meta)."""
    todo = []
    sigs = {}
    conn = _connect(db_path)
    try:
        for p in paths:
            if not os.path.isfile(p):
                continue
            st = os.stat(p)
            sig = f"{st.st_size}:{int(st.st_mtime)}"
            row = conn.execute("SELECT v FROM meta WHERE k=?", (f"imported:{source}:{grp}:{os.path.abspath(p)}",)).fetchone()
            if row and row[0] == sig:
                continue
            todo.append(p)
            sigs[p] = sig
    finally:
        conn.close()
    stats = import_text_logs(db_path, todo, source=source, grp=grp) if todo else {"files": 0, "lines": 0, "inserted": 0, "unparsed": 0}
    if todo:
        conn = _connect(db_path)
        try:
            for p, sig in sigs.items():
                conn.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                             (f"imported:{source}:{grp}:{os.path.abspath(p)}", sig))
        finally:
            conn.close()
    return stats


def rotated_family(base_path: str) -> List[str]:
    """base, base.1, base.2 … (παλαιότερα πρώτα)."""
    out = []
    d = os.path.dirname(base_path) or "."
    b = os.path.basename(base_path)
    try:
        for n in os.listdir(d):
            if n == b or re.fullmatch(re.escape(b) + r"\.\d+", n):
                out.append(os.path.join(d, n))
    except OSError:
        pass
    def k(p):
        m = re.search(r"\.(\d+)$", p)
        return -int(m.group(1)) if m else 1
    return sorted(out, key=k)
