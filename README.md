# Personal Distributed Compute Network

> Turn your idle laptops into an on-demand distributed compute cluster.

---

## 1. Project Overview & Problem Statement

Modern workflows often involve having multiple personal machines (e.g., Laptop A and Laptop B). While working on Laptop A, Laptop B frequently sits idle with unused CPU and RAM resources.

**The Solution**: A lightweight personal distributed compute system where:
- A **central backend** (running on Laptop A) coordinates all jobs and workers.
- One or more **worker nodes** (running on other laptops) auto-register with the backend, execute jobs, and send heartbeats.
- A **controller script** (on Laptop A) submits jobs to the backend, which dispatches them to available workers and returns the result.

---

## 2. Architecture

### Milestone 2 – Central Backend + Worker Registration

```text
+-----------------------------------------------+
|              Laptop A (Controller)             |
|                                                |
|   ┌─────────────────────────────────────┐      |
|   │  Backend  (FastAPI :8000)           │      |
|   │                                     │      |
|   │  POST /register   ◄── Worker        │      |
|   │  POST /heartbeat  ◄── Worker        │      |
|   │  POST /submit     ◄── Controller    │      |
|   │  GET  /workers    ◄── Controller    │      |
|   └─────────────────────────────────────┘      |
|                                                |
|   scripts/submit_job.py  (Controller CLI)      |
+-------------------+----------------------------+
                    |
         Local Network / Wi-Fi
                    |
+-------------------+----------------------------+
|              Laptop B (Worker)                 |
|                                                |
|   ┌─────────────────────────────────────┐      |
|   │  Worker  (FastAPI/Uvicorn :8001)    │      |
|   │                                     │      |
|   │  POST /run     ◄── Backend dispatch │      |
|   │  GET  /health  ◄── Liveness probe   │      |
|   │  GET  /        ◄── Status           │      |
|   └─────────────────────────────────────┘      |
+-----------------------------------------------+
```

**Job flow:**
1. Worker starts → auto-registers its real LAN IP + port with the backend.
2. Worker sends a heartbeat to the backend every 30 seconds.
3. Controller runs `submit_job.py` → queries backend for available workers → submits job → calls worker `/run` → prints result.

---

## 3. Directory Structure

```text
distributed-compute/
├── backend/
│   ├── __init__.py
│   ├── main.py           # FastAPI app: /register /heartbeat /submit /result /workers
│   ├── config.py         # Backend settings (host, port, secret token)
│   ├── models.py         # Pydantic request/response models
│   ├── state.py          # In-memory worker registry and job store
│   └── requirements.txt  # fastapi, uvicorn, pydantic-settings, httpx
├── worker/
│   ├── worker.py         # Worker FastAPI service (stdlib fallback included)
│   ├── config.py         # Worker settings (host, port, backend_url, backend_secret)
│   ├── requirements.txt  # fastapi, uvicorn, pydantic-settings, httpx, psutil
│   └── __init__.py
├── scripts/
│   ├── submit_job.py     # Controller CLI: submit a job via the backend
│   └── test_milestone1.py# Milestone 1 ping/latency test
├── workloads/
│   └── examples/
│       ├── hello.py      # Minimal hello-world workload
│       └── cpu_test.py   # CPU stress test benchmark
├── .env.example          # Sample configuration
├── .gitignore
└── README.md
```

---

## 4. Setup & Running

### Prerequisites
- Python 3.12+ on both Laptop A and Laptop B.
- Both laptops connected to the same Wi-Fi or LAN.
- A shared secret token (default: `supersecret`) configured on both the backend and worker.

---

### Step 1: Start the Backend on Laptop A

```powershell
cd distributed-compute

# Activate virtual environment
.\venv\Scripts\Activate.ps1

# Install backend dependencies (once)
pip install -r backend\requirements.txt

# Set environment variables (optional – these are the defaults)
$env:BACKEND_HOST   = "0.0.0.0"
$env:BACKEND_PORT   = "8000"
$env:BACKEND_SECRET = "supersecret"
$env:BACKEND_LOG_LEVEL = "info"

# Start the backend
uvicorn backend.main:app `
    --host $env:BACKEND_HOST `
    --port $env:BACKEND_PORT `
    --log-level $env:BACKEND_LOG_LEVEL
```

Expected output:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

> **Windows Firewall** – if workers on other machines can't reach port 8000, run this on Laptop A as Administrator:
> ```powershell
> New-NetFirewallRule -DisplayName "DC Backend" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
> ```

---

### Step 2: Start the Worker on Laptop B

```powershell
cd worker-project-root   # wherever you copied the worker folder

# Activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install worker dependencies
pip install -r worker\requirements.txt

# Point the worker at Laptop A's backend
$env:BACKEND_URL    = "http://<LAPTOP_A_IP>:8000"   # e.g. http://192.168.1.10:8000
$env:BACKEND_SECRET = "supersecret"

