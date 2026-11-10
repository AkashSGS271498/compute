"""
Job scheduler — selects the best available worker for a job.

Milestone 6: resource-aware scheduling.
- Workers must be online and not busy.
- Workers must satisfy the job'"'"'s minimum cpu_cores and memory_mb requirements.
- Among qualifying workers the one with the MOST available RAM is chosen
  (avoids starving future large jobs by grabbing the only high-RAM machine).
"""

import logging
import httpx
from backend.state import list_workers, update_worker, mark_offline_workers
from backend.services.job_manager import update_job_status, get_job_by_id, update_job

logger = logging.getLogger("backend.scheduler")

_DEFAULT_CPU_CORES = 1
_DEFAULT_MEMORY_MB = 256


def _worker_available_ram(w_info: dict) -> int:
    """Return the available RAM (MB) cached from the last heartbeat, or 0."""
    stats = w_info.get("stats") or {}
    return stats.get("memory_available_mb", 0)


def _worker_cpu_cores(w_info: dict) -> int:
    """Return physical CPU cores cached from last heartbeat, or a safe default."""
    stats = w_info.get("stats") or {}
    cores = stats.get("cpu_cores", 0)
    return cores if cores > 0 else _DEFAULT_CPU_CORES


def schedule_job(job_id: str) -> None:
    job = get_job_by_id(job_id)
    if not job or job["status"] != "queued":
        return

    # Extract resource requirements from the job payload
    payload      = job.get("payload") or {}
    requirements = payload.get("requirements") or {}
    need_cpu     = int(requirements.get("cpu_cores", _DEFAULT_CPU_CORES))
    need_mem     = int(requirements.get("memory_mb", _DEFAULT_MEMORY_MB))

    # Refresh offline state before picking
    mark_offline_workers()
    workers = list_workers()

    # Build list of qualifying workers
    candidates = []
    for wid, w_info in workers.items():
        # Must be online and idle
        if w_info.get("status") != "online":
            continue
        if w_info.get("current_job_id") is not None:
            continue
        # worker_state must be AVAILABLE (or UNKNOWN — psutil not installed)
        wstate = w_info.get("worker_state", "AVAILABLE")
        if wstate == "BUSY" or wstate == "OFFLINE":
            continue

        stats = w_info.get("stats") or {}
        psutil_ok = stats.get("psutil_available", False)

        if psutil_ok:
            avail_mem  = stats.get("memory_available_mb", 0)
            avail_cpu  = stats.get("cpu_cores", 1)
            if avail_cpu < need_cpu:
                logger.info(
                    f"Worker {wid} skipped: cpu {avail_cpu} < required {need_cpu}"
                )
                continue
            if avail_mem < need_mem:
                logger.info(
                    f"Worker {wid} skipped: mem {avail_mem} MB < required {need_mem} MB"
                )
                continue

        # Qualifying — score by available RAM descending (most RAM first)
        candidates.append((wid, w_info, _worker_available_ram(w_info)))

    if not candidates:
        logger.info(
            f"No available worker satisfies job {job_id} "
            f"(need cpu>={need_cpu}, mem>={need_mem} MB)"
        )
        return

    # Pick the worker with the most available RAM
    candidates.sort(key=lambda x: x[2], reverse=True)
    selected_worker_id, selected_worker, _ = candidates[0]

    host = selected_worker.get("host", "127.0.0.1")
    port = selected_worker.get("port", 8001)
    worker_url = f"http://{host}:{port}"

    try:
        update_job_status(job_id, "assigned")
        update_job(job_id, {"worker_id": selected_worker_id})
        update_worker(selected_worker_id, {"current_job_id": job_id})

        logger.info(f"Assigning job {job_id} to worker {selected_worker_id}")

        resp = httpx.post(
            f"{worker_url}/jobs/assign",
            json={"job_id": job_id, "payload": payload},
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "accepted":
            reason = data.get("reason", "unknown")
            raise ValueError(f"Worker rejected job: {reason}")

    except Exception as e:
        logger.error(f"Failed to assign job {job_id} to worker {selected_worker_id}: {e}")
        # Reset worker and re-queue the job so the scheduler loop can retry
        update_worker(selected_worker_id, {"current_job_id": None})
        update_job(job_id, {"worker_id": None})
        try:
            # Reset to queued — bypass the state machine directly via update_job
            from backend.state import update_job as _update_job_state
            _update_job_state(job_id, {
                "status": "queued",
                "worker_id": None,
                "assigned_at": None,
            })
        except Exception as inner:
            logger.error(f"Failed to re-queue job {job_id}: {inner}")
