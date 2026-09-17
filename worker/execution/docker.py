"""Docker-based execution engine for running workloads in containers."""

import logging
import os
import shutil
import tempfile
from typing import Dict, Any

import docker
from docker.errors import DockerException, ImageNotFound, APIError

from worker.execution.base import ExecutionEngine, ExecutionResult

logger = logging.getLogger("worker.docker")


class DockerExecutionEngine(ExecutionEngine):
    """
    Executes workloads inside Docker containers.

    Security:
    - Only uses a trusted, pre-built image (configured via settings).
    - Workload code is written to a temp dir and bind-mounted read-only.
    - No privileged mode, no host network, no docker.sock exposure.
    - CPU, memory, and timeout limits enforced.
    - Network disabled by default.
    - Container and temp files always cleaned up.

    Limitations:
    - Docker is an isolation boundary, not a perfect security sandbox.
      A determined attacker with a kernel exploit could escape.
    - Only supports "python" workload type for now.
    """

    def __init__(
        self,
        image: str = "distributed-compute-python:latest",
        cpu_limit: float = 1.0,
        memory_limit: str = "512m",
        timeout_seconds: int = 30,
        network_disabled: bool = True,
    ):
        self.image = image
        self.cpu_limit = cpu_limit
        self.memory_limit = memory_limit
        self.timeout_seconds = timeout_seconds
        self.network_disabled = network_disabled
        self._client = None

    def _get_client(self) -> docker.DockerClient:
        """Lazily create and return a Docker client."""
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def is_available(self) -> bool:
        """Check if Docker daemon is reachable."""
        try:
            client = self._get_client()
            client.ping()
            logger.info("[DOCKER] Docker available")
            return True
        except DockerException as e:
            logger.error(f"[DOCKER] Docker is unavailable: {e}")
            self._client = None
            return False

    def image_exists(self) -> bool:
        """Check if the configured workload image exists locally."""
        try:
            client = self._get_client()
            client.images.get(self.image)
            return True
        except ImageNotFound:
            return False
        except DockerException:
            return False

    def execute(self, job_id: str, payload: Dict[str, Any]) -> ExecutionResult:
        """
        Execute a Python workload inside a Docker container.

        Args:
            job_id: Unique job identifier.
            payload: Must contain {"type": "python", "code": "<python code>"}.

        Returns:
            ExecutionResult with stdout, stderr, exit_code, error.
        """
        # ----------------------------------------------------------
        # 1. Validate payload
        # ----------------------------------------------------------
        workload_type = payload.get("type")
        code = payload.get("code")

        if workload_type != "python":
            return ExecutionResult(
                error=f"Unsupported workload type: {workload_type}"
            )

        if not isinstance(code, str) or not code.strip():
            return ExecutionResult(error="Code must be a non-empty string")

        # ----------------------------------------------------------
        # 2. Create temp directory and write script
        # ----------------------------------------------------------
        tmp_dir = None
        container = None
        container_name = f"compute-job-{job_id[:8]}"

        try:
            tmp_dir = tempfile.mkdtemp(prefix=f"dc-job-{job_id[:8]}-")
            script_path = os.path.join(tmp_dir, "script.py")

            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)

            logger.info(f"[DOCKER] Starting job: {job_id}")
            logger.info(f"[DOCKER] Image: {self.image}")
            logger.info(f"[DOCKER] CPU limit: {self.cpu_limit}")
            logger.info(f"[DOCKER] Memory limit: {self.memory_limit}")
            logger.info(f"[DOCKER] Timeout: {self.timeout_seconds}s")
            logger.info(f"[DOCKER] Network disabled: {self.network_disabled}")

            # ----------------------------------------------------------
            # 3. Create and start container
            # ----------------------------------------------------------
            client = self._get_client()

            # Convert tmp_dir to a Docker-compatible path on Windows
            # Docker Desktop on Windows needs forward slashes
            host_path = tmp_dir.replace("\\", "/")

            container = client.containers.run(
                image=self.image,
                command=["python", "/workspace/script.py"],
                name=container_name,
                detach=True,
                # Security: read-only mount, no privileged, no extra caps
                volumes={host_path: {"bind": "/workspace", "mode": "ro"}},
                privileged=False,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                # Resource limits
                nano_cpus=int(self.cpu_limit * 1e9),
                mem_limit=self.memory_limit,
                # Network
                network_disabled=self.network_disabled,
                # Cleanup
                auto_remove=False,  # we remove manually after collecting logs
            )

            logger.info(f"[DOCKER] Container started: {container_name}")

            # ----------------------------------------------------------
            # 4. Wait for completion with timeout
            # ----------------------------------------------------------
            try:
                result = container.wait(timeout=self.timeout_seconds)
                exit_code = result.get("StatusCode", -1)
            except Exception:
                # Timeout or other wait error
                logger.warning(
                    f"[DOCKER] Job {job_id} timed out after {self.timeout_seconds}s"
                )
                try:
                    container.kill()
                except Exception:
                    pass
                return ExecutionResult(
                    stdout=self._safe_logs(container, stdout=True, stderr=False),
                    stderr=self._safe_logs(container, stdout=False, stderr=True),
                    exit_code=-1,
                    error=f"Job timed out after {self.timeout_seconds} seconds",
                )

            # ----------------------------------------------------------
            # 5. Capture output
            # ----------------------------------------------------------
            stdout = self._safe_logs(container, stdout=True, stderr=False)
            stderr = self._safe_logs(container, stdout=False, stderr=True)

            logger.info(f"[DOCKER] Execution finished")
            logger.info(f"[DOCKER] Exit code: {exit_code}")

            if exit_code == 0:
                return ExecutionResult(
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                )
            else:
                return ExecutionResult(
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    error="Workload failed",
                )

        except ImageNotFound:
            msg = f"Docker image not found: {self.image}"
            logger.error(f"[DOCKER] {msg}")
            return ExecutionResult(error=msg)

        except APIError as e:
            msg = f"Docker API error: {e.explanation or str(e)}"
            logger.error(f"[DOCKER] {msg}")
            return ExecutionResult(error=msg)

        except DockerException as e:
            msg = f"Docker error: {str(e)}"
            logger.error(f"[DOCKER] {msg}")
            return ExecutionResult(error=msg)

        except Exception as e:
            msg = f"Unexpected execution error: {str(e)}"
            logger.error(f"[DOCKER] {msg}")
            return ExecutionResult(error=msg)

        finally:
            # ----------------------------------------------------------
            # 6. Always clean up container
            # ----------------------------------------------------------
            if container is not None:
                try:
                    container.remove(force=True)
                    logger.info(f"[DOCKER] Container removed: {container_name}")
                except Exception as e:
                    logger.warning(
                        f"[DOCKER] Failed to remove container {container_name}: {e}"
                    )

            # ----------------------------------------------------------
            # 7. Always clean up temp files
            # ----------------------------------------------------------
            if tmp_dir and os.path.exists(tmp_dir):
                try:
                    shutil.rmtree(tmp_dir)
                    logger.debug(f"[DOCKER] Temp dir removed: {tmp_dir}")
                except Exception as e:
                    logger.warning(
                        f"[DOCKER] Failed to remove temp dir {tmp_dir}: {e}"
                    )

    @staticmethod
    def _safe_logs(
        container, stdout: bool = True, stderr: bool = False
    ) -> str:
        """Safely retrieve container logs."""
        try:
            return container.logs(stdout=stdout, stderr=stderr).decode(
                "utf-8", errors="replace"
            )
        except Exception:
            return ""
