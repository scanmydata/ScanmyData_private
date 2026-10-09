# -*- coding: utf-8 -*-
"""
accounting_result/job_registry.py

Lightweight in-process progress/abort registry for the Μαζικός bulk-compute
run, mirroring the exact pattern already used by e3/checks/e3_brain.py's
own bulk job (_get_or_init_brain_abort_registry / _publish_brain_step) so
the two features behave the same way to the user. No threading involved:
the bulk-compute request itself runs synchronously on the request thread,
publishing progress as it goes and checking the abort flag between
companies; the browser polls/aborts via separate lightweight requests
while that POST is still in flight.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

_ABORT_REGISTRY: Dict[str, Dict[str, Any]] = {}
_PROGRESS_REGISTRY: Dict[str, Dict[str, Any]] = {}

# ΝΕΟ: ποιοι μαζικοί υπολογισμοί τρέχουν ΠΡΑΓΜΑΤΙΚΑ, ανά ομάδα — ώστε το banner προόδου να το
# βλέπει κάθε χρήστης της ομάδας (και μετά από logout/login), όχι μόνο το tab που ξεκίνησε το run.
# Δύο φάσεις: (α) «client»: τα βήματα που οδηγεί ο browser (ΑΑΔΕ, προέλεγχος, popup) — ζωντανό όσο
# ο browser στέλνει heartbeat· (β) «server»: το bulk_compute τρέχει στον server και δημοσιεύει πρόοδο.
_JOBS: Dict[str, Dict[str, Any]] = {}
CLIENT_STALE_SECONDS = 25      # χωρίς heartbeat από τον browser = το run σταμάτησε (logout/κλείσιμο)
SERVER_STALE_SECONDS = 300     # το bulk_compute δημοσιεύει πρόοδο ανά εταιρία


def request_abort(job_id: str) -> bool:
    """Mark a running job for abort. Returns True if the job was already known."""
    job_id = str(job_id or "").strip()
    if not job_id:
        return False
    entry = _ABORT_REGISTRY.get(job_id)
    if entry is None:
        # Pre-register so a slightly-late abort still wins if the run is
        # just about to start.
        _ABORT_REGISTRY[job_id] = {"abort": True}
        return False
    entry["abort"] = True
    return True


def is_abort_requested(job_id: str) -> bool:
    job_id = str(job_id or "").strip()
    if not job_id:
        return False
    return bool((_ABORT_REGISTRY.get(job_id) or {}).get("abort"))


def clear_abort(job_id: str) -> None:
    job_id = str(job_id or "").strip()
    if job_id:
        _ABORT_REGISTRY.pop(job_id, None)


def publish_progress(job_id: Optional[str], label: str, percent: Optional[int] = None,
                      current: Optional[int] = None, total: Optional[int] = None) -> None:
    if not job_id:
        return
    _PROGRESS_REGISTRY[str(job_id)] = {
        "label": label,
        "percent": percent,
        "current": current,
        "total": total,
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    job = _JOBS.get(str(job_id))
    if job is not None:
        job["server_ts"] = time.time()


def get_progress(job_id: str) -> Dict[str, Any]:
    return dict(_PROGRESS_REGISTRY.get(str(job_id or "").strip(), {}))


def clear_progress(job_id: str) -> None:
    job_id = str(job_id or "").strip()
    if job_id:
        _PROGRESS_REGISTRY.pop(job_id, None)
        job = _JOBS.get(job_id)
        if job is not None:
            job["server_running"] = False


# ---------------------------------------------------------------------------
# Ενεργά jobs ανά ομάδα
# ---------------------------------------------------------------------------
def touch_job(job_id: str, group: str, user_key: str, username: str,
              label: Optional[str] = None, total: Optional[int] = None) -> None:
    """Heartbeat/ενημέρωση από τον browser που οδηγεί το run (φάση «client»)."""
    job_id = str(job_id or "").strip()
    if not job_id or not group:
        return
    now = time.time()
    job = _JOBS.get(job_id)
    if job is None:
        job = _JOBS[job_id] = {
            "job_id": job_id, "group": group, "user_key": user_key, "username": username,
            "started": now, "label": "", "total": None, "server_running": False,
            "client_ts": now, "server_ts": 0.0,
        }
    job["client_ts"] = now
    if label is not None:
        job["label"] = str(label)
    if total is not None:
        job["total"] = total


def mark_server_running(job_id: str, group: str, user_key: str, username: str, total: Optional[int]) -> None:
    """Το bulk_compute ξεκίνησε στον server (συνεχίζει ακόμη κι αν ο browser φύγει)."""
    job_id = str(job_id or "").strip()
    if not job_id or not group:
        return
    touch_job(job_id, group, user_key, username, None, total)
    job = _JOBS[job_id]
    job["server_running"] = True
    job["server_ts"] = time.time()


def finish_job(job_id: str) -> None:
    job_id = str(job_id or "").strip()
    if job_id:
        _JOBS.pop(job_id, None)


def drop_client_jobs(user_key: str) -> int:
    """Στο logout: οι φάσεις που οδηγούσε ο browser αυτού του χρήστη δεν τρέχουν πια."""
    drop = [jid for jid, j in _JOBS.items() if j.get("user_key") == user_key and not j.get("server_running")]
    for jid in drop:
        _JOBS.pop(jid, None)
    return len(drop)


def active_jobs(group: str, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Jobs της ομάδας που τρέχουν πραγματικά (φρέσκο heartbeat ή φρέσκια πρόοδος server)."""
    now = now if now is not None else time.time()
    out: List[Dict[str, Any]] = []
    for jid, j in list(_JOBS.items()):
        if j.get("group") != group:
            continue
        server_live = bool(j.get("server_running")) and (now - float(j.get("server_ts") or 0)) < SERVER_STALE_SECONDS
        client_live = (now - float(j.get("client_ts") or 0)) < CLIENT_STALE_SECONDS
        if not (server_live or client_live):
            _JOBS.pop(jid, None)  # νεκρό: καθάρισε
            continue
        prog = _PROGRESS_REGISTRY.get(jid) or {}
        label, percent = j.get("label") or "", None
        if server_live and prog.get("label"):
            label, percent = prog["label"], prog.get("percent")
        out.append({
            "job_id": jid, "label": label, "percent": percent, "total": j.get("total"),
            "phase": "server" if server_live else "client",
            "user_key": j.get("user_key"), "username": j.get("username"), "started": j.get("started"),
        })
    out.sort(key=lambda x: x.get("started") or 0, reverse=True)
    return out
