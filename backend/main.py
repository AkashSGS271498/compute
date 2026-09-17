import uuid
import logging
from fastapi import FastAPI, HTTPException, Depends
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
from backend.services.job_manager import create_job, get_job_by_id, get_all_jobs, update_job_status
from backend.services.scheduler import schedule_job
from backend.state import (
    register_worker,
    update_heartbeat,
    get_worker,
    list_workers,
    update_worker,
)

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

security = HTTPBearer()

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    if token != settings.secret_token:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token

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
    update_heartbeat(req.worker_id)
    logger.info(f"Heartbeat received from {req.worker_id}")
    return {"status": "alive"}

@app.post("/jobs", response_model=JobResponse)
def create_new_job(req: JobCreateRequest, token: str = Depends(verify_token)):
    job_data = create_job(req.payload)
    logger.info(f"Job {job_data['job_id']} created")
    
    # Attempt to schedule immediately
    # Using background tasks would be better, but doing it synchronously for MVP
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

@app.post("/jobs/{job_id}/status")
def update_status(job_id: str, req: JobStatusUpdateRequest):
    # This endpoint is called by the worker, so no token auth for now (same as heartbeat)
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
        )
        logger.info(f"Job {job_id} status updated to {req.status}")
        
        # If job is completed or failed, free up the worker
        if req.status in ["completed", "failed"]:
            worker_id = job.get("worker_id")
            if worker_id:
                update_worker(worker_id, {"current_job_id": None})
                logger.info(f"Worker {worker_id} is now available")
                
        return {"status": "success"}
    except ValueError as e:
        logger.error(f"Invalid state transition for job {job_id}: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/workers")
def workers(token: str = Depends(verify_token)):
    return list_workers()
