"""Docker-based execution engine for running workloads in containers."""

import base64
import logging
import os
import shutil
import tempfile
import threading
from typing import Dict, Any, List, Optional

import docker
from docker.errors import DockerException, ImageNotFound, APIError

from worker.execution.base import ExecutionEngine, ExecutionResult
from worker.file_utils import validate_filename, safe_join
from worker.log_buffer import LogBuffer

logger = logging.getLogger("worker.docker")

# ---------------------------------------------------------------------------
# Global live-log registry
#
# Maps  job_id  →  LogBuffer  for all jobs currently running (or recently
# finished).  The worker's SSE endpoint reads from here.
# A cleanup thread removes sealed buffers after a grace period.
# ---------------------------------------------------------------------------
_live_logs: Dict[str, "LogBuffer"] = {}
_live_logs_lock = threading.Lock()
_LOG_RETAIN_SECONDS = 120   # keep sealed buffers this long


def get_log_buffer(job_id: str) -> Optional["LogBuffer"]:
    """Return the LogBuffer for *job_id*, or None if not found."""
    with _live_logs_lock:
        return _live_logs.get(job_id)


def _register_buffer(job_id: str, buf: "LogBuffer") -> None:
    with _live_logs_lock:
        _live_logs[job_id] = buf


def _schedule_cleanup(job_id: str, delay: float) -> None:
    """Remove a sealed LogBuffer from the registry after *delay* seconds."""
    def _remove():
        import time
        time.sleep(delay)
        with _live_logs_lock:
            _live_logs.pop(job_id, None)
        logger.debug(f"[DOCKER] LogBuffer for {job_id} evicted from registry")

    t = threading.Thread(target=_remove, daemon=True, name=f"log-cleanup-{job_id[:8]}")
    t.start()


