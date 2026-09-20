from pydantic import BaseModel
from typing import List, Dict, Any, Optional

class RegisterRequest(BaseModel):
    worker_name: str
    host: str
    port: int

class RegisterResponse(BaseModel):
    status: str
    worker_id: str

class HeartbeatRequest(BaseModel):
    worker_id: str
    stats: Optional[Dict[str, Any]] = None

class JobCreateRequest(BaseModel):
    payload: Dict[str, Any]

class OutputFileMeta(BaseModel):
    """Metadata (no raw bytes) for an output file."""
    filename: str
    size: int

class JobResponse(BaseModel):
    job_id: str
    status: str
    worker_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    assigned_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None
    # Output file metadata only — raw bytes served via /outputs/{filename}
    outputs: Optional[List[OutputFileMeta]] = None

class JobListResponse(BaseModel):
    jobs: List[JobResponse]

class JobStatusUpdateRequest(BaseModel):
    status: str
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None
    # Worker sends full file content as base64; backend stores and strips
    output_files: Optional[List[Dict[str, Any]]] = None

class WorkerAssignRequest(BaseModel):
    job_id: str
    payload: Dict[str, Any]

class WorkerAssignResponse(BaseModel):
    job_id: str
    status: str
    worker_id: Optional[str] = None
    reason: Optional[str] = None
