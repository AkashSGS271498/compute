import uuid
import logging
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from backend.config import settings
from backend.models import (
    RegisterRequest,
    RegisterResponse,
    HeartbeatRequest,
    JobSubmitRequest,
    JobSubmitResponse,
    JobResultResponse,
)
from backend.state import (
    register_worker,
    update_heartbeat,
    get_worker,
    list_workers,
    add_job,
    set_job_result,
    get_job,
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

@app.post("/submit", response_model=JobSubmitResponse)
def submit_job(req: JobSubmitRequest, token: str = Depends(verify_token)):
    # Simple first‑available worker selection
    available = list_workers()
    if not available:
        raise HTTPException(status_code=503, detail="No workers online")
    # Pick first worker (could be improved)
    worker_id, worker_info = next(iter(available.items()))
    job_id = str(uuid.uuid4())
    job_data = {
        "job_id": job_id,
        "worker_id": worker_id,
        "command": req.command,
        "args": req.args,
        "payload": req.payload,
        "status": "queued",
    }
    add_job(job_id, job_data)
    logger.info(f"Job {job_id} queued for worker {worker_id}")
    # In a real system we would POST to the worker /run endpoint here.
    # For the MVP we let the controller call the worker directly via the backend later.
    return JobSubmitResponse(job_id=job_id, status="queued")

@app.get("/result/{job_id}", response_model=JobResultResponse)
def get_result(job_id: str, token: str = Depends(verify_token)):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResultResponse(
        job_id=job_id,
        status=job.get("status", "unknown"),
        output=job.get("output"),
        error=job.get("error"),
    )

@app.get("/workers")
def workers(token: str = Depends(verify_token)):
    return list_workers()
