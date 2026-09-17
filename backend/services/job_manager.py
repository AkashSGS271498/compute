import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from backend.state import add_job, get_job, list_jobs, update_job

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
            
    update_job(job_id, update_data)
    return True