# Start the worker
python worker\worker.py
```

Expected output on Laptop B:
```
===================================================================
      PERSONAL DISTRIBUTED COMPUTE NETWORK - WORKER NODE
===================================================================
 Worker Hostname : DESKTOP-LEN
 Listening Port  : 8001
 Local IP(s)     : 172.20.10.5
===================================================================
INFO:worker Worker registered with ID: <uuid>
INFO:     Uvicorn running on http://0.0.0.0:8001
```

Expected output on Laptop A backend console:
```
INFO:backend Worker registered: DESKTOP-LEN -> <uuid>
INFO:backend Heartbeat received from <uuid>
```

> **Windows Firewall** on Laptop B (if blocked):
> ```powershell
> New-NetFirewallRule -DisplayName "DC Worker" -Direction Inbound -LocalPort 8001 -Protocol TCP -Action Allow
> ```

---

### Step 3: Submit a Job from Laptop A

```powershell
# In a new PowerShell window on Laptop A (venv activated)
cd distributed-compute

python scripts\submit_job.py `
    --command python `
    --args -c "print('Hello from backend job')"
```

Expected output:
```
Using worker <uuid> at 172.20.10.5:8001
Job submitted, id=<job-uuid>
Worker execution result:
{
  "output": "Hello from backend job\r\n",
  "error": "",
  "returncode": 0
}
```

---

## 5. Backend API Reference

| Endpoint | Method | Auth | Payload | Response |
|----------|--------|------|---------|----------|
| `/register` | POST | ❌ None | `{worker_name, host, port}` | `{status, worker_id}` |
| `/heartbeat` | POST | ❌ None | `{worker_id}` | `{status: "alive"}` |
| `/submit` | POST | ✅ Bearer token | `{job_name, command, args}` | `{job_id, status}` |
| `/result/{job_id}` | GET | ✅ Bearer token | – | `{job_id, status, output, error}` |
| `/workers` | GET | ✅ Bearer token | – | `{worker_id: {...}}` |

The auth token is set via `$env:BACKEND_SECRET` (default: `supersecret`).  
Pass it as: `Authorization: Bearer supersecret`

---

## 6. Worker API Reference

| Endpoint | Method | Payload | Response |
|----------|--------|---------|----------|
| `/` | GET | – | Worker status JSON |
| `/health` | GET | – | `{status: "healthy"}` |
| `/run` | POST | `{command, args}` | `{output, error, returncode}` |

---

## 7. Configuration

### Backend (`backend/config.py`)

| Env Var | Default | Description |
|---------|---------|-------------|
| `BACKEND_HOST` | `0.0.0.0` | Bind address |
| `BACKEND_PORT` | `8000` | Listen port |
| `BACKEND_SECRET` | `supersecret` | Shared auth token |
| `BACKEND_LOG_LEVEL` | `INFO` | Logging verbosity |

### Worker (`worker/config.py`)

| Env Var | Default | Description |
|---------|---------|-------------|
| `WORKER_HOST` | `0.0.0.0` | Bind address |
| `WORKER_PORT` | `8001` | Listen port |
| `WORKER_NAME` | hostname | Display name |
| `BACKEND_URL` | `http://127.0.0.1:8000` | Backend address |
| `BACKEND_SECRET` | `supersecret` | Shared auth token |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

---

## 8. Development Milestones Roadmap

- [x] **Milestone 1**: Direct Worker Communication (`Laptop A → Laptop B`)
  - Worker HTTP service with `/`, `/health`, `/run` endpoints
  - LAN IP auto-detection and startup banner
  - stdlib HTTP server fallback (no pip install needed)
  - Controller ping/latency test script

- [x] **Milestone 2**: Central Backend & Worker Registration
  - FastAPI backend with `/register`, `/heartbeat`, `/submit`, `/result`, `/workers`
  - Workers auto-register real LAN IP on startup
  - 30-second heartbeat loop (background thread)
  - `submit_job.py` controller CLI for end-to-end job dispatch
  - Token-based auth on controller-facing endpoints

- [ ] **Milestone 3**: Job Lifecycle State Machine (`QUEUED → RUNNING → COMPLETED`)
- [ ] **Milestone 4**: Backend auto-dispatches to worker (no direct controller→worker call)
- [ ] **Milestone 5**: Multiple workers + load balancing (round-robin / least-loaded)
- [ ] **Milestone 6**: Sandboxed Docker Container Execution
- [ ] **Milestone 7**: Hardware Constraints (CPU, Memory, Timeouts)
- [ ] **Milestone 8**: Logs & Results Streaming
- [ ] **Milestone 9**: Fault Tolerance & Dead Worker Detection
- [ ] **Milestone 10**: Secure Cross-Internet Mesh (Tailscale/WireGuard)
