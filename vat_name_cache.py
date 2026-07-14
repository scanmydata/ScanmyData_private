"""
Shared AFM → company-name cache.

A single, global SQLite database (shared across ALL users and groups) that maps
a Greek AFM to a company name.  It is consulted *before* the external VAT
validator (VIES / Business Portal) so that repeated AFM→name lookups — very
common for receipts — are served instantly and without hammering the external
service.  The VAT validator remains the fallback for unknown AFMs, and any name
it (or a group client_db, or a scrape) resolves is written back here so the next
lookup — for any user, in any team — is free.

The store is intentionally decoupled from the Flask/SQLAlchemy models: it is a
standalone SQLite file with its own connection handling, so it works regardless
of request/app context and is genuinely shared process-wide.

Source trust ordering (higher wins on conflict):
    client_db          (3)  curated per-team accountant databases
    vies / business    (2)  official VAT registries
    scrape             (1)  names read off a document page
Lower-trust sources never overwrite a higher-trust name, but they do fill gaps.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import logging
import datetime
from typing import Optional, Dict, Iterable, Tuple

log = logging.getLogger(__name__)

# Global DB location. Overridable via env for tests / alternate deployments.
_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "vat_name_cache.db"
)
_DB_PATH = os.environ.get("VAT_NAME_CACHE_DB") or _DEFAULT_PATH

_lock = threading.Lock()
_initialized = False

# --- Background validation ("sub-agent") state ---------------------------------
import queue as _queue  # noqa: E402

_validation_queue: "_queue.Queue" = _queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()
# In-process dedupe so the same AFM is not re-validated repeatedly per run.
_validation_seen = set()
_validation_seen_lock = threading.Lock()


def _validation_enabled() -> bool:
    raw = str(os.environ.get("VAT_NAME_CACHE_VALIDATE", "1")).strip().lower()
    return raw in {"1", "true", "yes", "on"}

# Source trust levels.
_SOURCE_PRIORITY = {
    "client_db": 3,
    "vies": 2,
    "business_portal": 2,
    "vat_validator": 2,
    "scrape": 1,
    "unknown": 1,
}


def _source_priority(source: str) -> int:
    return _SOURCE_PRIORITY.get(str(source or "").strip().lower(), 1)


def _norm_afm(afm) -> Optional[str]:
    """Normalize to a 9-digit Greek AFM (last 9 digits, left-padded)."""
    if afm is None:
        return None
    d = re.sub(r"\D", "", str(afm))
    if not d:
        return None
    if len(d) > 9:
        d = d[-9:]
    d = d.zfill(9)
    return d if len(d) == 9 else None


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
    except Exception:
        pass
    return conn


def _ensure() -> bool:
    global _initialized
    if _initialized:
        return True
    with _lock:
        if _initialized:
            return True
        try:
            os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
            conn = _connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS vat_company_name (
                        afm           TEXT PRIMARY KEY,
                        name          TEXT NOT NULL,
                        source        TEXT,
                        priority      INTEGER NOT NULL DEFAULT 1,
                        validated     INTEGER NOT NULL DEFAULT 0,
                        validation_ts TEXT,
                        updated_at    TEXT
                    )
                    """
                )
                # Light migration for DBs created by an earlier version.
                existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(vat_company_name)").fetchall()}
                if "validated" not in existing_cols:
                    conn.execute("ALTER TABLE vat_company_name ADD COLUMN validated INTEGER NOT NULL DEFAULT 0")
                if "validation_ts" not in existing_cols:
                    conn.execute("ALTER TABLE vat_company_name ADD COLUMN validation_ts TEXT")
                conn.commit()
            finally:
                conn.close()
            _initialized = True
            return True
        except Exception:
            log.exception("vat_name_cache: failed to initialize DB at %s", _DB_PATH)
            return False


def lookup_name(afm) -> Optional[str]:
    """Return the cached company name for an AFM, or None if not present."""
    key = _norm_afm(afm)
    if not key:
        return None
    if not _ensure():
        return None
    try:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT name FROM vat_company_name WHERE afm=?", (key,)
            ).fetchone()
        finally:
            conn.close()
        if row and row[0] and str(row[0]).strip():
            return str(row[0]).strip()
    except Exception:
        log.exception("vat_name_cache: lookup failed for %s", key)
    return None


