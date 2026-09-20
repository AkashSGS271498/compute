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

        # File transfer limits
        max_input_file_bytes: int = Field(
            default=10 * 1024 * 1024,  # 10 MB
            alias="MAX_INPUT_FILE_BYTES",
        )

        max_total_input_bytes: int = Field(
            default=50 * 1024 * 1024,  # 50 MB
            alias="MAX_TOTAL_INPUT_BYTES",
        )

        max_output_file_bytes: int = Field(
            default=50 * 1024 * 1024,  # 50 MB
            alias="MAX_OUTPUT_FILE_BYTES",
        )

        max_output_files: int = Field(
            default=50,
            alias="MAX_OUTPUT_FILES",
        )

        max_input_files: int = Field(
            default=20,
            alias="MAX_INPUT_FILES",
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

            # File transfer limits
            self.max_input_file_bytes = int(os.getenv(
                "MAX_INPUT_FILE_BYTES",
                str(10 * 1024 * 1024),
            ))

            self.max_total_input_bytes = int(os.getenv(
                "MAX_TOTAL_INPUT_BYTES",
                str(50 * 1024 * 1024),
            ))

            self.max_output_file_bytes = int(os.getenv(
                "MAX_OUTPUT_FILE_BYTES",
                str(50 * 1024 * 1024),
            ))

            self.max_output_files = int(os.getenv(
                "MAX_OUTPUT_FILES",
                "50",
            ))

            self.max_input_files = int(os.getenv(
                "MAX_INPUT_FILES",
                "20",
            ))


    settings = SimpleSettings()

