import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

_lock = threading.Lock()

# In‑memory registry of workers and jobs
_workers: Dict[str, Dict[str, Any]] = {}
_jobs: Dict[str, Dict[str, Any]] = {}

# Output file bytes: job_id → {filename → bytes}
_output_store: Dict[str, Dict[str, bytes]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_worker(worker_id: str, data: Dict[str, Any]) -> None:
    with _lock:
        _workers[worker_id] = data
        _workers[worker_id]["last_heartbeat"] = _now()
        _workers[worker_id]["status"] = "online"
        if "current_job_id" not in _workers[worker_id]:
            _workers[worker_id]["current_job_id"] = None

def update_heartbeat(worker_id: str, stats: Optional[Dict[str, Any]] = None) -> None:
    with _lock:
        if worker_id in _workers:
            _workers[worker_id]["last_heartbeat"] = _now()
            _workers[worker_id]["status"] = "online"
            if stats:
                _workers[worker_id]["stats"] = stats
                # Mirror the derived worker_state into the top-level record
                _workers[worker_id]["worker_state"] = stats.get("worker_state", "UNKNOWN")

def update_worker(worker_id: str, data: Dict[str, Any]) -> None:
    with _lock:
        if worker_id in _workers:
            _workers[worker_id].update(data)

def get_worker(worker_id: str) -> Dict[str, Any] | None:
    with _lock:
        return _workers.get(worker_id)

def list_workers() -> Dict[str, Dict[str, Any]]:
    with _lock:
        return dict(_workers)

def add_job(job_id: str, data: Dict[str, Any]) -> None:
    with _lock:
        _jobs[job_id] = data

def update_job(job_id: str, data: Dict[str, Any]) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(data)

def get_job(job_id: str) -> Dict[str, Any] | None:
    with _lock:
        return _jobs.get(job_id)

def list_jobs() -> Dict[str, Dict[str, Any]]:
    with _lock:
        return dict(_jobs)


# ------------------------------------------------------------------
# Offline detection
# ------------------------------------------------------------------

_OFFLINE_THRESHOLD_SECONDS = 90

def mark_offline_workers() -> None:
    """
    Mark workers as OFFLINE if their last heartbeat is older than the
    threshold.  Call this lazily before returning worker lists.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_OFFLINE_THRESHOLD_SECONDS)
    with _lock:
        for w in _workers.values():
            last_hb = w.get("last_heartbeat")
            if last_hb:
                try:
                    hb_time = datetime.fromisoformat(last_hb)
                    # Make offset-aware if naive
                    if hb_time.tzinfo is None:
                        hb_time = hb_time.replace(tzinfo=timezone.utc)
                    if hb_time < cutoff:
                        w["status"] = "offline"
                        w["worker_state"] = "OFFLINE"
                except Exception:
                    pass

# ------------------------------------------------------------------
# Output file store
# ------------------------------------------------------------------

def store_output_file(job_id: str, filename: str, content: bytes) -> None:
    """Store raw output file bytes for a job."""
    with _lock:
        if job_id not in _output_store:
            _output_store[job_id] = {}
        _output_store[job_id][filename] = content

def get_output_file(job_id: str, filename: str) -> Optional[bytes]:
    """Retrieve raw output file bytes, or None if not found."""
    with _lock:
        return _output_store.get(job_id, {}).get(filename)