def store_name(afm, name, source: str = "unknown", validated: Optional[bool] = None) -> bool:
    """
    Insert or update the name for an AFM.

    A name is written when the AFM is new, or when the incoming source is at
    least as trusted as the stored one (so client_db/VIES refresh each other but
    a scraped name never clobbers a curated one).

    Low-trust sources (scrape/unknown) are additionally queued for background
    validation of the AFM↔name correspondence (see _validate_worker).
    """
    key = _norm_afm(afm)
    clean_name = (name or "").strip()
    if not key or not clean_name:
        return False
    if not _ensure():
        return False
    prio = _source_priority(source)
    # Authoritative sources (client_db / VIES / Business Portal) are trusted as-is;
    # scraped/unknown pairs stay unvalidated until the background worker confirms.
    is_validated = (prio >= 2) if validated is None else bool(validated)
    try:
        with _lock:
            conn = _connect()
            try:
                row = conn.execute(
                    "SELECT name, priority FROM vat_company_name WHERE afm=?", (key,)
                ).fetchone()
                if row is not None:
                    existing_prio = int(row[1] or 1)
                    # Keep the existing curated name if this source is less trusted.
                    if prio < existing_prio:
                        return False
                    # Same value, nothing to do.
                    if prio == existing_prio and str(row[0] or "").strip() == clean_name:
                        return False
                conn.execute(
                    """
                    INSERT INTO vat_company_name(afm, name, source, priority, validated, validation_ts, updated_at)
                    VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(afm) DO UPDATE SET
                        name=excluded.name,
                        source=excluded.source,
                        priority=excluded.priority,
                        validated=excluded.validated,
                        validation_ts=excluded.validation_ts,
                        updated_at=excluded.updated_at
                    """,
                    (
                        key,
                        clean_name,
                        str(source or "unknown"),
                        prio,
                        1 if is_validated else 0,
                        datetime.datetime.utcnow().isoformat(timespec="seconds") if is_validated else None,
                        datetime.datetime.utcnow().isoformat(timespec="seconds"),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        # Kick off background validation for unconfirmed scraped correspondences.
        if not is_validated:
            _enqueue_validation(key, clean_name)
        return True
    except Exception:
        log.exception("vat_name_cache: store failed for %s", key)
        return False


def bulk_store(pairs: Iterable[Tuple[str, str]], source: str = "client_db") -> int:
    """
    Cross-reference/seed the shared cache from a collection of (afm, name) pairs
    — e.g. a group's imported client_db.  Returns the number of rows written.
    """
    if not _ensure():
        return 0
    prio = _source_priority(source)
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    # Bulk sources are authoritative (client_db by default) → mark validated.
    is_validated = 1 if prio >= 2 else 0
    validation_ts = now if is_validated else None
    written = 0
    try:
        with _lock:
            conn = _connect()
            try:
                existing = {
                    r[0]: int(r[1] or 1)
                    for r in conn.execute(
                        "SELECT afm, priority FROM vat_company_name"
                    ).fetchall()
                }
                rows = []
                for afm, name in pairs:
                    key = _norm_afm(afm)
                    clean_name = (name or "").strip()
                    if not key or not clean_name:
                        continue
                    if key in existing and prio < existing[key]:
                        continue
                    rows.append((key, clean_name, str(source or "client_db"), prio, is_validated, validation_ts, now))
                if rows:
                    conn.executemany(
                        """
                        INSERT INTO vat_company_name(afm, name, source, priority, validated, validation_ts, updated_at)
                        VALUES(?,?,?,?,?,?,?)
                        ON CONFLICT(afm) DO UPDATE SET
                            name=excluded.name,
                            source=excluded.source,
                            priority=excluded.priority,
                            validated=excluded.validated,
                            validation_ts=excluded.validation_ts,
                            updated_at=excluded.updated_at
                        """,
                        rows,
                    )
                    conn.commit()
                    written = len(rows)
            finally:
                conn.close()
    except Exception:
        log.exception("vat_name_cache: bulk_store failed")
    return written


def store_from_client_map(client_map: Dict) -> int:
    """Convenience: seed the cache from a client_map produced by _load_client_map."""
    try:
        names = (client_map or {}).get("names") or {}
        if not isinstance(names, dict) or not names:
            return 0
        return bulk_store(names.items(), source="client_db")
    except Exception:
        log.exception("vat_name_cache: store_from_client_map failed")
        return 0


def db_path() -> str:
    """Absolute path of the backing SQLite file (for cloud backup)."""
    return _DB_PATH


def snapshot_to(dest_path: str) -> bool:
    """
    Write a consistent copy of the cache DB to ``dest_path`` using SQLite's
    online backup API. Safe to call while the cache is in use (WAL mode) — the
    snapshot is a coherent point-in-time image, unlike copying the raw file.
    Used to produce a clean artifact for cloud upload.
    """
    if not _ensure():
        return False
    try:
        with _lock:
            src = _connect()
            try:
                dst = sqlite3.connect(dest_path)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
        return True
    except Exception:
        log.exception("vat_name_cache: snapshot_to failed (%s)", dest_path)
        return False


def merge_rows(rows: Iterable[Tuple]) -> int:
    """
    Merge externally-sourced rows (e.g. pulled from the cloud copy) into the
    local cache WITHOUT losing local data. Conflict resolution mirrors the
    write path: higher ``priority`` wins; on equal priority a validated row
    beats an unvalidated one, then the newer ``updated_at`` wins. Returns the
    number of rows written.

    Each row is (afm, name, source, priority, validated, validation_ts, updated_at);
    missing trailing fields are tolerated.
    """
    if not _ensure():
        return 0
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    written = 0
    try:
        with _lock:
            conn = _connect()
            try:
                existing = {}
                for afm, prio, val, upd in conn.execute(
                    "SELECT afm, priority, validated, updated_at FROM vat_company_name"
                ).fetchall():
                    existing[afm] = (int(prio or 1), int(val or 0), str(upd or ""))

                to_write = []
                for row in rows:
                    try:
                        seq = list(row) + [None] * 7
                        afm, name, source, prio, val, vts, upd = seq[:7]
                    except Exception:
                        continue
                    key = _norm_afm(afm)
                    clean = (name or "").strip()
                    if not key or not clean:
                        continue
                    try:
                        prio = int(prio) if prio is not None else _source_priority(source)
                    except Exception:
                        prio = _source_priority(source)
                    val = 1 if val else 0
                    upd = str(upd or "") or now
                    cur = existing.get(key)
                    if cur is not None:
                        cprio, cval, cupd = cur
                        if prio < cprio:
                            continue
                        if prio == cprio:
                            newer = (val and not cval) or (upd > cupd)
                            if not newer:
                                continue
                    to_write.append((key, clean, str(source or "unknown"), prio, val, vts, upd))

                if to_write:
                    conn.executemany(
                        """
                        INSERT INTO vat_company_name(afm, name, source, priority, validated, validation_ts, updated_at)
                        VALUES(?,?,?,?,?,?,?)
                        ON CONFLICT(afm) DO UPDATE SET
                            name=excluded.name,
                            source=excluded.source,
                            priority=excluded.priority,
                            validated=excluded.validated,
                            validation_ts=excluded.validation_ts,
                            updated_at=excluded.updated_at
                        """,
                        to_write,
                    )
                    conn.commit()
                    written = len(to_write)
            finally:
                conn.close()
    except Exception:
        log.exception("vat_name_cache: merge_rows failed")
    return written


def merge_from_sqlite_file(path: str) -> int:
    """Merge all rows from another cache DB file (e.g. the cloud copy) into local."""
    if not path or not os.path.isfile(path):
        return 0
    try:
        src = sqlite3.connect(path, timeout=10)
        try:
            rows = src.execute(
                "SELECT afm, name, source, priority, validated, validation_ts, updated_at "
                "FROM vat_company_name"
            ).fetchall()
        finally:
            src.close()
    except Exception:
        log.exception("vat_name_cache: could not read remote DB %s", path)
        return 0
    return merge_rows(rows)


def stats() -> Dict[str, int]:
    """Basic counts, handy for diagnostics / admin views."""
    out = {"total": 0}
    if not _ensure():
        return out
    try:
        conn = _connect()
        try:
            out["total"] = int(
                conn.execute("SELECT COUNT(*) FROM vat_company_name").fetchone()[0]
            )
            for src, cnt in conn.execute(
                "SELECT source, COUNT(*) FROM vat_company_name GROUP BY source"
            ).fetchall():
                out[f"source_{src or 'unknown'}"] = int(cnt)
            try:
                out["unvalidated"] = int(
                    conn.execute("SELECT COUNT(*) FROM vat_company_name WHERE validated=0").fetchone()[0]
                )
            except Exception:
                pass
        finally:
            conn.close()
    except Exception:
        log.exception("vat_name_cache: stats failed")
    return out


# ---------------------------------------------------------------------------
# Background validation "sub-agent"
#
# Scraped AFM↔name pairs are written to the cache WITHOUT calling the external
# VAT validator (fast path).  A background daemon thread then confirms each such
# correspondence against VIES / Business Portal: when the registry resolves the
# AFM, we adopt the official name (authoritative spelling/script) and mark the
# entry validated; otherwise the scraped name is kept as the best available.
# ---------------------------------------------------------------------------

def _mark_validation_result(afm: str, official_name: Optional[str]) -> None:
    """Apply a background validation outcome to the stored row."""
    key = _norm_afm(afm)
    if not key or not _ensure():
        return
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    official = (official_name or "").strip()
    try:
        with _lock:
            conn = _connect()
            try:
                if official:
                    # Registry resolved the AFM → adopt the official name and
                    # promote the entry to a validator-grade, validated record.
                    conn.execute(
                        """
                        UPDATE vat_company_name
                        SET name=?, source='vat_validator', priority=?,
                            validated=1, validation_ts=?, updated_at=?
                        WHERE afm=?
                        """,
                        (official, _source_priority("vat_validator"), now, now, key),
                    )
                else:
                    # No registry hit: record the attempt, keep the scraped name.
                    conn.execute(
                        "UPDATE vat_company_name SET validation_ts=? WHERE afm=?",
                        (now, key),
                    )
                conn.commit()
            finally:
                conn.close()
    except Exception:
        log.exception("vat_name_cache: failed to apply validation result for %s", key)


def _validate_one(afm: str) -> None:
    key = _norm_afm(afm)
    if not key:
        return
    official_name = None
    try:
        from vat_validator import validate_greek_vat  # lazy import (avoids cycles)
        result = validate_greek_vat(key) or {}
        if result.get("valid") and result.get("name"):
            official_name = str(result["name"]).strip()
    except Exception:
        log.exception("vat_name_cache: background validation call failed for %s", key)
        return
    _mark_validation_result(key, official_name)


def _validate_worker() -> None:
    # One-time startup sweep: re-queue any unvalidated rows left from prior runs.
    try:
        _enqueue_pending_unvalidated()
    except Exception:
        log.exception("vat_name_cache: startup unvalidated sweep failed")
    while True:
        try:
            item = _validation_queue.get()
        except Exception:
            return
        try:
            if item is None:
                continue
            afm, _name = item
            _validate_one(afm)
            # Be gentle with the external registry (serial worker already
            # rate-limits, but add a small spacing between calls).
            try:
                import time as _time
                _time.sleep(float(os.environ.get("VAT_NAME_CACHE_VALIDATE_DELAY", "1.5")))
            except Exception:
                pass
        except Exception:
            log.exception("vat_name_cache: validation worker iteration failed")
        finally:
            try:
                _validation_queue.task_done()
            except Exception:
                pass


def _ensure_worker() -> None:
    global _worker_started
    if _worker_started or not _validation_enabled():
        return
    with _worker_lock:
        if _worker_started:
            return
        try:
            t = threading.Thread(target=_validate_worker, name="vat-name-validator", daemon=True)
            t.start()
            _worker_started = True
        except Exception:
            log.exception("vat_name_cache: failed to start validation worker")


def _enqueue_validation(afm: str, name: str) -> None:
    key = _norm_afm(afm)
    if not key or not _validation_enabled():
        return
    with _validation_seen_lock:
        if key in _validation_seen:
            return
        _validation_seen.add(key)
    try:
        _ensure_worker()
        _validation_queue.put((key, name or ""))
    except Exception:
        log.exception("vat_name_cache: failed to enqueue validation for %s", key)


def _enqueue_pending_unvalidated(limit: int = 5000) -> int:
    """Queue unvalidated scraped rows (e.g. after a restart) for validation."""
    if not _ensure():
        return 0
    queued = 0
    try:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT afm, name FROM vat_company_name WHERE validated=0 ORDER BY updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        finally:
            conn.close()
        for afm, name in rows:
            key = _norm_afm(afm)
            if not key:
                continue
            with _validation_seen_lock:
                if key in _validation_seen:
                    continue
                _validation_seen.add(key)
            _validation_queue.put((key, name or ""))
            queued += 1
    except Exception:
        log.exception("vat_name_cache: failed to enqueue pending unvalidated rows")
    return queued
