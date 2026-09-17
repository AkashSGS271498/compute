import os
import socket

try:
    from pydantic_settings import BaseSettings
    from pydantic import Field

    class WorkerSettings(BaseSettings):
        host: str = Field(
            default="0.0.0.0",
            alias="WORKER_HOST",
        )

        port: int = Field(
            default=8001,
            alias="WORKER_PORT",
        )

        worker_name: str = Field(
            default_factory=socket.gethostname,
            alias="WORKER_NAME",
        )

        backend_url: str = Field(
            default="http://127.0.0.1:8000",
            alias="BACKEND_URL",
        )

        backend_secret: str = Field(
            default="supersecret",
            alias="BACKEND_SECRET",
        )

        log_level: str = Field(
            default="INFO",
            alias="LOG_LEVEL",
        )

        # Docker execution settings
        docker_image: str = Field(
            default="distributed-compute-python:latest",
            alias="DOCKER_IMAGE",
        )

        job_cpu_limit: float = Field(
            default=1.0,
            alias="JOB_CPU_LIMIT",
        )

        job_memory_limit: str = Field(
            default="512m",
            alias="JOB_MEMORY_LIMIT",
        )

        job_timeout_seconds: int = Field(
            default=30,
            alias="JOB_TIMEOUT_SECONDS",
        )

        job_network_disabled: bool = Field(
            default=True,
            alias="JOB_NETWORK_DISABLED",
        )

        class Config:
            env_file = ".env"
            env_file_encoding = "utf-8"
            extra = "ignore"


    settings = WorkerSettings()


except ImportError:

    # Fallback if pydantic-settings is not yet installed

    class SimpleSettings:

        def __init__(self):
            self.host = os.getenv(
                "WORKER_HOST",
                "0.0.0.0",
            )

            self.port = int(
                os.getenv(
                    "WORKER_PORT",
                    "8001",
                )
            )

            self.worker_name = os.getenv(
                "WORKER_NAME",
                socket.gethostname(),
            )

            self.backend_url = os.getenv(
                "BACKEND_URL",
                "http://127.0.0.1:8000",
            )

            self.backend_secret = os.getenv(
                "BACKEND_SECRET",
                "supersecret",
            )

            self.log_level = os.getenv(
                "LOG_LEVEL",
                "INFO",
            )

            # Docker execution settings
            self.docker_image = os.getenv(
                "DOCKER_IMAGE",
                "distributed-compute-python:latest",
            )

            self.job_cpu_limit = float(os.getenv(
                "JOB_CPU_LIMIT",
                "1.0",
            ))

            self.job_memory_limit = os.getenv(
                "JOB_MEMORY_LIMIT",
                "512m",
            )

            self.job_timeout_seconds = int(os.getenv(
                "JOB_TIMEOUT_SECONDS",
                "30",
            ))

            self.job_network_disabled = os.getenv(
                "JOB_NETWORK_DISABLED",
                "true",
            ).lower() in ("true", "1", "yes")


    settings = SimpleSettings()

