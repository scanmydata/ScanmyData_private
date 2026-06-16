"""Google Drive storage backend — parallel to firebase_config's RTDB backend.

Layout on Drive:
    Scanmydata_data/                       (root, looked up by name on init)
        <group_folder>/
            <file_name>                    (binary, Fernet-encrypted)
            excel/<file>.xlsx              (binary, Fernet-encrypted)
            epsilon/<file>.json            (binary, Fernet-encrypted)
            imports/<file>.xlsx            (binary, Fernet-encrypted)
            activity.log                   (plaintext NDJSON, appended)
            .sync_meta.json                (plaintext JSON, payload_state marker)

Public API mirrors firebase_config:
    drive_push_group_files(group_name, ...)
    drive_pull_group_to_local(group_name, ...)
    drive_log_activity(user_id, group_name, action, details)
    drive_ensure_group_data_local(group_folder, ...)

Authentication:
    OAuth USER credentials. Reads google_client_id / google_client_secret /
    google_drive_refresh_token from env (loaded from Infisical at startup).
    For local dev, falls back to drive_token.json next to the project root.
"""
from __future__ import annotations

import io
import json
import logging
import os
import random
import re
import socket
import ssl
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from googleapiclient.errors import HttpError

from admin import encryption

logger = logging.getLogger(__name__)

ROOT_FOLDER_NAME = "Scanmydata_data"
SCOPES = ["https://www.googleapis.com/auth/drive"]
TOKEN_FILE = os.path.join(os.getcwd(), "drive_token.json")
FOLDER_MIME = "application/vnd.google-apps.folder"

# Cache only the credentials — Drive service objects wrap a single httplib2.Http
# instance which is NOT safe to reuse across many calls (sockets get into bad
# states, surfacing as SSL DECRYPTION_FAILED / WRONG_VERSION_NUMBER). We build
# a fresh service per call; build() is cheap once cache_discovery=False.
_drive_creds: Optional[Credentials] = None
_drive_lock = threading.Lock()
_root_folder_id: Optional[str] = None
# Per-group folder id cache: {group_folder: folder_id}
_group_folder_cache: Dict[str, str] = {}
# Per-group file index cache: {group_folder: {rel_path: {'id': str, 'modifiedTime': str, 'size': int}}}
_group_file_index: Dict[str, Dict[str, Dict[str, Any]]] = {}
_index_lock = threading.Lock()


# ============================================================================
# Initialization
# ============================================================================

