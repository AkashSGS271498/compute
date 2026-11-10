import base64
import uuid
import logging
import httpx as _httpx
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import Response, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from backend.config import settings
from backend.models import (
    RegisterRequest,
    RegisterResponse,
    HeartbeatRequest,
    JobCreateRequest,
    JobResponse,
    JobListResponse,
    JobStatusUpdateRequest,
)
from backend.services.job_manager import create_job, get_job_by_id, get_all_jobs, update_job_status, requeue_job_for_retry
from backend.services.scheduler import schedule_job
from backend.state import (
    register_worker,
    update_heartbeat,
    get_worker,
    list_workers,
    update_worker,
    get_output_file,
    mark_offline_workers,
    get_dead_workers,
    get_jobs_for_worker,
)
from backend.database import db_counts

app = FastAPI(
    title="Personal Distributed Compute – Backend",
    description="Central coordination service for workers and controller.",
    version="0.1.0",
)

logger = logging.getLogger("backend")
logger.setLevel(settings.log_level.upper())
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logger.addHandler(handler)

# ------------------------------------------------------------------
# Background loops (M7 scheduler retry + M8 fault tolerance)
# ------------------------------------------------------------------
import threading as _threading
import time as _time

def _scheduler_loop() -> None:
    """
    Every 2 s:
      1. Detect dead workers (heartbeat expired) → mark offline → recover jobs
      2. Re-queue jobs on dead workers (up to MAX_RETRIES times)
      3. Reschedule all QUEUED jobs so nothing stays stuck
    """
    while True:
        _time.sleep(2)
        try:
            # --- M8: Dead worker detection & job recovery ----------------
            dead_wids = get_dead_workers()
            for wid in dead_wids:
                worker = get_worker(wid)
                if not worker:
                    continue
                if worker.get("status") == "offline":
                    continue   # already processed

                # Mark offline
                update_worker(wid, {"status": "offline", "worker_state": "OFFLINE"})
                logger.warning(f"[M8] Worker {wid[:8]} detected offline — recovering jobs")

                # Find jobs assigned to this dead worker
                stranded = get_jobs_for_worker(wid)
                for job in stranded:
                    jid = job["job_id"]
                    requeued = requeue_job_for_retry(jid)
                    if requeued:
                        logger.info(f"[M8] Job {jid[:8]} re-queued (retry {job.get('retry_count',0)+1})")
                    else:
                        logger.warning(f"[M8] Job {jid[:8]} permanently failed (max retries exceeded)")

            # --- M7/M8: Reschedule all QUEUED jobs -----------------------
            queued = [j for j in get_all_jobs() if j.get("status") == "queued"]
            for job in queued:
                _threading.Thread(
                    target=schedule_job,
                    args=(job["job_id"],),
                    daemon=True,
                ).start()
        except Exception as exc:
            logger.debug(f"[scheduler-loop] error: {exc}")

_threading.Thread(target=_scheduler_loop, daemon=True, name="scheduler-loop").start()

security = HTTPBearer()

# ------------------------------------------------------------------
# File size limits (can be moved to BackendSettings later)
# ------------------------------------------------------------------
_MAX_INPUT_FILE_BYTES  = 10 * 1024 * 1024   # 10 MB per file
_MAX_TOTAL_INPUT_BYTES = 50 * 1024 * 1024   # 50 MB total
_MAX_INPUT_FILES       = 20

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    if token != settings.secret_token:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token

# ------------------------------------------------------------------
# Filename validation (mirrors worker/file_utils.py)
# ------------------------------------------------------------------
import re as _re
_UNSAFE_RE = _re.compile(r'[/\\:\*\?"<>\|]')

def _validate_filename(name: str) -> bool:
    if not isinstance(name, str) or not name:
        return False
    if "\x00" in name or any(ord(c) < 32 for c in name):
        return False
    if _UNSAFE_RE.search(name):
        return False
    if not name.strip("."):
        return False
    if name.startswith("-"):
        return False
    if len(name) > 255:
        return False
    import os as _os
    if _os.path.basename(name) != name:
        return False
    return True

# ------------------------------------------------------------------
# Worker registration & heartbeat
# ------------------------------------------------------------------

