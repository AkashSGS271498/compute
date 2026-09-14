import json
import logging
import os
import socket
import sys
from datetime import datetime, timezone

# Add current directory to path if needed
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from worker.config import settings
except ImportError:
    from config import settings

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("worker")


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
            if ":" not in ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass

    return ips if ips else ["127.0.0.1"]


def get_worker_status_data() -> dict:
    return {
        "message": "Hello from worker",
        "status": "online",
        "hostname": settings.worker_name,
        "port": settings.port,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "milestone": 1,
    }


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


# Try FastAPI first; fallback to standard library http.server if dependencies are pending
try:
    import uvicorn
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(
        title="Personal Distributed Compute - Worker",
        description="Worker node API for remote workload execution",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    def read_root():
        """Root endpoint returning greeting and worker status."""
        logger.info("Received request from controller on GET /")
        return get_worker_status_data()

    @app.get("/health")
    def health_check():
        """Health check endpoint for liveness probes."""
        return {"status": "healthy", "worker": settings.worker_name}

    def start_server():
        print_startup_banner()
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level.lower(),
        )

except ImportError:
    # Standard library fallback so Milestone 1 works even before pip install
    from http.server import BaseHTTPRequestHandler, HTTPServer

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


if __name__ == "__main__":
    start_server()
