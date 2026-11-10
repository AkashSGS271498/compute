import json
import logging
import os
import socket
import sys
import threading
import time
import asyncio
from datetime import datetime, timezone


# Add current directory to path if needed
sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)


try:
    from worker.config import settings
except ImportError:
    from config import settings


# ---------------------------------------------------------
# Logging
# ---------------------------------------------------------

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("worker")

# Worker process start time (used for uptime calculation)
_WORKER_START_TIME = time.time()


# ---------------------------------------------------------
# Network utilities
# ---------------------------------------------------------

def get_local_ips() -> list[str]:
    """Discover outward-facing LAN IPv4 addresses of this machine."""

    ips = []

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        primary_ip = s.getsockname()[0]
        if primary_ip and not primary_ip.startswith("127."):
            ips.append(primary_ip)
        s.close()
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if ":" not in ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass

    return ips if ips else ["127.0.0.1"]


# ---------------------------------------------------------
# Worker status
# ---------------------------------------------------------

def get_worker_status_data() -> dict:
    return {
        "message": "Hello from worker",
        "status": "online",
        "hostname": settings.worker_name,
        "port": settings.port,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "milestone": 1,
    }


# ---------------------------------------------------------
# Startup banner
# ---------------------------------------------------------

def print_startup_banner():
    local_ips = get_local_ips()

    banner = f"""
===================================================================
      PERSONAL DISTRIBUTED COMPUTE NETWORK - WORKER NODE
===================================================================
 Worker Hostname : {settings.worker_name}
 Listening Host  : {settings.host} (All network interfaces)
 Listening Port  : {settings.port}
 Local IP(s)     : {", ".join(local_ips)}

 Laptop A (Controller) connection command:
   curl http://{local_ips[0]}:{settings.port}/
   or
   python scripts/test_milestone1.py --worker-url http://{local_ips[0]}:{settings.port}
===================================================================
"""
    print(banner, flush=True)


# ---------------------------------------------------------
# FastAPI server
# ---------------------------------------------------------