def _credentials_from_env() -> Optional[Credentials]:
    refresh = (os.getenv("google_drive_refresh_token") or os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN") or "").strip()
    client_id = (os.getenv("google_client_id") or os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("google_client_secret") or os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
    if not (refresh and client_id and client_secret):
        return None
    creds = Credentials(
        token=None,
        refresh_token=refresh,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    try:
        creds.refresh(Request())
        return creds
    except Exception as e:
        logger.warning("Drive env-credentials refresh failed: %s", e)
        return None


def _credentials_from_token_file() -> Optional[Credentials]:
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
                fh.write(creds.to_json())
        return creds
    except Exception as e:
        logger.warning("Drive token file load failed: %s", e)
        return None


def init_drive() -> bool:
    """Initialize Drive credentials and resolve the root folder id."""
    global _drive_creds, _root_folder_id
    with _drive_lock:
        if _drive_creds is not None and _root_folder_id is not None:
            return True
        creds = _credentials_from_env() or _credentials_from_token_file()
        if creds is None:
            logger.warning("Drive disabled: no OAuth user credentials available")
            return False
        try:
            _drive_creds = creds
            _root_folder_id = _find_root_folder()
            if not _root_folder_id:
                logger.error("Drive root folder %r not found", ROOT_FOLDER_NAME)
                _drive_creds = None
                return False
            logger.info("Drive backend initialized (root folder id=%s)", _root_folder_id)
            return True
        except Exception as e:
            logger.error("Drive init failed: %s", e)
            _drive_creds = None
            return False


def is_drive_enabled() -> bool:
    return _drive_creds is not None and _root_folder_id is not None


def _service():
    if _drive_creds is None:
        init_drive()
    if _drive_creds is None:
        raise RuntimeError("Drive backend is not initialized")
    # Fresh service per call — see comment on _drive_creds above.
    return build("drive", "v3", credentials=_drive_creds, cache_discovery=False)


# ============================================================================
# Retry wrapper (rate-limit + transient network)
# ============================================================================

_TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}
_RATE_LIMIT_REASONS = {"userRateLimitExceeded", "rateLimitExceeded", "quotaExceeded"}
_TRANSIENT_NETWORK_EXC = (ssl.SSLError, socket.error, ConnectionError, OSError)


def _http_error_is_rate_limited(e: HttpError) -> bool:
    status = getattr(getattr(e, "resp", None), "status", None)
    if status in _TRANSIENT_HTTP_STATUSES:
        return True
    if status != 403:
        return False
    try:
        content = e.content
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        data = json.loads(content) if content else {}
        for err in (data.get("error", {}) or {}).get("errors", []) or []:
            if str(err.get("reason") or "") in _RATE_LIMIT_REASONS:
                return True
    except Exception:
        pass
    return False


def _is_retriable(e: BaseException) -> bool:
    if isinstance(e, _TRANSIENT_NETWORK_EXC):
        return True
    if isinstance(e, HttpError):
        return _http_error_is_rate_limited(e)
    return False


def _call_with_retry(fn: Callable, *args, max_attempts: int = 5, base_delay: float = 1.0, **kwargs):
    """Run fn with exponential backoff on transient Drive errors.

    Per the official Drive API guide:
        wait = min((2^n) + random_ms, maximum_backoff)
    where maximum_backoff is 32-64 seconds.

    In addition to per-call retries this updates a module-level backoff state
    that the scheduler consults to throttle subsequent sync attempts — so
    sustained rate-limit pressure pushes the *next* scheduler tick further
    into the future instead of hammering Drive every 60 s.
    """
    saw_rate_limit = False
    for attempt in range(max_attempts):
        try:
            result = fn(*args, **kwargs)
            if saw_rate_limit:
                _record_rate_limit_event(severity="soft")
            return result
        except Exception as e:
            retriable = _is_retriable(e)
            is_rate = isinstance(e, HttpError) and _http_error_is_rate_limited(e)
            if is_rate:
                saw_rate_limit = True
            if not retriable or attempt == max_attempts - 1:
                if is_rate:
                    _record_rate_limit_event(severity="hard")
                raise
            # Drive guide: min((2^n) + random_ms, 32s).
            delay = min((2 ** attempt) + random.uniform(0, 1.0), 32.0)
            logger.warning(
                "Drive transient error (attempt %d/%d): %s - retrying in %.1fs",
                attempt + 1, max_attempts, type(e).__name__, delay,
            )
            time.sleep(delay)


# ============================================================================
# Scheduler-level adaptive backoff
# ============================================================================
# When Drive returns rate-limit errors, the *scheduler* should also slow down
# its next sync attempt — otherwise we just generate more failed calls each
# minute. Tracks consecutive hits and pushes next_allowed_at forward.

_backoff_state: Dict[str, float] = {
    "next_allowed_at": 0.0,
    "consecutive_hits": 0.0,
    "last_hit_at": 0.0,
    "last_success_at": 0.0,
}
_backoff_lock = threading.Lock()

# Tuning constants.
_BACKOFF_BASE_SECS = 60.0          # 1 hit ≈ 1 minute defer
_BACKOFF_MAX_SECS = 3600.0         # cap at 1 hour
_BACKOFF_MAX_LEVEL = 8             # ≈ 2^8 * 60 = 256 min, but capped above


def _record_rate_limit_event(severity: str = "soft") -> None:
    """Note a rate-limit event and push the next-allowed time forward.

    severity='soft' = retry succeeded, mild pressure signal (weight 1)
    severity='hard' = retry exhausted, strong pressure signal (weight 2)
    """
    weight = 2 if severity == "hard" else 1
    with _backoff_lock:
        _backoff_state["consecutive_hits"] = min(
            _backoff_state["consecutive_hits"] + weight, _BACKOFF_MAX_LEVEL
        )
        n = int(_backoff_state["consecutive_hits"])
        delay = min(_BACKOFF_BASE_SECS * (2 ** n) + random.uniform(0, 30), _BACKOFF_MAX_SECS)
        _backoff_state["next_allowed_at"] = max(
            _backoff_state["next_allowed_at"], time.time() + delay
        )
        _backoff_state["last_hit_at"] = time.time()
        logger.warning(
            "Drive rate-limit pressure: severity=%s consecutive=%d next_sync_in=%.0fs",
            severity, n, delay,
        )


def record_sync_success() -> None:
    """Decay the backoff state after a successful group sync."""
    with _backoff_lock:
        if _backoff_state["consecutive_hits"] > 0:
            _backoff_state["consecutive_hits"] -= 1
        _backoff_state["last_success_at"] = time.time()


def time_until_next_allowed() -> float:
    """Seconds until the scheduler is allowed to attempt the next sync."""
    with _backoff_lock:
        return max(0.0, _backoff_state["next_allowed_at"] - time.time())


def get_backoff_status() -> Dict[str, Any]:
    """Snapshot of the backoff state for admin/debug UI."""
    with _backoff_lock:
        return dict(_backoff_state)


def _local_group_has_payload(data_dir: str) -> bool:
    """True if data_dir contains any non-placeholder business file."""
    try:
        if not os.path.isdir(data_dir):
            return False
        for root, _d, files in os.walk(data_dir):
            for name in files:
                if name.startswith("."):
                    continue
                if name in {"activity.log", "error.log"}:
                    continue
                return True
    except Exception:
        return False
    return False


def should_defer_sync(group_folder: str = None) -> tuple:
    """Decide whether the scheduler should skip this tick.

    Returns (should_defer, seconds_remaining, reason).

    Cold-start exception: if the local data/<group_folder>/ directory has no
    business payload, never defer — that group needs its initial pull NOW.
    """
    if group_folder:
        data_dir = os.path.join(os.getcwd(), "data", str(group_folder))
        if not _local_group_has_payload(data_dir):
            return False, 0.0, "cold_start_bypass"
    remaining = time_until_next_allowed()
    if remaining > 0:
        return True, remaining, "rate_limit_backoff"
    return False, 0.0, "ok"


# ============================================================================
# Folder + file helpers
# ============================================================================

def _find_root_folder() -> Optional[str]:
    svc = build("drive", "v3", credentials=_drive_creds, cache_discovery=False)
    q = (
        f"name = '{ROOT_FOLDER_NAME}' and "
        f"mimeType = '{FOLDER_MIME}' and trashed = false"
    )
    resp = _call_with_retry(
        svc.files().list(q=q, fields="files(id, name)", spaces="drive", pageSize=10).execute
    )
    folders = resp.get("files", [])
    return folders[0]["id"] if folders else None


def _list_children(parent_id: str, only_folders: bool = False) -> List[Dict[str, Any]]:
    svc = _service()
    q = f"'{parent_id}' in parents and trashed = false"
    if only_folders:
        q += f" and mimeType = '{FOLDER_MIME}'"
    out: List[Dict[str, Any]] = []
    page_token = None
    while True:
        resp = _call_with_retry(svc.files().list(
            q=q,
            fields="nextPageToken, files(id, name, mimeType, modifiedTime, size, appProperties)",
            spaces="drive",
            pageSize=1000,
            pageToken=page_token,
        ).execute)
        out.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def _ensure_subfolder(parent_id: str, name: str) -> str:
    svc = _service()
    q = (
        f"'{parent_id}' in parents and name = '{_escape_q(name)}' and "
        f"mimeType = '{FOLDER_MIME}' and trashed = false"
    )
    resp = _call_with_retry(
        svc.files().list(q=q, fields="files(id, name)", spaces="drive", pageSize=10).execute
    )
    folders = resp.get("files", [])
    if folders:
        return folders[0]["id"]
    meta = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
    created = _call_with_retry(svc.files().create(body=meta, fields="id").execute)
    return created["id"]


def _escape_q(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _ensure_group_folder(group_folder: str) -> str:
    if group_folder in _group_folder_cache:
        return _group_folder_cache[group_folder]
    if not is_drive_enabled():
        init_drive()
    fid = _ensure_subfolder(_root_folder_id, group_folder)
    _group_folder_cache[group_folder] = fid
    return fid


def _ensure_path(group_folder: str, rel_dir: str) -> str:
    """Ensure rel_dir (e.g. 'excel' or 'epsilon/sub') exists under group folder. Returns leaf id."""
    parent = _ensure_group_folder(group_folder)
    if not rel_dir:
        return parent
    for seg in [s for s in rel_dir.replace("\\", "/").split("/") if s]:
        parent = _ensure_subfolder(parent, seg)
    return parent


def _build_group_index(group_folder: str, force: bool = False) -> Dict[str, Dict[str, Any]]:
    """Walk the group's Drive subtree and build {rel_path: meta} map."""
    if (not force) and group_folder in _group_file_index:
        return _group_file_index[group_folder]
    if not is_drive_enabled():
        init_drive()
    group_id = _ensure_group_folder(group_folder)
    index: Dict[str, Dict[str, Any]] = {}

    def _walk(folder_id: str, prefix: str):
        for child in _list_children(folder_id):
            name = child.get("name", "")
            if child.get("mimeType") == FOLDER_MIME:
                sub = f"{prefix}/{name}" if prefix else name
                _walk(child["id"], sub)
            else:
                rel = f"{prefix}/{name}" if prefix else name
                index[rel] = {
                    "id": child["id"],
                    "modifiedTime": child.get("modifiedTime"),
                    "size": int(child.get("size") or 0),
                    "appProperties": child.get("appProperties") or {},
                }

    _walk(group_id, "")
    with _index_lock:
        _group_file_index[group_folder] = index
    return index


def _invalidate_group_index(group_folder: str) -> None:
    with _index_lock:
        _group_file_index.pop(group_folder, None)


# ============================================================================
# Encrypted upload / download primitives
# ============================================================================

def _cipher():
    key = encryption._ensure_key()
    if not key:
        raise RuntimeError("No Fernet key available")
    from cryptography.fernet import Fernet
    return Fernet(key)


def _upload_bytes(parent_id: str, name: str, raw: bytes, mtime: float, *, existing_id: Optional[str] = None) -> Dict[str, Any]:
    svc = _service()
    body = io.BytesIO(raw)
    media = MediaIoBaseUpload(body, mimetype="application/octet-stream", resumable=False)
    iso_mtime = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    metadata = {
        "modifiedTime": iso_mtime,
        "appProperties": {"local_mtime": f"{mtime:.6f}"},
    }
    if existing_id:
        # Update in place — keeps the file id stable.
        return _call_with_retry(svc.files().update(
            fileId=existing_id,
            body=metadata,
            media_body=media,
            fields="id, modifiedTime, size",
        ).execute)
    metadata["name"] = name
    metadata["parents"] = [parent_id]
    return _call_with_retry(svc.files().create(
        body=metadata,
        media_body=media,
        fields="id, modifiedTime, size",
    ).execute)


def _download_bytes(file_id: str) -> bytes:
    svc = _service()
    req = svc.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _status, done = _call_with_retry(downloader.next_chunk)
    return buf.getvalue()


def _delete_file(file_id: str) -> bool:
    try:
        _call_with_retry(_service().files().delete(fileId=file_id).execute)
        return True
    except HttpError as e:
        logger.warning("Drive delete failed for %s: %s", file_id, e)
        return False


# ============================================================================
# rel-path helpers (must mirror firebase_config push routing exactly)
# ============================================================================

def _resolve_group_identity(group_name: str, local_group_folder: str = None) -> Dict[str, Any]:
    input_group = str(group_name or "").strip()
    input_folder = str(local_group_folder or "").strip()
    try:
        from models import Group
        for token in [t for t in [input_group, input_folder] if t]:
            grp = Group.query.filter_by(name=token).first() or Group.query.filter_by(data_folder=token).first()
            if grp:
                name = str(getattr(grp, "name", "") or "").strip()
                folder = str(getattr(grp, "data_folder", "") or "").strip() or name
                if name:
                    return {"resolved": True, "group_name": name, "local_folder": folder}
    except Exception:
        pass
    return {
        "resolved": False,
        "group_name": input_group,
        "local_folder": input_folder or input_group,
    }


def _compute_drive_rel_path(file_path: str, source_dir: str) -> Optional[str]:
    """Return the Drive-relative path for a local file, or None to skip."""
    fname = os.path.basename(file_path)
    if fname.startswith(".") or fname in ("files_json", "fiscal_meta_json"):
        return None
    if re.match(r"^epsilon_invoices\.(json|xlsx|xls)$", fname, re.IGNORECASE):
        # Legacy AFM-less epsilon files are skipped, same as firebase backend.
        return None
    rel_path = os.path.relpath(file_path, source_dir).replace("\\", "/")
    if "/" not in rel_path:
        # Root-level Excel files live under imports/ on the remote (mirrors firebase backend).
        _, ext = os.path.splitext(fname)
        if ext.lower() in (".xls", ".xlsx"):
            return f"imports/{fname}"
    return rel_path


# ============================================================================
# Public API — mirrors firebase_config
# ============================================================================

def drive_push_group_files(
    group_name: str,
    local_data_root: str = None,
    dry_run: bool = False,
    verbose: bool = False,
    force: bool = False,
    local_group_folder: str = None,
):
    """Upload data/<group_folder>/ to Scanmydata_data/<group_folder>/ on Drive."""
    try:
        if not is_drive_enabled() and not init_drive():
            logger.warning("[DRIVE PUSH] Drive backend not initialized")
            return False

        identity = _resolve_group_identity(group_name, local_group_folder)
        resolved = bool(identity.get("resolved"))
        allow_unknown = str(os.getenv("FIREBASE_ALLOW_UNKNOWN_GROUP_PUSH", "0")).strip().lower() in {"1", "true", "yes", "on"}
        if (not resolved) and (not allow_unknown):
            logger.warning("[DRIVE PUSH] Refusing push for unknown group identity: group=%s folder=%s", group_name, local_group_folder)
            return False
        group_name = identity["group_name"]
        local_folder = identity["local_folder"]

        if local_data_root is None:
            local_data_root = os.path.join(os.getcwd(), "data")
        source_dir = os.path.join(local_data_root, local_folder)
        if not os.path.isdir(source_dir):
            logger.warning("[DRIVE PUSH] Source dir missing: %s", source_dir)
            return True

        cipher = _cipher()
        smart_sync = os.getenv("FIREBASE_SMART_SYNC", "1") == "1"
        index = _build_group_index(local_folder, force=True)

        files_uploaded = 0
        files_failed = 0
        files_deleted = 0
        bytes_uploaded = 0
        upload_candidates: List[str] = []
        delete_candidates: List[str] = []
        local_rel_paths = set()

        for root, _dirs, files in os.walk(source_dir):
            for fname in files:
                file_path = os.path.join(root, fname)
                rel_path = _compute_drive_rel_path(file_path, source_dir)
                if rel_path is None:
                    continue
                local_rel_paths.add(rel_path)

                try:
                    local_mtime = os.path.getmtime(file_path)
                except Exception:
                    local_mtime = time.time()

                existing = index.get(rel_path)
                if smart_sync and (not force) and existing:
                    # Compare against appProperties.local_mtime (precise) or Drive modifiedTime.
                    remote_mtime = 0.0
                    try:
                        remote_mtime = float((existing.get("appProperties") or {}).get("local_mtime") or 0)
                    except Exception:
                        remote_mtime = 0.0
                    # Apply smart-sync uniformly across all subfolders. The legacy
                    # firebase backend exempted excel/ and epsilon/ — on Drive that
                    # caused every scheduled push to re-upload the whole subtree
                    # and triggered rate-limit failures.
                    if remote_mtime and local_mtime <= remote_mtime:
                        continue

                try:
                    with open(file_path, "rb") as fh:
                        raw = fh.read()
                    encrypted = cipher.encrypt(raw)
                except Exception as e:
                    files_failed += 1
                    logger.error("[DRIVE PUSH] Encrypt failed for %s: %s", file_path, e)
                    continue

                if dry_run:
                    upload_candidates.append(rel_path)
                    continue

                try:
                    rel_dir = os.path.dirname(rel_path)
                    parent_id = _ensure_path(local_folder, rel_dir)
                    leaf_name = os.path.basename(rel_path)
                    res = _upload_bytes(
                        parent_id, leaf_name, encrypted, local_mtime,
                        existing_id=(existing or {}).get("id"),
                    )
                    files_uploaded += 1
                    bytes_uploaded += len(raw)
                    index[rel_path] = {
                        "id": res.get("id"),
                        "modifiedTime": res.get("modifiedTime"),
                        "size": int(res.get("size") or 0),
                        "appProperties": {"local_mtime": f"{local_mtime:.6f}"},
                    }
                    if verbose:
                        logger.info("[DRIVE PUSH] uploaded %s (%d bytes)", rel_path, len(raw))
                except HttpError as e:
                    files_failed += 1
                    logger.error("[DRIVE PUSH] upload failed for %s: %s", rel_path, e)

        # Delete remote files no longer present locally.
        for rel_path, meta in list(index.items()):
            if rel_path in local_rel_paths:
                continue
            if rel_path in ("activity.log", "error.log", ".sync_meta.json"):
                continue  # keep server-side logs/meta
            if dry_run:
                delete_candidates.append(rel_path)
                continue
            if _delete_file(meta["id"]):
                files_deleted += 1
                index.pop(rel_path, None)

        logger.info(
            "[DRIVE PUSH] group=%s uploaded=%d deleted=%d failed=%d bytes=%d",
            group_name, files_uploaded, files_deleted, files_failed, bytes_uploaded,
        )

        if dry_run:
            return {
                "success": True,
                "upload_candidates": upload_candidates,
                "delete_candidates": delete_candidates,
                "uploaded_count": len(upload_candidates),
                "deleted_count": len(delete_candidates),
                "failed_count": files_failed,
            }

        # Record push activity for unified admin Logs visibility (same action
        # name as the legacy firebase backend so the existing log viewer
        # picks it up without changes).
        try:
            drive_log_activity("system", group_name, "firebase_push", {
                "backend": "drive",
                "local_folder": local_folder,
                "force": bool(force),
                "files_uploaded": int(files_uploaded),
                "files_deleted": int(files_deleted),
                "files_failed": int(files_failed),
                "bytes_uploaded": int(bytes_uploaded),
            })
        except Exception:
            logger.debug("Could not write firebase_push activity entry")

        if files_failed == 0:
            record_sync_success()
            # Update the local last-push marker so subsequent freshness checks
            # can decide push/equal without any network call.
            try:
                latest_local_mtime = 0.0
                file_count = 0
                for r, _d, fs in os.walk(source_dir):
                    for n in fs:
                        if n.startswith(".") or n in ("activity.log", "error.log"):
                            continue
                        try:
                            file_count += 1
                            mt = os.path.getmtime(os.path.join(r, n))
                            if mt > latest_local_mtime:
                                latest_local_mtime = mt
                        except Exception:
                            continue
                _write_last_push_marker(source_dir, latest_local_mtime, file_count)
            except Exception:
                logger.debug("Could not update last-push marker for %s", source_dir)
        return files_failed == 0
    except Exception as e:
        logger.error("[DRIVE PUSH] Unexpected error for group %s: %s", group_name, e)
        try:
            drive_log_activity("system", group_name, "firebase_push_error", {
                "backend": "drive",
                "error": str(e),
                "local_group_folder": str(local_group_folder or group_name or "").strip(),
                "force": bool(force),
            })
        except Exception:
            logger.debug("Could not write firebase_push_error activity entry")
        return False


def drive_pull_group_to_local(
    group_name: str,
    local_data_root: str = None,
    force: bool = False,
    local_group_folder: str = None,
) -> bool:
    """Download Scanmydata_data/<group_folder>/ from Drive to data/<group_folder>/."""
    try:
        if not is_drive_enabled() and not init_drive():
            logger.warning("[DRIVE PULL] Drive backend not initialized")
            return False

        if local_data_root is None:
            local_data_root = os.path.join(os.getcwd(), "data")
        local_folder = str(local_group_folder or group_name or "").strip()
        target_dir = os.path.join(local_data_root, local_folder)
        os.makedirs(target_dir, exist_ok=True)

        cipher = _cipher()
        smart_sync = os.getenv("FIREBASE_SMART_SYNC", "1") == "1"
        index = _build_group_index(local_folder, force=True)

        files_created = 0
        files_failed = 0
        bytes_downloaded = 0

        for rel_path, meta in index.items():
            try:
                if rel_path == ".sync_meta.json":
                    continue
                local_path = os.path.join(target_dir, rel_path.replace("/", os.sep))
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                remote_mtime = 0.0
                try:
                    remote_mtime = float((meta.get("appProperties") or {}).get("local_mtime") or 0)
                except Exception:
                    remote_mtime = 0.0

                # Activity logs: append-only behavior to mirror firebase backend.
                is_log = rel_path.endswith(".log")

                if smart_sync and (not force) and (not is_log) and os.path.exists(local_path):
                    # See push-side comment: no excel/epsilon exemption on Drive.
                    try:
                        local_mtime = os.path.getmtime(local_path)
                    except Exception:
                        local_mtime = 0
                    if remote_mtime and local_mtime >= remote_mtime:
                        continue

                blob = _download_bytes(meta["id"])
                bytes_downloaded += len(blob)
                try:
                    plain = cipher.decrypt(blob)
                except Exception:
                    plain = blob  # plaintext file (activity.log / error.log historically)

                if is_log:
                    with open(local_path, "ab") as fh:
                        fh.write(plain)
                else:
                    with open(local_path, "wb") as fh:
                        fh.write(plain)
                if remote_mtime:
                    try:
                        os.utime(local_path, (remote_mtime, remote_mtime))
                    except Exception:
                        pass
                files_created += 1
            except Exception as e:
                files_failed += 1
                logger.error("[DRIVE PULL] Failed to materialize %s: %s", rel_path, e)

        logger.info(
            "[DRIVE PULL] group=%s created=%d failed=%d bytes=%d -> %s",
            group_name, files_created, files_failed, bytes_downloaded, target_dir,
        )

        # Activity log (firebase_pull action for log-viewer compatibility).
        try:
            if files_created > 0 or files_failed > 0 or bytes_downloaded > 0:
                drive_log_activity("system", group_name, "firebase_pull", {
                    "backend": "drive",
                    "files_created": int(files_created),
                    "files_failed": int(files_failed),
                    "bytes_downloaded": int(bytes_downloaded),
                })
        except Exception:
            logger.debug("Could not write firebase_pull activity entry")

        if files_failed == 0:
            record_sync_success()
            # After a pull, local equals remote — set marker so the next
            # freshness check is a no-op.
            try:
                latest_local_mtime = 0.0
                file_count = 0
                for r, _d, fs in os.walk(target_dir):
                    for n in fs:
                        if n.startswith(".") or n in ("activity.log", "error.log"):
                            continue
                        try:
                            file_count += 1
                            mt = os.path.getmtime(os.path.join(r, n))
                            if mt > latest_local_mtime:
                                latest_local_mtime = mt
                        except Exception:
                            continue
                _write_last_push_marker(target_dir, latest_local_mtime, file_count)
            except Exception:
                logger.debug("Could not update last-push marker for %s", target_dir)
        return True
    except Exception as e:
        logger.error("[DRIVE PULL] Unexpected error for group %s: %s", group_name, e)
        return False


def drive_log_activity(user_id: str, group_name: str, action: str, details: Optional[Dict] = None) -> bool:
    """Append an NDJSON activity entry to Scanmydata_data/<group_folder>/activity.log."""
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        entry = {
            "user_id": user_id,
            "group": group_name,
            "action": action,
            "timestamp": timestamp,
            "details": details or {},
        }

        folder_name = group_name
        try:
            from models import Group
            grp = Group.query.filter_by(name=group_name).first()
            if grp and getattr(grp, "data_folder", None):
                folder_name = grp.data_folder
        except Exception:
            pass

        # 1) Always append locally — fast, atomic, no Drive call needed for hot path.
        try:
            data_dir = os.path.join(os.getcwd(), "data", str(folder_name) if folder_name else "global")
            os.makedirs(data_dir, exist_ok=True)
            with open(os.path.join(data_dir, "activity.log"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

        # 2) Append remotely (rewrite full content; small files, infrequent).
        if is_drive_enabled() or init_drive():
            try:
                index = _build_group_index(folder_name)
                meta = index.get("activity.log")
                existing_blob = b""
                if meta:
                    try:
                        existing_blob = _download_bytes(meta["id"])
                    except Exception:
                        existing_blob = b""
                new_blob = existing_blob + (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
                parent_id = _ensure_group_folder(folder_name)
                res = _upload_bytes(
                    parent_id, "activity.log", new_blob, time.time(),
                    existing_id=(meta or {}).get("id"),
                )
                index["activity.log"] = {
                    "id": res.get("id"),
                    "modifiedTime": res.get("modifiedTime"),
                    "size": int(res.get("size") or 0),
                    "appProperties": {"local_mtime": f"{time.time():.6f}"},
                }
            except Exception as e:
                logger.debug("[DRIVE LOG] remote append failed: %s", e)

        return True
    except Exception as e:
        logger.error("[DRIVE LOG] activity log failed: %s", e)
        return False


_LAST_PUSH_MARKER = ".drive_last_push.json"


def _last_push_marker_path(target_dir: str) -> str:
    return os.path.join(target_dir, _LAST_PUSH_MARKER)


def _read_last_push_marker(target_dir: str) -> float:
    """Return the local 'latest_mtime' captured at the last successful push (0 if absent)."""
    try:
        p = _last_push_marker_path(target_dir)
        if not os.path.exists(p):
            return 0.0
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return float(data.get("latest_mtime") or 0)
    except Exception:
        return 0.0


def _write_last_push_marker(target_dir: str, latest_mtime: float, file_count: int) -> None:
    try:
        os.makedirs(target_dir, exist_ok=True)
        p = _last_push_marker_path(target_dir)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({
                "latest_mtime": float(latest_mtime or 0),
                "file_count": int(file_count or 0),
                "written_at": time.time(),
            }, fh)
        os.replace(tmp, p)
    except Exception:
        logger.debug("Could not write last-push marker to %s", target_dir)


def drive_compare_group_payload_freshness(
    group_name: str,
    local_group_folder: str = None,
    local_data_root: str = None,
) -> Dict[str, Any]:
    """Decide push/pull/equal without touching RTDB OR Drive (zero network).

    Uses a local marker file `.drive_last_push.json` to remember the local
    `latest_mtime` captured at the last successful push. The scheduler can
    call this every tick cheaply:

    * No local files yet  -> 'pull'  (cold start, fetch from Drive)
    * local_mtime > marker -> 'push' (something changed locally)
    * else                 -> 'equal' (no work to do)

    Returns the same shape as firebase_config.compare_group_payload_freshness.
    """
    local_folder = str(local_group_folder or group_name or "").strip()
    if local_data_root is None:
        local_data_root = os.path.join(os.getcwd(), "data")
    target_dir = os.path.join(local_data_root, local_folder)

    local_latest_mtime = 0.0
    local_count = 0
    try:
        if os.path.isdir(target_dir):
            for root, _d, files in os.walk(target_dir):
                for name in files:
                    if name.startswith("."):
                        continue
                    if name in ("activity.log", "error.log"):
                        continue
                    try:
                        local_count += 1
                        mt = os.path.getmtime(os.path.join(root, name))
                        if mt > local_latest_mtime:
                            local_latest_mtime = mt
                    except Exception:
                        continue
    except Exception:
        pass

    marker_mtime = _read_last_push_marker(target_dir)
    local_meta = {
        "exists": local_count > 0,
        "latest_mtime": local_latest_mtime,
        "file_count": local_count,
    }

    tolerance = 2.0
    if local_count == 0:
        # Cold start — let the scheduler pull from Drive.
        return {
            "group_name": group_name,
            "local_group_folder": local_folder,
            "action": "pull",
            "reason": "local_empty",
            "local": local_meta,
            "remote": {"exists": True, "latest_mtime": 0, "file_count": 0, "source": "drive_authoritative"},
        }

    if local_latest_mtime > marker_mtime + tolerance:
        action, reason = "push", "local_newer_than_last_push"
    else:
        action, reason = "equal", "no_local_changes_since_last_push"

    return {
        "group_name": group_name,
        "local_group_folder": local_folder,
        "action": action,
        "reason": reason,
        "local": local_meta,
        "remote": {
            "exists": True,
            "latest_mtime": marker_mtime,
            "file_count": 0,
            "source": "local_push_marker",
        },
    }


def drive_group_sync_diff(group_name: str, local_group_folder: str = None) -> Dict[str, Any]:
    """Compare local data/<group>/ against Drive without transferring files.

    Returns {in_sync, pending_push, pending_pull, push[:50], pull[:50]} where
    pending_push = local files newer/absent on Drive, pending_pull = Drive
    files newer/absent locally. Used by the admin 'up-to-date' check.
    """
    if not is_drive_enabled() and not init_drive():
        return {"error": "drive_disabled"}

    identity = _resolve_group_identity(group_name, local_group_folder)
    local_folder = identity["local_folder"]
    source_dir = os.path.join(os.getcwd(), "data", local_folder)

    index = _build_group_index(local_folder, force=True)

    local: Dict[str, float] = {}
    if os.path.isdir(source_dir):
        for root, _dirs, files in os.walk(source_dir):
            for fname in files:
                fp = os.path.join(root, fname)
                rel = _compute_drive_rel_path(fp, source_dir)
                if rel is None:
                    continue
                try:
                    local[rel] = os.path.getmtime(fp)
                except Exception:
                    local[rel] = 0.0

    skip = {"activity.log", "error.log", ".sync_meta.json"}
    push: List[str] = []
    pull: List[str] = []
    tolerance = 2.0

    for rel, lmt in local.items():
        existing = index.get(rel)
        if not existing:
            push.append(rel)
            continue
        try:
            rmt = float((existing.get("appProperties") or {}).get("local_mtime") or 0)
        except Exception:
            rmt = 0.0
        if rmt and lmt > rmt + tolerance:
            push.append(rel)

    for rel, meta in index.items():
        if rel in skip:
            continue
        if rel not in local:
            pull.append(rel)
            continue
        try:
            rmt = float((meta.get("appProperties") or {}).get("local_mtime") or 0)
        except Exception:
            rmt = 0.0
        if rmt and rmt > local.get(rel, 0.0) + tolerance:
            pull.append(rel)

    return {
        "in_sync": not push and not pull,
        "pending_push": len(push),
        "pending_pull": len(pull),
        "push": push[:50],
        "pull": pull[:50],
    }


def drive_ensure_group_data_local(group_folder: str, create_empty_dirs: bool = True) -> bool:
    """Mirror of firebase ensure_group_data_local: lazy-pull if missing locally."""
    try:
        if not group_folder:
            return False
        data_root = os.path.join(os.getcwd(), "data")
        target_dir = os.path.join(data_root, group_folder)

        def _has_payload() -> bool:
            try:
                if not os.path.isdir(target_dir):
                    return False
                for root, _d, files in os.walk(target_dir):
                    for n in files:
                        if n.startswith("."):
                            continue
                        if n in ("activity.log", "error.log"):
                            continue
                        return True
            except Exception:
                return False
            return False

        if _has_payload():
            return True

        if is_drive_enabled() or init_drive():
            ok = bool(drive_pull_group_to_local(group_folder, data_root, force=True))
            if _has_payload():
                return True
            if not ok and create_empty_dirs:
                os.makedirs(target_dir, exist_ok=True)
                for sub in ("epsilon", "excel"):
                    os.makedirs(os.path.join(target_dir, sub), exist_ok=True)
                return True

        if create_empty_dirs:
            os.makedirs(target_dir, exist_ok=True)
            for sub in ("epsilon", "excel"):
                os.makedirs(os.path.join(target_dir, sub), exist_ok=True)
            return True
        return False
    except Exception as e:
        logger.error("[DRIVE ENSURE] failed for %s: %s", group_folder, e)
        return False
