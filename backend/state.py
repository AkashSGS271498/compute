"""
backend/state.py — M9: thin shim that delegates to backend/database.py.

The public API is identical to the previous in-memory version so that
main.py, job_manager.py, and scheduler.py require zero changes.
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from backend.database import (
    db_register_worker,
    db_update_heartbeat,
    db_update_worker,
    db_get_worker,
    db_list_workers,
    db_add_job,
    db_update_job,
    db_get_job,
    db_list_jobs,
    db_store_output_file,
    db_get_output_file,
)

# ------------------------------------------------------------------
# Offline threshold
# ------------------------------------------------------------------
_OFFLINE_THRESHOLD_SECONDS = 60


# ------------------------------------------------------------------
# Worker operations
# ------------------------------------------------------------------

def register_worker(worker_id: str, data: Dict[str, Any]) -> None:
    db_register_worker(worker_id, data)


def update_heartbeat(worker_id: str, stats: Optional[Dict[str, Any]] = None) -> None:
    db_update_heartbeat(worker_id, stats)


def update_worker(worker_id: str, data: Dict[str, Any]) -> None:
    db_update_worker(worker_id, data)


def get_worker(worker_id: str) -> Optional[Dict[str, Any]]:
    return db_get_worker(worker_id)


def list_workers() -> Dict[str, Dict[str, Any]]:
    return db_list_workers()


# ------------------------------------------------------------------
# Offline detection helpers
# ------------------------------------------------------------------

def _is_expired(last_hb: Optional[str]) -> bool:
    if not last_hb:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_OFFLINE_THRESHOLD_SECONDS)
    try:
        hb_time = datetime.fromisoformat(last_hb)
        if hb_time.tzinfo is None:
            hb_time = hb_time.replace(tzinfo=timezone.utc)
        return hb_time < cutoff
    except Exception:
        return False


def mark_offline_workers() -> None:
    """
    Lazily mark workers as OFFLINE if their heartbeat has expired.
    Call before returning worker lists so the UI sees fresh state.
    """
    workers = db_list_workers()
    for wid, w in workers.items():
        if _is_expired(w.get("last_heartbeat")):
            db_update_worker(wid, {"status": "offline", "worker_state": "OFFLINE"})


def get_dead_workers() -> List[str]:
    """Return worker_ids whose heartbeat has expired (without mutating state)."""
    workers = db_list_workers()
    return [
        wid for wid, w in workers.items()
        if _is_expired(w.get("last_heartbeat"))
    ]


def get_jobs_for_worker(worker_id: str) -> List[Dict[str, Any]]:
    """Return all assigned/running jobs for a given worker."""
    jobs = db_list_jobs()
    return [
        j for j in jobs.values()
        if j.get("worker_id") == worker_id
        and j.get("status") in ("assigned", "running")
    ]


def requeue_job(job_id: str, retry_count: int) -> None:
    """Reset a job to 'queued' for rescheduling."""
    db_update_job(job_id, {
        "status": "queued",
        "worker_id": None,
        "assigned_at": None,
        "started_at": None,
        "retry_count": retry_count,
    })


# ------------------------------------------------------------------
# Job operations
# ------------------------------------------------------------------

def add_job(job_id: str, data: Dict[str, Any]) -> None:
    db_add_job(job_id, data)


def update_job(job_id: str, data: Dict[str, Any]) -> None:
    db_update_job(job_id, data)


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    return db_get_job(job_id)


def list_jobs() -> Dict[str, Dict[str, Any]]:
    return db_list_jobs()


# ------------------------------------------------------------------
# Output file store
# ------------------------------------------------------------------

def store_output_file(job_id: str, filename: str, content: bytes) -> None:
    db_store_output_file(job_id, filename, content)


def get_output_file(job_id: str, filename: str) -> Optional[bytes]:
    return db_get_output_file(job_id, filename)