try:
    import uvicorn

    from fastapi import FastAPI, HTTPException, Depends
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.security import (
        HTTPBearer,
        HTTPAuthorizationCredentials,
    )

    import httpx


    # -----------------------------------------------------
    # FastAPI application
    # -----------------------------------------------------

    app = FastAPI(
        title="Personal Distributed Compute - Worker",
        description="Worker node API for remote workload execution",
        version="0.1.0",
    )


    # -----------------------------------------------------
    # CORS
    # -----------------------------------------------------

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


    # -----------------------------------------------------
    # Global State
    # -----------------------------------------------------

    WORKER_STATE = {
        "current_job_id": None
    }

    # -----------------------------------------------------
    # Resource collection
    # -----------------------------------------------------

    try:
        from worker.resources import collect_stats
    except ImportError:
        from resources import collect_stats

    # Prime the psutil CPU counter so first call returns a real value
    try:
        import psutil as _psutil
        _psutil.cpu_percent(interval=None)
    except ImportError:
        pass

    def _get_stats() -> dict:
        return collect_stats(
            current_job_id=WORKER_STATE["current_job_id"],
            start_time=_WORKER_START_TIME,
        )

    # -----------------------------------------------------
    # Root endpoint
    # -----------------------------------------------------

    @app.get("/")
    def read_root():
        """Root endpoint returning greeting and worker status."""
        logger.info("Received request from controller on GET /")
        return get_worker_status_data()


    # -----------------------------------------------------
    # Health endpoint
    # -----------------------------------------------------

    @app.get("/health")
    def health_check():
        """Health check endpoint for liveness probes."""
        return {
            "status": "healthy",
            "worker": settings.worker_name,
        }


    # -----------------------------------------------------
    # Stats endpoint (Milestone 6)
    # -----------------------------------------------------

    @app.get("/stats")
    def get_stats():
        """
        Return live resource stats for this worker.

        CPU usage, RAM, current job, uptime, worker state.
        """
        return _get_stats()


    # -----------------------------------------------------
    # Docker Execution Engine
    # -----------------------------------------------------

    from worker.execution.docker import DockerExecutionEngine

    _engine = DockerExecutionEngine(
        image=settings.docker_image,
        cpu_limit=settings.job_cpu_limit,
        memory_limit=settings.job_memory_limit,
        timeout_seconds=settings.job_timeout_seconds,
        network_disabled=settings.job_network_disabled,
        max_input_file_bytes=settings.max_input_file_bytes,
        max_total_input_bytes=settings.max_total_input_bytes,
        max_output_file_bytes=settings.max_output_file_bytes,
        max_output_files=settings.max_output_files,
        max_input_files=settings.max_input_files,
    )

    # Check Docker availability on startup
    logger.info("[DOCKER] Checking Docker availability...")
    if _engine.is_available():
        logger.info("[DOCKER] Docker available")
    else:
        logger.error("[DOCKER] Docker is unavailable -- job execution will fail")

    # -----------------------------------------------------
    # Background job execution
    # -----------------------------------------------------

    def _execute_job_background(job_id: str, payload: dict):
        """Run the workload in Docker and report results to backend."""
        _auth_headers = {
            "Authorization": f"Bearer {settings.backend_secret}",
            "Content-Type": "application/json",
        }

        try:
            # Report RUNNING
            httpx.post(
                f"{settings.backend_url}/jobs/{job_id}/status",
                json={"status": "running"},
                headers=_auth_headers,
            )

            # Execute in Docker
            result = _engine.execute(job_id, payload)

            # Report result
            if result.success:
                httpx.post(
                    f"{settings.backend_url}/jobs/{job_id}/status",
                    json={
                        "status": "completed",
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "exit_code": result.exit_code,
                        "output_files": result.output_files,
                    },
                    headers=_auth_headers,
                )
            else:
                httpx.post(
                    f"{settings.backend_url}/jobs/{job_id}/status",
                    json={
                        "status": "failed",
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "exit_code": result.exit_code,
                        "error": result.error or "Workload failed",
                        "output_files": result.output_files,
                    },
                    headers=_auth_headers,
                )

        except Exception as e:
            logger.error(f"Job execution error for {job_id}: {e}")
            try:
                httpx.post(
                    f"{settings.backend_url}/jobs/{job_id}/status",
                    json={
                        "status": "failed",
                        "error": str(e),
                    },
                    headers=_auth_headers,
                )
            except Exception:
                pass

        finally:
            WORKER_STATE["current_job_id"] = None
            logger.info(f"Worker available again (job {job_id} finished)")

    # -----------------------------------------------------
    # Assign job endpoint
    # -----------------------------------------------------

    @app.post("/jobs/assign")
    def assign_job(req: dict):
        job_id  = req.get("job_id")
        payload = req.get("payload", {})

        if not job_id:
            raise HTTPException(status_code=400, detail="Missing job_id")

        if WORKER_STATE["current_job_id"] is not None:
            return {
                "job_id": job_id,
                "status": "rejected",
                "reason": "worker_busy",
            }

        # Resource requirements check (defence-in-depth; scheduler already checked)
        requirements = payload.get("requirements") or {}
        if requirements:
            stats = _get_stats()
            needed_mem  = requirements.get("memory_mb", 0)
            needed_cpu  = requirements.get("cpu_cores", 0)
            avail_mem   = stats.get("memory_available_mb", 0)
            avail_cpu   = stats.get("cpu_cores", 1)

            if stats.get("psutil_available") and needed_mem > 0 and avail_mem < needed_mem:
                return {
                    "job_id": job_id,
                    "status": "rejected",
                    "reason": f"insufficient_memory: need {needed_mem} MB, have {avail_mem} MB",
                }
            if stats.get("psutil_available") and needed_cpu > 0 and avail_cpu < needed_cpu:
                return {
                    "job_id": job_id,
                    "status": "rejected",
                    "reason": f"insufficient_cpu: need {needed_cpu} cores, have {avail_cpu}",
                }

        WORKER_STATE["current_job_id"] = job_id
        logger.info(f"Accepted job: {job_id}")

        threading.Thread(
            target=_execute_job_background,
            args=(job_id, payload),
            daemon=True,
        ).start()

        return {
            "job_id": job_id,
            "status": "accepted",
            "worker_id": settings.worker_name,
        }

    @app.post("/jobs/clear")
    def clear_job():
        """Used by tests to clear worker state."""
        WORKER_STATE["current_job_id"] = None
        return {"status": "cleared"}

    # -----------------------------------------------------
    # Log streaming endpoint (Milestone 7)
    # -----------------------------------------------------

    from fastapi.responses import StreamingResponse
    try:
        from worker.execution.docker import get_log_buffer
    except ImportError:
        from execution.docker import get_log_buffer

    @app.get("/jobs/{job_id}/stream")
    def stream_job_logs(job_id: str):
        """
        Stream live log output for *job_id* as Server-Sent Events.

        Events:
            data: {"seq": int, "source": "stdout"|"stderr", "line": str}
            event: done  data: {"status": "completed"|"failed"|"unknown"}

        Returns 404 immediately if the job has never been seen by this worker.
        If the job is currently assigned (buffer not yet created) we wait up to
        10 s for the Docker execution thread to register the LogBuffer.
        """
        import time as _time
        import json as _json

        buf = get_log_buffer(job_id)

        if buf is None:
            # Only wait if this worker currently owns the job (buffer is about
            # to be registered by the execution thread).
            if WORKER_STATE.get("current_job_id") == job_id:
                deadline = _time.monotonic() + 10.0
                while buf is None and _time.monotonic() < deadline:
                    _time.sleep(0.1)
                    buf = get_log_buffer(job_id)

        if buf is None:
            raise HTTPException(
                status_code=404,
                detail="No log buffer for job (not assigned to this worker or already evicted)",
            )

        def _event_generator():
            for entry in buf.iter_from(start_seq=0, timeout=600):
                payload = _json.dumps({
                    "seq":    entry["seq"],
                    "source": entry["source"],
                    "line":   entry["line"],
                })
                yield f"data: {payload}\n\n"
            # Emit final "done" event
            final = buf.final_status or "unknown"
            yield f"event: done\ndata: {_json.dumps({'status': final})}\n\n"

        return StreamingResponse(
            _event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # -----------------------------------------------------
    # Run job endpoint (legacy)
    # -----------------------------------------------------

    @app.post("/run")
    async def run_job(command: dict):
        cmd  = command.get("command")
        args = command.get("args", [])

        if not cmd:
            raise HTTPException(status_code=400, detail="Missing command")

        try:
            proc = await asyncio.create_subprocess_exec(
                cmd,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            return {
                "output": stdout.decode(),
                "error": stderr.decode(),
                "returncode": proc.returncode,
            }
        except Exception as e:
            logger.error(f"Job execution failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))


    # -----------------------------------------------------
    # Start server
    # -----------------------------------------------------

    def start_server():
        print_startup_banner()

        _auth_headers = {
            "Authorization": f"Bearer {settings.backend_secret}",
            "Content-Type": "application/json",
        }

        try:
            # Allow tests/deployments to override the registration host.
            # WORKER_REGISTER_HOST=127.0.0.1 makes the extra worker reachable
            # from the backend scheduler on the same machine.
            _real_ip = os.environ.get("WORKER_REGISTER_HOST") or get_local_ips()[0]
            resp = httpx.post(
                f"{settings.backend_url}/register",
                json={
                    "worker_name": settings.worker_name,
                    "host": _real_ip,
                    "port": settings.port,
                },
                headers=_auth_headers,
            )
            resp.raise_for_status()
            worker_id = resp.json().get("worker_id")
            logger.info(f"Worker registered with ID: {worker_id}")

            if worker_id:
                # Send an immediate heartbeat+stats so backend has resource
                # data from second 0 (instead of waiting up to 30 s).
                try:
                    httpx.post(
                        f"{settings.backend_url}/heartbeat",
                        json={"worker_id": worker_id, "stats": _get_stats()},
                        headers=_auth_headers,
                    )
                    logger.debug("Initial heartbeat with stats sent")
                except Exception as e:
                    logger.warning(f"Initial heartbeat failed: {e}")

                def _heartbeat_loop(wid_ref: list, hdrs: dict):
                    """
                    Sends a heartbeat every 30 s.
                    On 404 (backend restarted) the worker re-registers
                    and immediately pushes a fresh heartbeat.
                    """
                    while True:
                        try:
                            stats   = _get_stats()
                            hb_resp = httpx.post(
                                f"{settings.backend_url}/heartbeat",
                                json={"worker_id": wid_ref[0], "stats": stats},
                                headers=hdrs,
                            )

                            if hb_resp.status_code == 404:
                                logger.warning(
                                    "Heartbeat 404 - backend restarted? Re-registering..."
                                )
                                reg = httpx.post(
                                    f"{settings.backend_url}/register",
                                    json={
                                        "worker_name": settings.worker_name,
                                        "host": get_local_ips()[0],
                                        "port": settings.port,
                                    },
                                    headers=hdrs,
                                )
                                reg.raise_for_status()
                                new_id = reg.json().get("worker_id")
                                if new_id:
                                    wid_ref[0] = new_id
                                    logger.info(
                                        f"Re-registered with new worker_id: {new_id}"
                                    )
                                    # Push fresh stats immediately
                                    httpx.post(
                                        f"{settings.backend_url}/heartbeat",
                                        json={"worker_id": new_id, "stats": _get_stats()},
                                        headers=hdrs,
                                    )
                            else:
                                hb_resp.raise_for_status()
                                logger.debug(
                                    f"Heartbeat sent (state={stats.get('worker_state')})"
                                )

                        except Exception as e:
                            logger.error(f"Heartbeat error: {e}")

                        time.sleep(30)

                threading.Thread(
                    target=_heartbeat_loop,
                    args=([worker_id], _auth_headers),
                    daemon=True,
                ).start()

        except Exception as e:
            logger.error(f"Worker registration failed: {e}")

        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level.lower(),
        )


# ---------------------------------------------------------
# Standard library fallback
# ---------------------------------------------------------

except ImportError:

    from http.server import (
        BaseHTTPRequestHandler,
        HTTPServer,
    )

    class WorkerHTTPHandler(BaseHTTPRequestHandler):

        def do_GET(self):
            if self.path in ("/", "/health"):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                data = get_worker_status_data()
                self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))
                logger.info("Responded to GET %s with status 200", self.path)
            else:
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Not Found"}).encode("utf-8"))

        def log_message(self, format, *args):
            logger.info("%s - %s", self.address_string(), format % args)

    def start_server():
        print_startup_banner()
        logger.warning(
            "FastAPI/Uvicorn not found; using standard library HTTP server. "
            "Install worker/requirements.txt for the full stack."
        )
        server = HTTPServer((settings.host, settings.port), WorkerHTTPHandler)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nWorker server stopped gracefully.")
            server.server_close()


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

if __name__ == "__main__":
    start_server()