class DockerExecutionEngine(ExecutionEngine):
    """
    Executes workloads inside Docker containers.

    Security:
    - Only uses a trusted, pre-built image (configured via settings).
    - Workload code is written to an isolated job workspace.
    - Input files are bind-mounted read-only at /workspace/input.
    - Output files are bind-mounted read-write at /workspace/output.
    - No privileged mode, no host network, no docker.sock exposure.
    - CPU, memory, and timeout limits enforced.
    - Network disabled by default.
    - Container and entire job workspace always cleaned up.

    Workspace layout (on the host):
        <tmp>/dc-job-<job_id[:8]>/
            script.py        <- workload code (RO mounted)
            input/           <- decoded input files (RO mounted)
            output/          <- written by container (RW mounted)

    Container sees:
        /workspace/script.py
        /workspace/input/<files>
        /workspace/output/        (writable)
    """

    def __init__(
        self,
        image: str = "distributed-compute-python:latest",
        cpu_limit: float = 1.0,
        memory_limit: str = "512m",
        timeout_seconds: int = 30,
        network_disabled: bool = True,
        max_input_file_bytes: int = 10 * 1024 * 1024,
        max_total_input_bytes: int = 50 * 1024 * 1024,
        max_output_file_bytes: int = 50 * 1024 * 1024,
        max_output_files: int = 50,
        max_input_files: int = 20,
    ):
        self.image = image
        self.cpu_limit = cpu_limit
        self.memory_limit = memory_limit
        self.timeout_seconds = timeout_seconds
        self.network_disabled = network_disabled
        self.max_input_file_bytes = max_input_file_bytes
        self.max_total_input_bytes = max_total_input_bytes
        self.max_output_file_bytes = max_output_file_bytes
        self.max_output_files = max_output_files
        self.max_input_files = max_input_files
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

    # ------------------------------------------------------------------
    # Public execute()
    # ------------------------------------------------------------------

    def execute(self, job_id: str, payload: Dict[str, Any]) -> ExecutionResult:
        """
        Execute a Python workload inside a Docker container.

        Args:
            job_id:  Unique job identifier.
            payload: Must contain:
                       {"type": "python", "code": "<python code>"}
                     May optionally contain:
                       {"inputs": {"filename": "<base64>", ...}}

        Returns:
            ExecutionResult with stdout, stderr, exit_code, error,
            and output_files.
        """
        # 1. Validate payload ──────────────────────────────────────
        workload_type = payload.get("type")
        code = payload.get("code")
        inputs: Dict[str, str] = payload.get("inputs") or {}

        if workload_type != "python":
            return ExecutionResult(
                error=f"Unsupported workload type: {workload_type}"
            )

        if not isinstance(code, str) or not code.strip():
            return ExecutionResult(error="Code must be a non-empty string")

        # 2. Validate input file metadata ──────────────────────────
        if not isinstance(inputs, dict):
            return ExecutionResult(error="'inputs' must be a filename→base64 dict")

        if len(inputs) > self.max_input_files:
            return ExecutionResult(
                error=f"Too many input files: {len(inputs)} > {self.max_input_files}"
            )

        # 3. Set up isolated job workspace ─────────────────────────
        job_dir = None
        container = None
        container_name = f"compute-job-{job_id[:8]}"

        try:
            job_dir = tempfile.mkdtemp(prefix=f"dc-job-{job_id[:8]}-")
            input_dir = os.path.join(job_dir, "input")
            output_dir = os.path.join(job_dir, "output")
            script_path = os.path.join(job_dir, "script.py")

            os.makedirs(input_dir, exist_ok=True)
            os.makedirs(output_dir, exist_ok=True)

            # Write code
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)

            # 4. Decode and write input files ──────────────────────
            total_bytes = 0
            for filename, b64_content in inputs.items():
                safe_name = validate_filename(filename)
                if safe_name is None:
                    return ExecutionResult(
                        error=f"Rejected input filename: {filename!r}"
                    )

                try:
                    file_bytes = base64.b64decode(b64_content)
                except Exception:
                    return ExecutionResult(
                        error=f"Invalid base64 for input file: {filename!r}"
                    )

                if len(file_bytes) > self.max_input_file_bytes:
                    return ExecutionResult(
                        error=f"Input file {filename!r} exceeds size limit "
                              f"({len(file_bytes)} > {self.max_input_file_bytes} bytes)"
                    )

                total_bytes += len(file_bytes)
                if total_bytes > self.max_total_input_bytes:
                    return ExecutionResult(
                        error=f"Total input size exceeds limit "
                              f"({total_bytes} > {self.max_total_input_bytes} bytes)"
                    )

                dest = safe_join(input_dir, safe_name)
                if dest is None:
                    return ExecutionResult(
                        error=f"Path traversal detected for input file: {filename!r}"
                    )

                with open(dest, "wb") as f:
                    f.write(file_bytes)

            logger.info(f"[DOCKER] Starting job: {job_id}")
            logger.info(f"[DOCKER] Image: {self.image}")
            logger.info(f"[DOCKER] Timeout: {self.timeout_seconds}s")
            logger.info(f"[DOCKER] Input files: {list(inputs.keys())}")

            # 5. Build Docker volume mounts ────────────────────────
            # Docker Desktop on Windows needs forward slashes
            def _docker_path(p: str) -> str:
                return p.replace("\\", "/")

            volumes = {
                _docker_path(script_path): {
                    "bind": "/workspace/script.py",
                    "mode": "ro",
                },
                _docker_path(input_dir): {
                    "bind": "/workspace/input",
                    "mode": "ro",
                },
                _docker_path(output_dir): {
                    "bind": "/workspace/output",
                    "mode": "rw",
                },
            }

            # 6. Create and start container ────────────────────────
            client = self._get_client()

            container = client.containers.run(
                image=self.image,
                command=["python", "/workspace/script.py"],
                name=container_name,
                detach=True,
                volumes=volumes,
                privileged=False,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                nano_cpus=int(self.cpu_limit * 1e9),
                mem_limit=self.memory_limit,
                network_disabled=self.network_disabled,
                auto_remove=False,
            )

            logger.info(f"[DOCKER] Container started: {container_name}")

            # 7. Create log buffer and stream logs in background ───
            log_buf = LogBuffer(job_id)
            _register_buffer(job_id, log_buf)
            stdout_accumulator: list[str] = []
            stream_done = threading.Event()

            def _stream_logs() -> None:
                """Read combined stdout/stderr from Docker and feed the buffer."""
                try:
                    for chunk in container.logs(
                        stdout=True,
                        stderr=True,
                        stream=True,
                        follow=True,
                        timestamps=False,
                    ):
                        if isinstance(chunk, bytes):
                            chunk = chunk.decode("utf-8", errors="replace")
                        log_buf.append("stdout", chunk)
                        stdout_accumulator.append(chunk)
                except Exception as exc:
                    logger.debug(f"[DOCKER] Log stream ended: {exc}")
                finally:
                    stream_done.set()

            log_thread = threading.Thread(
                target=_stream_logs,
                daemon=True,
                name=f"log-stream-{job_id[:8]}",
            )
            log_thread.start()

            # 8. Wait for container to exit (with timeout) ─────────
            timed_out = False
            exit_code = -1
            try:
                wait_result = container.wait(timeout=self.timeout_seconds)
                exit_code = wait_result.get("StatusCode", -1)
                stream_done.wait(timeout=5)   # let log thread finish draining
            except Exception:
                timed_out = True
                logger.warning(
                    f"[DOCKER] Job {job_id} timed out after {self.timeout_seconds}s"
                )
                try:
                    container.kill()
                except Exception:
                    pass
                stream_done.wait(timeout=5)

            # 9. Assemble output strings and seal buffer ───────────
            stdout = "".join(stdout_accumulator)
            stderr = self._safe_logs(container, stdout=False, stderr=True)

            final_status = "failed" if (timed_out or exit_code != 0) else "completed"
            log_buf.seal(final_status)
            _schedule_cleanup(job_id, _LOG_RETAIN_SECONDS)

            if timed_out:
                return ExecutionResult(
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=-1,
                    error=f"Job timed out after {self.timeout_seconds} seconds",
                    output_files=self._collect_outputs(output_dir),
                )

            # 10. Collect output files ─────────────────────────────
            output_files = self._collect_outputs(output_dir)

            logger.info(f"[DOCKER] Exit code: {exit_code}")
            logger.info(f"[DOCKER] Output files: {[f['filename'] for f in output_files]}")

            if exit_code == 0:
                return ExecutionResult(
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    output_files=output_files,
                )
            else:
                return ExecutionResult(
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    error="Workload failed",
                    output_files=output_files,
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
            # 10. Always remove container ──────────────────────────
            if container is not None:
                try:
                    container.remove(force=True)
                    logger.info(f"[DOCKER] Container removed: {container_name}")
                except Exception as e:
                    logger.warning(
                        f"[DOCKER] Failed to remove container {container_name}: {e}"
                    )

            # 11. Always remove entire job workspace ───────────────
            if job_dir and os.path.exists(job_dir):
                try:
                    shutil.rmtree(job_dir)
                    logger.debug(f"[DOCKER] Job workspace removed: {job_dir}")
                except Exception as e:
                    logger.warning(
                        f"[DOCKER] Failed to remove workspace {job_dir}: {e}"
                    )

    # ------------------------------------------------------------------
    # Output collection
    # ------------------------------------------------------------------

    def _collect_outputs(self, output_dir: str) -> List[Dict[str, Any]]:
        """
        Walk *output_dir*, read each file, and return a list of
        {"filename": str, "content_b64": str, "size": int} dicts.

        Files that exceed *max_output_file_bytes* are skipped with a
        warning. If more than *max_output_files* files exist, only the
        first N are returned (alphabetical order).
        """
        results = []

        try:
            entries = sorted(os.listdir(output_dir))
        except OSError:
            return results

        for name in entries:
            if len(results) >= self.max_output_files:
                logger.warning(
                    f"[DOCKER] Output file limit ({self.max_output_files}) reached; "
                    f"skipping remaining files"
                )
                break

            safe_name = validate_filename(name)
            if safe_name is None:
                logger.warning(f"[DOCKER] Skipping unsafe output filename: {name!r}")
                continue

            full_path = safe_join(output_dir, safe_name)
            if full_path is None or not os.path.isfile(full_path):
                continue

            file_size = os.path.getsize(full_path)
            if file_size > self.max_output_file_bytes:
                logger.warning(
                    f"[DOCKER] Output file {name!r} too large "
                    f"({file_size} > {self.max_output_file_bytes}); skipping"
                )
                continue

            try:
                with open(full_path, "rb") as f:
                    content = f.read()
                results.append({
                    "filename": safe_name,
                    "content_b64": base64.b64encode(content).decode("ascii"),
                    "size": file_size,
                })
            except OSError as e:
                logger.warning(f"[DOCKER] Could not read output file {name!r}: {e}")

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
