from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class WorkerData(BaseModel):
    worker_id: str = Field(..., description="Unique identifier for the worker, usually hostname")
    hostname: str
    ip_address: str
    port: int
    cpu_count: int
    memory_mb: int
    status: str = Field(default="online", description="online / offline")
    last_seen: datetime = Field(default_factory=datetime.utcnow)
