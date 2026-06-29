"""Startup data warmup + readiness gate for the Google Drive storage backend.

On every server start / redeploy we proactively pull each group's ``data/``
subfolder down from Google Drive *before* allowing logins. While that runs the
app serves a maintenance page and blocks login. Once the pull completes the
gate opens.

Design goals
------------
* **Universal / multi-worker safe.** gunicorn may run N workers (Dockerfile
  uses 1, start.sh uses 4). The warmup state lives in a JSON marker file under
  ``data/`` so every worker shares the same readiness, and a leader-election
  lock file ensures only ONE worker actually performs the Drive pull while the
  others just follow the shared state.
* **Fail-open.** If the warmup stalls or errors (Drive rate-limit, network,
  leader crash) the gate auto-unblocks after ``DRIVE_WARMUP_MAX_SECONDS`` so a
  Drive outage can never permanently brick the server. A warning is logged.
* **Smart pull.** Uses the existing smart-sync (mtime based) pull so frequent
  restarts are cheap — only new/changed remote files are fetched.

It also exposes a small background job runner reused by the admin panel for
manual push/pull and an up-to-date diff against Drive.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
def _data_root() -> str:
    return os.path.join(os.getcwd(), "data")


_WARMUP_STATE_FILE = ".drive_warmup_state.json"
_WARMUP_LOCK_FILE = ".drive_warmup.lock"
_MANUAL_JOB_FILE = ".drive_sync_job.json"


def _max_seconds() -> float:
    try:
        return float(os.getenv("DRIVE_WARMUP_MAX_SECONDS", "300"))
    except Exception:
        return 300.0


# Stale-leader takeover: if the lock heartbeat is older than this, another
# worker may steal the lock (the original leader presumably crashed).
_LOCK_STALE_SECONDS = 90.0

# Process start time — ultimate fail-open reference even if warmup never starts.
_PROC_START = time.time()

_proc_lock = threading.Lock()
_started_local = False

# Tiny in-process cache so the before_request gate doesn't hit the disk on
# every single request.
_state_cache: Dict[str, Any] = {"data": None, "read_at": 0.0}
_STATE_CACHE_TTL = 1.0


# ---------------------------------------------------------------------------
# State file helpers
# ---------------------------------------------------------------------------
def _state_path() -> str:
    return os.path.join(_data_root(), _WARMUP_STATE_FILE)


def _lock_path() -> str:
    return os.path.join(_data_root(), _WARMUP_LOCK_FILE)


def _read_state_raw() -> Optional[Dict[str, Any]]:
    try:
        with open(_state_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _read_state_cached() -> Optional[Dict[str, Any]]:
    now = time.time()
    if _state_cache["data"] is not None and (now - _state_cache["read_at"]) < _STATE_CACHE_TTL:
        return _state_cache["data"]
    data = _read_state_raw()
    _state_cache["data"] = data
    _state_cache["read_at"] = now
    return data


def _write_state(state: Dict[str, Any]) -> None:
    state["updated_at"] = time.time()
    try:
        os.makedirs(_data_root(), exist_ok=True)
        p = _state_path()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
        os.replace(tmp, p)
        # refresh cache immediately for this worker
        _state_cache["data"] = state
        _state_cache["read_at"] = time.time()
    except Exception as e:
        logger.debug("warmup: could not write state: %s", e)


# ---------------------------------------------------------------------------
# Readiness — consulted by the before_request gate on every request
# ---------------------------------------------------------------------------
def is_ready() -> bool:
    """True if the app may accept logins / normal traffic.

    Ready when: warmup finished, Drive backend disabled, or fail-open (timeout
    / error / leader never started). Designed to never block forever.
    """
    # Ultimate fail-open: regardless of any state, never gate longer than the
    # configured maximum from process start.
    if (time.time() - _PROC_START) > _max_seconds():
        return True

    state = _read_state_cached()
    if not state:
        # No state yet — warmup hasn't written anything. Keep gating until the
        # fail-open window above elapses.
        return False

    status = str(state.get("status") or "")
    if status in ("ready", "disabled"):
        return True
    if state.get("fail_open"):
        return True
    if status == "error":
        return True
    # status == 'syncing' — honour the per-warmup timeout too (covers a leader
    # that started but stalled; followers read started_at from the file).
    started = float(state.get("started_at") or 0)
    if started and (time.time() - started) > _max_seconds():
        return True
    return False


def get_public_state() -> Dict[str, Any]:
    """Safe snapshot for the maintenance page / readiness API."""
    state = _read_state_cached() or {}
    total = int(state.get("total") or 0)
    done = int(state.get("done") or 0)
    pct = int((done / total) * 100) if total > 0 else (100 if is_ready() else 0)
    return {
        "ready": is_ready(),
        "status": str(state.get("status") or "starting"),
        "total": total,
        "done": done,
        "percent": pct,
        "current_group": state.get("current_group"),
        "error": state.get("error"),
        "fail_open": bool(state.get("fail_open")),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "updated_at": state.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# Leader election (cross-process)
# ---------------------------------------------------------------------------
def _try_become_leader() -> bool:
    """Atomically claim the warmup lock. Returns True if this process leads."""
    lock = _lock_path()
    try:
        os.makedirs(_data_root(), exist_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()}@{time.time()}".encode("utf-8"))
        os.close(fd)
        return True
    except FileExistsError:
        # Someone holds it. Steal if stale (leader likely crashed).
        try:
            age = time.time() - os.path.getmtime(lock)
            state = _read_state_raw() or {}
            if state.get("status") in ("ready", "disabled"):
                return False
            if age > _LOCK_STALE_SECONDS:
                logger.warning("warmup: stale lock (age=%.0fs) — taking over", age)
                try:
                    os.remove(lock)
                except Exception:
                    pass
                return _try_become_leader()
        except Exception:
            pass
        return False
    except Exception as e:
        logger.debug("warmup: lock attempt failed: %s", e)
        return False


def _release_leader() -> None:
    try:
        os.remove(_lock_path())
    except Exception:
        pass


def _touch_lock() -> None:
    try:
        os.utime(_lock_path(), None)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Warmup entry point
# ---------------------------------------------------------------------------
def ensure_started(app) -> None:
    """Kick off the warmup once per process (idempotent)."""
    global _started_local
    with _proc_lock:
        if _started_local:
            return
        _started_local = True
    t = threading.Thread(target=_warmup_entry, args=(app,), daemon=True, name="drive-warmup")
    t.start()


def _drive_backend_active(app=None) -> bool:
    # The active backend is read from Setting in the DB, which needs a Flask
    # app context. Outside a request (e.g. the warmup thread) we must establish
    # one, otherwise the lookup silently falls back to the 'firebase' default.
    try:
        from firebase import firebase_config
        if app is not None:
            with app.app_context():
                return firebase_config._drive_backend_active()
        return firebase_config._drive_backend_active()
    except Exception:
        return False


def _list_group_folders(app) -> List[str]:
    folders: List[str] = []
    try:
        with app.app_context():
            from models import Group
            for g in Group.query.all():
                folder = (getattr(g, "data_folder", "") or "").strip() or (getattr(g, "name", "") or "").strip()
                if folder and folder not in folders:
                    folders.append(folder)
    except Exception as e:
        logger.warning("warmup: could not enumerate groups: %s", e)
    return folders


def _remote_backend_and_ready(app):
    """Return (backend, ready). backend is 'drive'|'firebase'|'none'.

    Works for both storage backends — the warmup reconcile uses the unified
    dispatcher, so it is not Drive-specific.
    """
    try:
        from firebase import firebase_config as fc
        with app.app_context():
            backend = fc._get_storage_backend()
        if backend == "drive":
            from firebase import drive_storage as ds
            return "drive", bool(ds.is_drive_enabled() or ds.init_drive())
        return "firebase", bool(fc.is_firebase_enabled())
    except Exception as e:
        logger.warning("warmup: backend probe failed: %s", e)
        return "none", False


def _warmup_entry(app) -> None:
    # If no remote backend is usable there is nothing to reconcile.
    backend, ready = _remote_backend_and_ready(app)
    if not ready:
        _write_state({"status": "disabled", "reason": f"{backend}_unavailable",
                      "started_at": time.time(), "finished_at": time.time(),
                      "total": 0, "done": 0})
        logger.info("warmup: remote backend '%s' unavailable — gate open immediately", backend)
        return

    if not _try_become_leader():
        logger.info("warmup: another worker leads the warmup; this worker follows shared state")
        return

    # Won the lock, but a previous leader may have already finished (it releases
    # the lock on completion). Don't re-run and re-gate in that case.
    prev = _read_state_raw() or {}
    if prev.get("status") in ("ready", "disabled"):
        _release_leader()
        return

    try:
        _run_warmup_as_leader(app, backend)
    except Exception as e:
        # Any unexpected crash (e.g. missing google-api-python-client) must
        # fail open, never leave the gate stuck on 'pulling'.
        logger.exception("warmup: leader crashed — gate fail-open")
        _patch_state(status="error", fail_open=True, error=str(e),
                     finished_at=time.time())
    finally:
        _release_leader()


def _reset_all_presence(app) -> None:
    """Clear stale presence/session claims left over from the previous process.

    A freshly started process has zero live client connections, yet the DB still
    holds ``last_active_at`` / ``current_session_id`` from just before the
    restart. Without this reset every user whose ``last_active_at`` is within
    ``SESSION_TIMEOUT`` (5 min) wrongly shows as *online* in the admin panel and
    is blocked from logging back in ("already active on another device") until
    the timestamp ages out. This is the automatic equivalent of the manual
    ``scripts/clear_all_sessions.py``.

    The final session's elapsed time is folded into ``total_active_seconds`` so
    the usage stats stay accurate instead of silently dropping the last session.
    """
    try:
        with app.app_context():
            from models import db, User
            cleared = 0
            stale = User.query.filter(
                (User.current_session_id.isnot(None))
                | (User.session_started_at.isnot(None))
                | (User.last_active_at.isnot(None))
            ).all()
            for user in stale:
                try:
                    if user.session_started_at and user.last_active_at:
                        dur = int((user.last_active_at - user.session_started_at).total_seconds())
                        if dur > 0:
                            user.total_active_seconds = int(user.total_active_seconds or 0) + dur
                except Exception:
                    pass
                user.current_session_id = None
                user.session_started_at = None
                user.last_active_at = None
                cleared += 1
            if cleared:
                db.session.commit()
            logger.info("warmup: reset presence for %d stale session(s) on startup", cleared)
    except Exception as e:
        logger.warning("warmup: presence reset failed: %s", e)
        try:
            with app.app_context():
                from models import db
                db.session.rollback()
        except Exception:
            pass


def _run_warmup_as_leader(app, backend: str) -> None:
    """Reconcile every group's local data/ folder with the remote backend.

    Per group we ask `compare_group_payload_freshness` for the authoritative
    direction and act on it:
      * 'push'  -> server has newer data, back it up to the remote
      * 'pull'  -> remote has newer data (or local is empty), fetch it
      * 'equal'/'noop' -> nothing to do (fast path)
    This means a server that holds newer data than the remote is NOT clobbered
    — it wins and is pushed up instead.
    """
    from firebase import firebase_config as fc

    # Clear presence/session claims left over from the previous process before
    # we open the gate. Runs exactly once per deploy (leader only) while logins
    # are still blocked, so it can never race a fresh login.
    _reset_all_presence(app)

    started = time.time()
    _write_state({"status": "syncing", "phase": "warmup", "started_at": started,
                  "total": 0, "done": 0, "current_group": None,
                  "backend": backend, "leader_pid": os.getpid(), "groups": []})

    folders = _list_group_folders(app)
    total = len(folders)
    groups_state: List[Dict[str, Any]] = [{"folder": f, "status": "pending"} for f in folders]
    _write_state({"status": "syncing", "phase": "warmup", "started_at": started,
                  "total": total, "done": 0, "current_group": None,
                  "backend": backend, "leader_pid": os.getpid(), "groups": groups_state})

    done = 0
    for entry in groups_state:
        folder = entry["folder"]
        # Fail-open guard: if we blow past the budget, stop blocking — the
        # remaining groups reconcile lazily on first login anyway.
        if (time.time() - started) > _max_seconds():
            logger.warning("warmup: exceeded %.0fs budget at %d/%d — opening gate, "
                            "remaining groups reconcile lazily", _max_seconds(), done, total)
            _patch_state(status="ready", fail_open=True, done=done,
                         current_group=None, finished_at=time.time(),
                         note="timeout_partial")
            return
        try:
            _patch_state(current_group=folder)
            with app.app_context():
                fresh = fc.compare_group_payload_freshness(folder, local_group_folder=folder) or {}
                action = str(fresh.get("action") or "")
                if action == "push":
                    fc.firebase_push_group_files(folder, local_group_folder=folder, force=False)
                elif action in ("equal", "noop"):
                    pass  # already in sync — fast path
                else:
                    # 'pull' / 'unknown' / anything else -> fetch remote (smart,
                    # never overwrites a locally-newer file).
                    fc.firebase_pull_group_to_local(folder, local_group_folder=folder, force=False)
            entry["status"] = "ok"
            entry["action"] = action or "pull"
        except Exception as e:
            entry["status"] = "fail"
            entry["error"] = str(e)
            logger.warning("warmup: reconcile failed for %s: %s", folder, e)
        done += 1
        _patch_state(done=done, groups=groups_state)
        _touch_lock()

    _patch_state(status="ready", done=done, current_group=None,
                 finished_at=time.time(), groups=groups_state)
    logger.info("warmup: complete — %d/%d groups reconciled, gate open", done, total)


def _patch_state(**changes) -> None:
    state = _read_state_raw() or {}
    state.update(changes)
    _write_state(state)


# ---------------------------------------------------------------------------
# Manual job runner (admin panel push/pull) — separate state, does NOT gate.
# ---------------------------------------------------------------------------
_manual_lock = threading.Lock()
_manual_thread: Optional[threading.Thread] = None


def _manual_state_path() -> str:
    return os.path.join(_data_root(), _MANUAL_JOB_FILE)


def _write_manual(state: Dict[str, Any]) -> None:
    state["updated_at"] = time.time()
    try:
        os.makedirs(_data_root(), exist_ok=True)
        p = _manual_state_path()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as e:
        logger.debug("manual job: could not write state: %s", e)


def get_manual_state() -> Dict[str, Any]:
    try:
        with open(_manual_state_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"status": "idle"}


def manual_job_running() -> bool:
    return bool(_manual_thread and _manual_thread.is_alive())


def start_manual_job(app, action: str, groups: Optional[List[str]] = None, force: bool = False) -> Dict[str, Any]:
    """Start a background push/pull job. action in {'push','pull'}.

    groups=None means all groups. Returns the initial job state, or an error
    dict if a job is already running.
    """
    global _manual_thread
    if action not in ("push", "pull"):
        return {"error": "action must be 'push' or 'pull'"}
    with _manual_lock:
        if manual_job_running():
            return {"error": "a sync job is already running", "state": get_manual_state()}
        target = groups if groups else _list_group_folders(app)
        init = {
            "status": "running", "action": action, "force": bool(force),
            "total": len(target), "done": 0, "current_group": None,
            "started_at": time.time(), "results": [],
        }
        _write_manual(init)
        _manual_thread = threading.Thread(
            target=_run_manual_job, args=(app, action, list(target), bool(force)),
            daemon=True, name=f"drive-manual-{action}",
        )
        _manual_thread.start()
        return init


def _run_manual_job(app, action: str, folders: List[str], force: bool) -> None:
    from firebase import firebase_config as fc
    results: List[Dict[str, Any]] = []
    done = 0
    for folder in folders:
        _write_manual({"status": "running", "action": action, "force": force,
                       "total": len(folders), "done": done, "current_group": folder,
                       "results": results})
        ok = False
        err = None
        try:
            with app.app_context():
                if action == "push":
                    ok = bool(fc.firebase_push_group_files(folder, local_group_folder=folder, force=force))
                else:
                    ok = bool(fc.firebase_pull_group_to_local(folder, local_group_folder=folder, force=force))
        except Exception as e:
            err = str(e)
            logger.warning("manual %s failed for %s: %s", action, folder, e)
        results.append({"folder": folder, "ok": ok, "error": err})
        done += 1
    _write_manual({"status": "done", "action": action, "force": force,
                   "total": len(folders), "done": done, "current_group": None,
                   "results": results, "finished_at": time.time()})
    logger.info("manual %s job complete — %d groups", action, done)


# ---------------------------------------------------------------------------
# Up-to-date check against the remote backend (on-demand, admin panel)
# ---------------------------------------------------------------------------
def compute_sync_diff(app) -> Dict[str, Any]:
    """Per-group reconcile direction vs the active remote backend.

    Backend-agnostic — uses the unified `compare_group_payload_freshness`, so it
    works for both Drive and Firebase. Returns, per group, whether it is in sync
    and (if not) which direction is pending.
    """
    backend, ready = _remote_backend_and_ready(app)
    if not ready:
        return {"backend": backend, "supported": True, "remote_ok": False,
                "message": "Η απομακρυσμένη βάση δεν είναι διαθέσιμη (credentials/init)."}

    from firebase import firebase_config as fc
    folders = _list_group_folders(app)
    groups: List[Dict[str, Any]] = []
    groups_push = groups_pull = 0
    for folder in folders:
        action = "unknown"
        err = None
        local_files = remote_files = 0
        try:
            with app.app_context():
                fresh = fc.compare_group_payload_freshness(folder, local_group_folder=folder) or {}
            action = str(fresh.get("action") or "unknown")
            local_files = int((fresh.get("local") or {}).get("file_count") or 0)
            remote_files = int((fresh.get("remote") or {}).get("file_count") or 0)
        except Exception as e:
            err = str(e)
        in_sync = action in ("equal", "noop") and not err
        if action == "push":
            groups_push += 1
        elif action == "pull" or (action == "unknown" and not err):
            groups_pull += 1
        groups.append({
            "folder": folder,
            "action": action,
            "in_sync": in_sync,
            "local_files": local_files,
            "remote_files": remote_files,
            "error": err,
        })
    return {
        "backend": backend, "supported": True, "remote_ok": True,
        "checked_at": time.time(),
        "up_to_date": groups_push == 0 and groups_pull == 0,
        "groups_pending_push": groups_push,
        "groups_pending_pull": groups_pull,
        "groups": groups,
    }
