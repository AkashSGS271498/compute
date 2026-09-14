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

class JobSubmitRequest(BaseModel):
    job_name: str
    command: str
    args: List[str] = []
    payload: Optional[Dict[str, Any]] = None

class JobSubmitResponse(BaseModel):
    job_id: str
    status: str

class JobResultResponse(BaseModel):
    job_id: str
    status: str
    output: Optional[str] = None
    error: Optional[str] = None
