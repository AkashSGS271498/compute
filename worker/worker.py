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


# ---------------------------------------------------------
# Network utilities
# ---------------------------------------------------------

def get_local_ips() -> list[str]:
    """Discover outward-facing LAN IPv4 addresses of this machine."""

    ips = []

    try:
        # Connect to a dummy public IP to detect default gateway interface
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

            if (
                ":" not in ip
                and not ip.startswith("127.")
                and ip not in ips
            ):
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
 Local IP(s)     : {', '.join(local_ips)}

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
# Try FastAPI first; fallback to standard library
# http.server if dependencies are not installed.

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
    # Docker Execution Engine
    # -----------------------------------------------------

    from worker.execution.docker import DockerExecutionEngine

    _engine = DockerExecutionEngine(
        image=settings.docker_image,
        cpu_limit=settings.job_cpu_limit,
        memory_limit=settings.job_memory_limit,
        timeout_seconds=settings.job_timeout_seconds,
        network_disabled=settings.job_network_disabled,
    )

    # Check Docker availability on startup
    logger.info("[DOCKER] Checking Docker availability...")
    if _engine.is_available():
        logger.info("[DOCKER] Docker available")
    else:
        logger.error("[DOCKER] Docker is unavailable — job execution will fail")

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
        job_id = req.get("job_id")
        payload = req.get("payload", {})

        if not job_id:
            raise HTTPException(status_code=400, detail="Missing job_id")
            
        if WORKER_STATE["current_job_id"] is not None:
            return {
                "job_id": job_id,
                "status": "rejected",
                "reason": "worker_busy"
            }
            
        WORKER_STATE["current_job_id"] = job_id
        logger.info(f"Accepted job: {job_id}")

        # Spawn background execution thread (non-blocking)
        threading.Thread(
            target=_execute_job_background,
            args=(job_id, payload),
            daemon=True,
        ).start()
        
        return {
            "job_id": job_id,
            "status": "accepted",
            "worker_id": settings.worker_name
        }

    @app.post("/jobs/clear")
    def clear_job():
        # Used by tests to clear worker state
        WORKER_STATE["current_job_id"] = None
        return {"status": "cleared"}

    # -----------------------------------------------------
    # Run job endpoint
    # -----------------------------------------------------

    @app.post("/run")
    async def run_job(command: dict):
        """
        Execute a simple command payload.

        Expected JSON:
        {
            "command": "python",
            "args": ["script.py"]
        }

        Returns stdout/stderr and return code.
        """

        cmd = command.get("command")
        args = command.get("args", [])

        if not cmd:
            raise HTTPException(
                status_code=400,
                detail="Missing command",
            )

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
            logger.error(
                f"Job execution failed: {e}"
            )

            raise HTTPException(
                status_code=500,
                detail=str(e),
            )


    # -----------------------------------------------------
    # Start server
    # -----------------------------------------------------

    def start_server():
        print_startup_banner()

        # Auth header required by the backend
        _auth_headers = {
            "Authorization": f"Bearer {settings.backend_secret}",
            "Content-Type": "application/json",
        }

        # Register worker with backend and start heartbeat thread
        try:
            _real_ip = get_local_ips()[0]  # actual LAN IP, not 0.0.0.0
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

            logger.info(
                f"Worker registered with ID: {worker_id}"
            )

            # ---------------------------------------------
            # Heartbeat
            # ---------------------------------------------

            if worker_id:

                def _heartbeat_loop(wid, hdrs):

                    while True:

                        try:
                            hb_resp = httpx.post(
                                f"{settings.backend_url}/heartbeat",
                                json={"worker_id": wid},
                                headers=hdrs,
                            )

                            hb_resp.raise_for_status()

                            logger.debug("Heartbeat sent")

                        except Exception as e:
                            logger.error(
                                f"Heartbeat error: {e}"
                            )

                        time.sleep(30)


                threading.Thread(
                    target=_heartbeat_loop,
                    args=(worker_id, _auth_headers),
                    daemon=True,
                ).start()


        except Exception as e:
            logger.error(
                f"Worker registration failed: {e}"
            )


        # ---------------------------------------------
        # Start FastAPI server
        # ---------------------------------------------

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

    # Standard library fallback so Milestone 1 works
    # even before pip install.

    from http.server import (
        BaseHTTPRequestHandler,
        HTTPServer,
    )


    class WorkerHTTPHandler(BaseHTTPRequestHandler):

        def do_GET(self):

            if self.path in ("/", "/health"):

                self.send_response(200)

                self.send_header(
                    "Content-Type",
                    "application/json",
                )

                self.end_headers()

                data = get_worker_status_data()

                self.wfile.write(
                    json.dumps(
                        data,
                        indent=2,
                    ).encode("utf-8")
                )

                logger.info(
                    "Responded to GET %s with status 200",
                    self.path,
                )

            else:

                self.send_response(404)

                self.send_header(
                    "Content-Type",
                    "application/json",
                )

                self.end_headers()

                self.wfile.write(
                    json.dumps(
                        {"error": "Not Found"}
                    ).encode("utf-8")
                )


        def log_message(self, format, *args):

            logger.info(
                "%s - %s",
                self.address_string(),
                format % args,
            )


    # -----------------------------------------------------
    # Start fallback server
    # -----------------------------------------------------

    def start_server():

        print_startup_banner()

        logger.warning(
            "FastAPI/Uvicorn not found; using standard "
            "library HTTP server. Install worker/requirements.txt "
            "for the full stack."
        )

        server = HTTPServer(
            (settings.host, settings.port),
            WorkerHTTPHandler,
        )

        try:
            server.serve_forever()

        except KeyboardInterrupt:

            print(
                "\nWorker server stopped gracefully."
            )

            server.server_close()


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

if __name__ == "__main__":
    start_server()