@app.post("/register", response_model=RegisterResponse)
def register(req: RegisterRequest):
    worker_id = str(uuid.uuid4())
    register_worker(worker_id, req.dict())
    logger.info(f"Worker registered: {req.worker_name} -> {worker_id}")
    return RegisterResponse(status="registered", worker_id=worker_id)


@app.post("/heartbeat")
def heartbeat(req: HeartbeatRequest):
    if not get_worker(req.worker_id):
        raise HTTPException(status_code=404, detail="Worker not found")
    update_heartbeat(req.worker_id, stats=req.stats)
    logger.info(f"Heartbeat received from {req.worker_id}")
    return {"status": "alive"}

# ------------------------------------------------------------------
# Job creation
# ------------------------------------------------------------------

@app.post("/jobs", response_model=JobResponse)
def create_new_job(req: JobCreateRequest, token: str = Depends(verify_token)):
    # Validate input files in payload, if present
    payload = req.payload
    inputs = payload.get("inputs")
    if inputs is not None:
        if not isinstance(inputs, dict):
            raise HTTPException(status_code=400, detail="'inputs' must be a filename→base64 dict")

        if len(inputs) > _MAX_INPUT_FILES:
            raise HTTPException(
                status_code=400,
                detail=f"Too many input files: {len(inputs)} > {_MAX_INPUT_FILES}"
            )

        total_bytes = 0
        for filename, b64_content in inputs.items():
            if not _validate_filename(filename):
                raise HTTPException(
                    status_code=400,
                    detail=f"Rejected input filename: {filename!r}"
                )
            try:
                file_bytes = base64.b64decode(b64_content)
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid base64 for input file: {filename!r}"
                )
            if len(file_bytes) > _MAX_INPUT_FILE_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Input file {filename!r} exceeds {_MAX_INPUT_FILE_BYTES} bytes"
                )
            total_bytes += len(file_bytes)
            if total_bytes > _MAX_TOTAL_INPUT_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Total input size exceeds {_MAX_TOTAL_INPUT_BYTES} bytes"
                )

    job_data = create_job(payload)
    logger.info(f"Job {job_data['job_id']} created")
    
    import threading
    threading.Thread(target=schedule_job, args=(job_data["job_id"],), daemon=True).start()
    
    return JobResponse(**job_data)

@app.get("/jobs", response_model=JobListResponse)
def list_all_jobs(token: str = Depends(verify_token)):
    jobs = get_all_jobs()
    return JobListResponse(jobs=[JobResponse(**j) for j in jobs])

@app.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, token: str = Depends(verify_token)):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse(**job)

# ------------------------------------------------------------------
# Job status update (called by worker — no token auth)
# ------------------------------------------------------------------

