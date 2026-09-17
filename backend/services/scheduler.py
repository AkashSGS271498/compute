import logging
import httpx
from backend.state import list_workers, update_worker
from backend.services.job_manager import update_job_status, get_job_by_id, update_job

logger = logging.getLogger("backend.scheduler")

def schedule_job(job_id: str) -> None:
    job = get_job_by_id(job_id)
    if not job or job["status"] != "queued":
        return

    workers = list_workers()
    selected_worker_id = None
    selected_worker = None

    # First available worker strategy
    for wid, w_info in workers.items():
        if w_info.get("status") == "online" and w_info.get("current_job_id") is None:
            selected_worker_id = wid
            selected_worker = w_info
            break

    if not selected_worker_id:
        logger.info(f"No available workers to schedule job {job_id}")
        return

    worker_url = f"http://{selected_worker['host']}:{selected_worker['port']}"
    
    try:
        # Optimistically mark as assigned and set worker's current job
        update_job_status(job_id, "assigned")
        update_job(job_id, {"worker_id": selected_worker_id})
        update_worker(selected_worker_id, {"current_job_id": job_id})
        
        logger.info(f"Assigning job {job_id} to worker {selected_worker_id}")
        
        # Send assignment to worker
        resp = httpx.post(
            f"{worker_url}/jobs/assign",
            json={"job_id": job_id, "payload": job["payload"]},
            timeout=5.0
        )
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("status") != "accepted":
            raise ValueError(f"Worker rejected job: {data.get('reason')}")
            
    except Exception as e:
        logger.error(f"Failed to assign job {job_id} to worker {selected_worker_id}: {e}")
        # Revert state on failure
        update_worker(selected_worker_id, {"current_job_id": None})
        update_job(job_id, {"worker_id": None})
        try:
            update_job_status(job_id, "failed", error=str(e))
        except ValueError:
            pass # ignore invalid transition if it was never marked assigned
