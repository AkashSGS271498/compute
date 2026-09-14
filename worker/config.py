import os
import socket
from typing import Optional

try:
    from pydantic_settings import BaseSettings
    from pydantic import Field

    class WorkerSettings(BaseSettings):
        host: str = Field(default="0.0.0.0", alias="WORKER_HOST")
        port: int = Field(default=8001, alias="WORKER_PORT")
        worker_name: str = Field(default_factory=socket.gethostname, alias="WORKER_NAME")
        backend_url: str = Field(default="http://127.0.0.1:8000", alias="BACKEND_URL")
    backend_secret: str = Field(default="supersecret", alias="BACKEND_SECRET")

        class Config:
            env_file = ".env"
            env_file_encoding = "utf-8"
            extra = "ignore"

    settings = WorkerSettings()

except ImportError:
    # Fallback if pydantic-settings is not yet installed
    class SimpleSettings:
        def __init__(self):
            self.host = os.getenv("WORKER_HOST", "0.0.0.0")
            self.port = int(os.getenv("WORKER_PORT", "8001"))
            self.worker_name = os.getenv("WORKER_NAME", socket.gethostname())
            self.log_level = os.getenv("LOG_LEVEL", "INFO")

    settings = SimpleSettings()