@app.post("/jobs/{job_id}/status")
def update_status(job_id: str, req: JobStatusUpdateRequest):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    try:
        update_job_status(
            job_id, req.status,
            error=req.error,
            stdout=req.stdout,
            stderr=req.stderr,
            exit_code=req.exit_code,
            output_files=req.output_files,
        )
        logger.info(f"Job {job_id} status updated to {req.status}")
        
        if req.status in ["completed", "failed"]:
            worker_id = job.get("worker_id")
            if worker_id:
                update_worker(worker_id, {"current_job_id": None})
                logger.info(f"Worker {worker_id} is now available")
                
        return {"status": "success"}
    except ValueError as e:
        logger.error(f"Invalid state transition for job {job_id}: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# ------------------------------------------------------------------
# Log stream proxy (Milestone 7)
# ------------------------------------------------------------------

@app.get("/jobs/{job_id}/stream")
def stream_job_logs(job_id: str, token: str = Depends(verify_token)):
    """
    Proxy the live SSE log stream from the worker that owns this job.

    The backend looks up the worker's host/port from the job record and
    opens a streaming connection to  GET http://<worker>/jobs/{job_id}/stream,
    forwarding each SSE chunk verbatim to the calling client.

    Returns:
        text/event-stream — same format as the worker endpoint.

    Raises:
        404 if job or worker not found.
        502 if the worker stream is unavailable.
    """
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    worker_id = job.get("worker_id")
    if not worker_id:
        raise HTTPException(
            status_code=404,
            detail="Job has not been assigned to a worker yet"
        )

    worker = get_worker(worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")

    host = worker.get("host", "127.0.0.1")
    port = worker.get("port", 8001)
    worker_stream_url = f"http://{host}:{port}/jobs/{job_id}/stream"

    def _proxy():
        try:
            with _httpx.stream("GET", worker_stream_url, timeout=620) as resp:
                if resp.status_code == 404:
                    # Worker says no buffer — job may have already finished and
                    # buffer evicted; emit a synthetic done event.
                    final_status = job.get("status", "unknown")
                    import json as _json
                    yield f"event: done\ndata: {_json.dumps({'status': final_status})}\n\n"
                    return
                for chunk in resp.iter_text():
                    if chunk:
                        yield chunk
        except _httpx.RequestError as exc:
            import json as _json
            yield f"event: error\ndata: {_json.dumps({'detail': str(exc)})}\n\n"

    return StreamingResponse(
        _proxy(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ------------------------------------------------------------------
# Output file download
# ------------------------------------------------------------------

@app.get("/jobs/{job_id}/outputs/{filename}")
def download_output(job_id: str, filename: str, token: str = Depends(verify_token)):
    """
    Download a single output file produced by a completed job.

    Returns raw bytes with Content-Type application/octet-stream.
    """
    # Validate job exists and is complete
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Validate filename (prevent any traversal attempts in the URL)
    if not _validate_filename(filename):
        raise HTTPException(status_code=400, detail=f"Invalid filename: {filename!r}")

    raw = get_output_file(job_id, filename)
    if raw is None:
        raise HTTPException(
            status_code=404,
            detail=f"Output file {filename!r} not found for job {job_id}"
        )

    # Sniff a basic MIME type from extension
    import mimetypes as _mimetypes
    mime, _ = _mimetypes.guess_type(filename)
    content_type = mime or "application/octet-stream"

    return Response(
        content=raw,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(raw)),
        },
    )

# ------------------------------------------------------------------
# Workers list + per-worker stats
# ------------------------------------------------------------------

@app.get("/workers")
def workers(token: str = Depends(verify_token)):
    mark_offline_workers()   # lazy offline detection
    return list_workers()


@app.get("/workers/{worker_id}/stats")
def worker_stats(worker_id: str, token: str = Depends(verify_token)):
    """
    Return the cached resource snapshot from the worker's last heartbeat.

    Fields: cpu_cores, cpu_percent, memory_total_mb, memory_available_mb,
            memory_percent, worker_state, uptime_seconds, current_job_id.
    """
    mark_offline_workers()
    w = get_worker(worker_id)
    if not w:
        raise HTTPException(status_code=404, detail="Worker not found")
    stats = w.get("stats")
    if not stats:
        raise HTTPException(
            status_code=404,
            detail="No stats available yet — worker has not sent a heartbeat with stats"
        )
    return stats


# ------------------------------------------------------------------
# M9: DB stats / health
# ------------------------------------------------------------------

@app.get("/db/stats")
def db_stats(token: str = Depends(verify_token)):
    """Return SQLite row counts — useful to verify persistence is working."""
    return db_counts()


@app.get("/stats")
def cluster_stats(token: str = Depends(verify_token)):
    """Cluster-wide summary: worker counts, job counts, DB stats."""
    mark_offline_workers()
    all_workers = list_workers()
    all_jobs = get_all_jobs()
    online = sum(1 for w in all_workers.values() if w.get("status") == "online")
    return {
        "workers": {
            "total": len(all_workers),
            "online": online,
            "offline": len(all_workers) - online,
        },
        "jobs": {
            "total": len(all_jobs),
            "queued":    sum(1 for j in all_jobs if j.get("status") == "queued"),
            "assigned":  sum(1 for j in all_jobs if j.get("status") == "assigned"),
            "running":   sum(1 for j in all_jobs if j.get("status") == "running"),
            "completed": sum(1 for j in all_jobs if j.get("status") == "completed"),
            "failed":    sum(1 for j in all_jobs if j.get("status") == "failed"),
        },
        "storage": db_counts(),
    }
