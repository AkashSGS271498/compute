import threading
from typing import Dict, Any

_lock = threading.Lock()

# In‑memory registry of workers and jobs
_workers: Dict[str, Dict[str, Any]] = {}
_jobs: Dict[str, Dict[str, Any]] = {}


def register_worker(worker_id: str, data: Dict[str, Any]) -> None:
    with _lock:
        _workers[worker_id] = data
        _workers[worker_id]["last_heartbeat"] = threading.Event()  # placeholder


def update_heartbeat(worker_id: str) -> None:
    with _lock:
        if worker_id in _workers:
            _workers[worker_id]["last_heartbeat"] = threading.Event()


def get_worker(worker_id: str) -> Dict[str, Any] | None:
    with _lock:
        return _workers.get(worker_id)


def list_workers() -> Dict[str, Dict[str, Any]]:
    with _lock:
        return dict(_workers)


def add_job(job_id: str, data: Dict[str, Any]) -> None:
    with _lock:
        _jobs[job_id] = data


def set_job_result(job_id: str, result: Dict[str, Any]) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(result)
            _jobs[job_id]["status"] = "completed"


def get_job(job_id: str) -> Dict[str, Any] | None:
    with _lock:
        return _jobs.get(job_id)
