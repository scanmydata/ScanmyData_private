# -*- coding: utf-8 -*-
"""
activity_store.py — αποθήκευση του activity log της ομάδας σε SQLite (αντί για ένα τεράστιο activity.log).

Γιατί: το data/<ομάδα>/activity.log ήταν NDJSON που μεγάλωνε σε εκατοντάδες MB. Κάθε «τελευταία λήψη», admin
dashboard ή λίστα δραστηριότητας το διάβαζε ΟΛΟ γραμμή-γραμμή (λεπτά, κολλούσε τους workers). Εδώ κάθε εγγραφή
είναι μία γραμμή ευρετηριασμένη (χρόνος / ενέργεια / ΑΦΜ), άρα οι ερωτήσεις είναι άμεσες.

Σχεδιασμός:
  * ένα αρχείο ανά ομάδα: data/<φάκελος>/activity.db (WAL)· δεν συγχρονίζεται ως αρχείο στο Drive — η απομακρυσμένη
    «κυλιόμενη» αντιγραφή του log συνεχίζει να γράφεται από το drive_log_activity (αυτό είναι το off-site backup).
  * ΜΗ καταστροφική μετάβαση: το παλιό activity.log δεν σβήνεται/μετονομάζεται· εισάγεται (idempotent, με
    δακτυλικό αποτύπωμα ανά γραμμή) και ο δείκτης προόδου κρατιέται στον πίνακα meta.
  * Fallback: αν η εγγραφή στη βάση αποτύχει, η γραμμή γράφεται στο activity.log (και εισάγεται αργότερα).
  * Οι αναγνώστες χρησιμοποιούν τη βάση ΜΟΝΟ όταν η εισαγωγή του ιστορικού έχει ολοκληρωθεί (is_ready)·
    μέχρι τότε συνεχίζουν με την παλιά (ουρά αρχείου) ροή.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

DB_NAME = "activity.db"
LEGACY_NAME = "activity.log"
BATCH = 5000

_LOCKS: Dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_READY_CACHE: Dict[str, float] = {}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS activity (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    uid       TEXT NOT NULL UNIQUE,
    ts        TEXT,
    ts_epoch  REAL NOT NULL DEFAULT 0,
    action    TEXT,
    vat       TEXT,
    user_id   TEXT,
    raw       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity(ts_epoch);
CREATE INDEX IF NOT EXISTS idx_activity_action_vat ON activity(action, vat, ts_epoch);
CREATE INDEX IF NOT EXISTS idx_activity_vat ON activity(vat, ts_epoch);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def db_path(group_dir: str) -> str:
    return os.path.join(group_dir, DB_NAME)


def legacy_path(group_dir: str) -> str:
    return os.path.join(group_dir, LEGACY_NAME)


def _lock_for(path: str) -> threading.RLock:
    with _LOCKS_GUARD:
        lk = _LOCKS.get(path)
        if lk is None:
            lk = _LOCKS[path] = threading.RLock()
        return lk


def _connect(group_dir: str) -> sqlite3.Connection:
    os.makedirs(group_dir, exist_ok=True)
    conn = sqlite3.connect(db_path(group_dir), timeout=15, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    conn.executescript(_SCHEMA)
    return conn


# --------------------------------------------------------------------------- parsing
_BULK_RE = re.compile(
    r'Bulk fetch performed:\s*(?P<d1>\d{2}/\d{2}/\d{4})\s*to\s*(?P<d2>\d{2}/\d{2}/\d{4}),\s*VAT\s*(?P<vat>\d+),'
    r'\s*(?P<docs>\d+)\s*docs\s*\+\s*(?P<summaries>\d+)\s*summaries\s*by\s*(?P<by>.+)$')


def _parse_ts(ts: Any) -> float:
    if not ts:
        return 0.0
    s = str(ts).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        try:
            dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f%z")
        except Exception:
            return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _extract_vat(obj: Dict[str, Any]) -> str:
    """ΑΦΜ της εγγραφής — στο details.vat ή στο details.details.vat (και παραλλαγές), όπως το διάβαζε το παλιό σκανάρισμα."""
    d1 = obj.get("details") if isinstance(obj.get("details"), dict) else {}
    d2 = d1.get("details") if isinstance(d1.get("details"), dict) else {}
    for src in (d1, d2, obj):
        for k in ("vat", "client_vat", "afm", "πελατης", "client"):
            v = src.get(k) if isinstance(src, dict) else None
            if v and re.fullmatch(r"\d{8,9}", str(v).strip()):
                return str(v).strip()
    return ""


def parse_line(line: str) -> Optional[Dict[str, Any]]:
    """Γραμμή του activity.log → dict εγγραφής (JSON ή παλιά «TS - μήνυμα»)."""
    line = (line or "").strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    if " - " in line:
        ts_part, msg = line.split(" - ", 1)
        ts, msg = ts_part.strip(), msg.strip()
        m = _BULK_RE.search(msg)
        if m:
            return {"timestamp": ts, "action": "bulk_fetch_data", "details": {
                "date_from": m.group("d1"), "date_to": m.group("d2"), "client_vat": m.group("vat"),
                "client_label": m.group("vat"), "added_docs": int(m.group("docs") or 0),
                "added_summaries": int(m.group("summaries") or 0), "by": m.group("by").strip()}}
        return {"timestamp": ts, "action": "log_message", "details": {"message": msg}}
    return {"timestamp": "", "action": "log_message", "details": {"message": line}}


def _row_from_entry(obj: Dict[str, Any], raw_text: Optional[str] = None):
    raw = raw_text if raw_text is not None else json.dumps(obj, ensure_ascii=False, sort_keys=True)
    uid = hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()
    ts = obj.get("timestamp") or obj.get("ts") or ""
    return (uid, str(ts), _parse_ts(ts), str(obj.get("action") or ""), _extract_vat(obj),
            str(obj.get("user_id") or ""), raw)


# --------------------------------------------------------------------------- writes
def append(group_dir: str, entry: Dict[str, Any]) -> bool:
    """Γράφει μία εγγραφή. Επιστρέφει True αν μπήκε στη βάση· αλλιώς γράφεται στο activity.log (fallback)."""
    try:
        row = _row_from_entry(entry)
        with _lock_for(db_path(group_dir)):
            conn = _connect(group_dir)
            try:
                conn.execute("INSERT OR IGNORE INTO activity(uid, ts, ts_epoch, action, vat, user_id, raw) VALUES (?,?,?,?,?,?,?)", row)
            finally:
                conn.close()
        return True
    except Exception:
        log.exception("activity_store.append failed — falling back to activity.log")
        try:
            os.makedirs(group_dir, exist_ok=True)
            with open(legacy_path(group_dir), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass
        return False


# --------------------------------------------------------------------------- import (migration)
def _meta_get(conn, k: str, default: str = "") -> str:
    r = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def _meta_set(conn, k: str, v: str) -> None:
    conn.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def import_legacy(group_dir: str, path: Optional[str] = None, progress=None, max_seconds: Optional[float] = None,
                  source_id: str = "", default_action: str = "") -> Dict[str, Any]:
    """Εισάγει (συνεχίζοντας από εκεί που έμεινε) το παλιό activity.log στη βάση. Idempotent: ξανατρέξιμο δεν
    διπλασιάζει εγγραφές (UNIQUE uid) και ξεκινά από το αποθηκευμένο offset. Επιστρέφει στατιστικά."""
    path = path or legacy_path(group_dir)
    stats = {"path": path, "lines": 0, "inserted": 0, "skipped_bad": 0, "done": False, "offset": 0}
    if not os.path.exists(path):
        stats["done"] = True
        return stats
    t0 = time.time()
    # Το source_id επιτρέπει εισαγωγή ΞΕΝΟΥ αρχείου (π.χ. αντίγραφο από Drive) με δικό του offset, χωρίς να πειράξει
    # το offset του τοπικού activity.log.
    _tag = os.path.basename(path) + ((":" + source_id) if source_id else "")
    key = "import_offset:" + _tag
    with _lock_for(db_path(group_dir)):
        conn = _connect(group_dir)
        try:
            size = os.path.getsize(path)
            offset = int(_meta_get(conn, key, "0") or 0)
            if offset > size:       # το αρχείο κόπηκε/ξαναγράφτηκε (π.χ. κυλιόμενο): από την αρχή (το uid προστατεύει)
                offset = 0
            with open(path, "rb") as fh:
                fh.seek(offset)
                batch: List[tuple] = []
                pos = offset
                for raw_line in fh:
                    pos += len(raw_line)
                    if not raw_line.endswith(b"\n") and pos >= size:
                        pos -= len(raw_line)   # ημιτελής τελευταία γραμμή (γράφεται τώρα): θα ξαναδιαβαστεί
                        break
                    stats["lines"] += 1
                    line = raw_line.decode("utf-8", "ignore")
                    obj = parse_line(line)
                    if obj is None:
                        continue
                    if default_action and not obj.get("action"):
                        obj["action"] = default_action
                    try:
                        batch.append(_row_from_entry(obj, raw_text=line.strip()))
                    except Exception:
                        stats["skipped_bad"] += 1
                        continue
                    if len(batch) >= BATCH:
                        conn.execute("BEGIN")
                        cur = conn.executemany("INSERT OR IGNORE INTO activity(uid, ts, ts_epoch, action, vat, user_id, raw) VALUES (?,?,?,?,?,?,?)", batch)
                        stats["inserted"] += max(int(cur.rowcount or 0), 0)
                        _meta_set(conn, key, str(pos))
                        conn.execute("COMMIT")
                        batch = []
                        time.sleep(0.005)   # παραχώρηση CPU στα αιτήματα όσο τρέχει η μετάβαση
                        if progress:
                            try:
                                progress(stats)
                            except Exception:
                                pass
                        if max_seconds is not None and time.time() - t0 > max_seconds:
                            stats["offset"] = pos
                            return stats
                if batch:
                    conn.execute("BEGIN")
                    cur = conn.executemany("INSERT OR IGNORE INTO activity(uid, ts, ts_epoch, action, vat, user_id, raw) VALUES (?,?,?,?,?,?,?)", batch)
                    stats["inserted"] += max(int(cur.rowcount or 0), 0)
                    _meta_set(conn, key, str(pos))
                    conn.execute("COMMIT")
                else:
                    _meta_set(conn, key, str(pos))
                stats["offset"] = pos
            stats["done"] = True
            _meta_set(conn, "import_done:" + _tag, str(int(time.time())))
        finally:
            conn.close()
    _READY_CACHE.pop(group_dir, None)
    return stats


def is_ready(group_dir: str) -> bool:
    """Η βάση υπάρχει και το ιστορικό του activity.log έχει εισαχθεί (άρα οι ερωτήσεις είναι πλήρεις)."""
    now = time.time()
    c = _READY_CACHE.get(group_dir)
    if c is not None and now - c < 30:
        return True
    try:
        if not os.path.exists(db_path(group_dir)):
            if not os.path.exists(legacy_path(group_dir)):
                return False          # καμία πηγή: αφήνουμε τους αναγνώστες στην παλιά (κενή) ροή
            return False
        conn = _connect(group_dir)
        try:
            if os.path.exists(legacy_path(group_dir)) and not _meta_get(conn, "import_done:" + LEGACY_NAME):
                return False
            ok = True
        finally:
            conn.close()
        if ok:
            _READY_CACHE[group_dir] = now
        return ok
    except Exception:
        return False


def ensure_synced(group_dir: str, max_seconds: float = 5.0) -> None:
    """Αν το activity.log μεγάλωσε μετά την τελευταία εισαγωγή (π.χ. fallback εγγραφές, ή νέο αρχείο από pull
    του Drive) εισάγει τις νέες γραμμές. Φθηνό όταν δεν υπάρχει αλλαγή (σύγκριση μεγέθους με το offset)."""
    try:
        p = legacy_path(group_dir)
        if not os.path.exists(p):
            return
        conn = _connect(group_dir)
        try:
            off = int(_meta_get(conn, "import_offset:" + LEGACY_NAME, "0") or 0)
        finally:
            conn.close()
        if os.path.getsize(p) != off:
            import_legacy(group_dir, max_seconds=max_seconds)
    except Exception:
        log.exception("activity_store.ensure_synced failed")


def delete_range(group_dir: str, start_epoch: float, end_epoch: Optional[float] = None) -> int:
    """Σβήνει εγγραφές με χρόνο ≥ start_epoch (και ≤ end_epoch αν δοθεί). Επιστρέφει πόσες."""
    try:
        with _lock_for(db_path(group_dir)):
            conn = _connect(group_dir)
            try:
                if end_epoch is None:
                    cur = conn.execute("DELETE FROM activity WHERE ts_epoch>=? AND ts_epoch>0", (float(start_epoch),))
                else:
                    cur = conn.execute("DELETE FROM activity WHERE ts_epoch>=? AND ts_epoch<=? AND ts_epoch>0", (float(start_epoch), float(end_epoch)))
                return max(int(cur.rowcount or 0), 0)
            finally:
                conn.close()
    except Exception:
        log.exception("activity_store.delete_range failed")
        return 0


# --------------------------------------------------------------------------- reads
def recent(group_dir: str, limit: int = 100, offset: int = 0, action: Optional[str] = None,
           vat: Optional[str] = None) -> List[Dict[str, Any]]:
    """Οι πιο πρόσφατες εγγραφές (newest-first) ως dict (η αρχική εγγραφή)."""
    out: List[Dict[str, Any]] = []
    if not os.path.exists(db_path(group_dir)):
        return out
    try:
        conn = _connect(group_dir)
        try:
            q = "SELECT raw FROM activity"
            cond, args = [], []
            if action:
                cond.append("action=?"); args.append(action)
            if vat:
                cond.append("vat=?"); args.append(vat)
            if cond:
                q += " WHERE " + " AND ".join(cond)
            q += " ORDER BY ts_epoch DESC, id DESC LIMIT ? OFFSET ?"
            for (raw,) in conn.execute(q, (*args, int(limit), int(offset))):
                try:
                    out.append(json.loads(raw))
                except Exception:
                    pass
        finally:
            conn.close()
    except Exception:
        log.exception("activity_store.recent failed")
    return out


def last_ts(group_dir: str, vat: str, actions: Iterable[str]) -> Optional[datetime]:
    """Η πιο πρόσφατη εγγραφή με ΕΝΑ από τα actions για το ΑΦΜ (datetime UTC) ή None."""
    acts = [a for a in actions if a]
    if not acts or not vat or not os.path.exists(db_path(group_dir)):
        return None
    try:
        conn = _connect(group_dir)
        try:
            q = "SELECT MAX(ts_epoch) FROM activity WHERE vat=? AND action IN (%s) AND ts_epoch>0" % ",".join("?" * len(acts))
            r = conn.execute(q, (str(vat), *acts)).fetchone()
        finally:
            conn.close()
        if r and r[0]:
            return datetime.fromtimestamp(float(r[0]), tz=timezone.utc)
    except Exception:
        log.exception("activity_store.last_ts failed")
    return None


def count(group_dir: str) -> int:
    if not os.path.exists(db_path(group_dir)):
        return 0
    try:
        conn = _connect(group_dir)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return 0


def clear(group_dir: str) -> None:
    """Σβήνει όλες τις εγγραφές (admin «καθαρισμός logs»)."""
    try:
        with _lock_for(db_path(group_dir)):
            conn = _connect(group_dir)
            try:
                conn.execute("DELETE FROM activity")
                conn.execute("DELETE FROM meta WHERE k LIKE 'import_offset:%'")
            finally:
                conn.close()
        _READY_CACHE.pop(group_dir, None)
    except Exception:
        log.exception("activity_store.clear failed")


def migrate_all(data_root: str, progress=None, max_seconds_per_group: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    """Εισάγει το activity.log (και activity.log.jsonl αν υπάρχει) κάθε φακέλου ομάδας μέσα στο data_root."""
    res: Dict[str, Dict[str, Any]] = {}
    try:
        names = sorted(os.listdir(data_root))
    except OSError:
        return res
    for name in names:
        gdir = os.path.join(data_root, name)
        if not os.path.isdir(gdir) or name.startswith((".", "_")):
            continue
        rl = os.path.join(gdir, "e3_brain_retrieval_log.jsonl")
        has = os.path.exists(legacy_path(gdir)) or os.path.exists(os.path.join(gdir, "activity.log.jsonl")) or os.path.exists(rl)
        if not has:
            continue
        try:
            st = import_legacy(gdir, progress=progress, max_seconds=max_seconds_per_group)
            if os.path.exists(rl):
                import_legacy(gdir, path=rl, default_action="e3_brain_retrieval")
            jl = os.path.join(gdir, "activity.log.jsonl")
            if os.path.exists(jl):
                st2 = import_legacy(gdir, path=jl, progress=progress, max_seconds=max_seconds_per_group)
                st["inserted"] += st2.get("inserted", 0)
                st["lines"] += st2.get("lines", 0)
            res[name] = st
        except Exception:
            log.exception("activity_store.migrate_all failed for %s", name)
            res[name] = {"error": True}
    return res
