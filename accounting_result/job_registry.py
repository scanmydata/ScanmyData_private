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

from datetime import datetime
from typing import Any, Dict, Optional

_ABORT_REGISTRY: Dict[str, Dict[str, Any]] = {}
_PROGRESS_REGISTRY: Dict[str, Dict[str, Any]] = {}


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


def get_progress(job_id: str) -> Dict[str, Any]:
    return dict(_PROGRESS_REGISTRY.get(str(job_id or "").strip(), {}))


def clear_progress(job_id: str) -> None:
    job_id = str(job_id or "").strip()
    if job_id:
        _PROGRESS_REGISTRY.pop(job_id, None)
