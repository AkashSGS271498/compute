import threading
from typing import Dict, Any, Optional
from datetime import datetime, timezone

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

def update_heartbeat(worker_id: str) -> None:
    with _lock:
        if worker_id in _workers:
            _workers[worker_id]["last_heartbeat"] = _now()
            _workers[worker_id]["status"] = "online"

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
