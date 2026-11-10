import base64
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from backend.state import add_job, get_job, list_jobs, update_job, store_output_file

_MAX_RETRIES = 3   # M8: maximum automatic retries before permanent failure

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def create_job(payload: Dict[str, Any]) -> Dict[str, Any]:
    job_id = str(uuid.uuid4())
    job_data = {
        "job_id": job_id,
        "status": "queued",
        "worker_id": None,
        "payload": payload,
        "created_at": _now(),
        "assigned_at": None,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "outputs": None,
        "retry_count": 0,          # M8: track how many times we've re-queued
    }
    add_job(job_id, job_data)
    return job_data

def get_job_by_id(job_id: str) -> Optional[Dict[str, Any]]:
    return get_job(job_id)

def get_all_jobs() -> List[Dict[str, Any]]:
    return list(list_jobs().values())

def update_job_status(
    job_id: str,
    status: str,
    error: Optional[str] = None,
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
    exit_code: Optional[int] = None,
    output_files: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    job = get_job(job_id)
    if not job:
        return False
    
    current_status = job["status"]
    
    # State transition validation
    valid_transitions = {
        "queued": ["assigned", "failed"],
        "assigned": ["running", "failed"],
        "running": ["completed", "failed"],
        "completed": [],
        "failed": [],
    }
    
    if status not in valid_transitions.get(current_status, []):
        raise ValueError(f"Invalid state transition from {current_status} to {status}")
    
    update_data = {"status": status}
    now = _now()
    
    if status == "assigned":
        update_data["assigned_at"] = now
    elif status == "running":
        update_data["started_at"] = now
    elif status == "completed":
        update_data["completed_at"] = now
    elif status == "failed":
        update_data["completed_at"] = now

    if error is not None:
        update_data["error"] = error
    if stdout is not None:
        update_data["stdout"] = stdout
    if stderr is not None:
        update_data["stderr"] = stderr
    if exit_code is not None:
        update_data["exit_code"] = exit_code

    # Handle output files: decode bytes → store, save metadata in job record
    if output_files:
        metadata = []
        for entry in output_files:
            filename = entry.get("filename", "")
            content_b64 = entry.get("content_b64", "")
            size = entry.get("size", 0)
            try:
                raw_bytes = base64.b64decode(content_b64)
                store_output_file(job_id, filename, raw_bytes)
                metadata.append({"filename": filename, "size": size})
            except Exception:
                pass  # Skip malformed entries
        if metadata:
            update_data["outputs"] = metadata

    update_job(job_id, update_data)
    return True


def requeue_job_for_retry(job_id: str) -> bool:
    """
    Re-queue a job that was running on a dead worker.

    Increments retry_count.  If the job has already been retried
    _MAX_RETRIES times, it is permanently failed instead.

    Returns True if the job was re-queued, False if permanently failed.
    """
    job = get_job(job_id)
    if not job:
        return False

    current_status = job.get("status")
    if current_status not in ("assigned", "running"):
        return False      # already terminal or queued by someone else

    retry_count = job.get("retry_count", 0) + 1

    if retry_count > _MAX_RETRIES:
        update_job(job_id, {
            "status": "failed",
            "error": f"Job failed after {_MAX_RETRIES} retries (worker died each time)",
            "completed_at": _now(),
        })
        return False

    update_job(job_id, {
        "status": "queued",
        "worker_id": None,
        "assigned_at": None,
        "started_at": None,
        "retry_count": retry_count,
        "error": f"Re-queued after worker failure (attempt {retry_count}/{_MAX_RETRIES})",
    })
    return True
