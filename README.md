# Personal Distributed Compute Network

> Turn your idle laptops into an on-demand distributed compute cluster.

---

## 1. Project Overview

Modern workflows often involve multiple personal machines sitting idle. This project turns them into a lightweight distributed compute cluster:

- A **Backend** (Laptop A) coordinates jobs and workers via a REST API.
- One or more **Workers** (other laptops) auto-register, execute jobs inside Docker containers, and send heartbeats.
- A **Controller** (Laptop A) submits jobs — including input files — and retrieves results and output files.

**Current capability (Milestone 5):** Submit Python code + input files, execute in an isolated Docker container, retrieve output files.

---

## 2. Architecture

```
Controller (Laptop A)
       |
       |  job + input files (base64)
       v
  Backend :8000
       |
       |  assign job
       v
  Worker :8001
       |
       |  decode inputs -> isolated workspace
       v
  Docker Container
       |
       |  reads /workspace/input/
       |  writes /workspace/output/
       v
  Worker
       |
       |  collect outputs -> report to backend
       v
  Backend stores output bytes
       |
       |  GET /jobs/{id}/outputs/{filename}
       v
  Controller downloads result
```

### Backend API (`:8000`)

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/register` | POST | None | Worker self-registration |
| `/heartbeat` | POST | None | Worker liveness ping |
| `/jobs` | POST | Bearer | Submit a job (+ optional input files) |
| `/jobs` | GET | Bearer | List all jobs |
| `/jobs/{id}` | GET | Bearer | Get job status + output metadata |
| `/jobs/{id}/status` | POST | None | Worker reports status update |
| `/jobs/{id}/outputs/{filename}` | GET | Bearer | Download an output file |
| `/workers` | GET | Bearer | List registered workers |

### Worker API (`:8001`)

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Worker status |
| `/health` | GET | Liveness probe |
| `/jobs/assign` | POST | Receive a job from backend |
| `/jobs/clear` | POST | Reset worker state (testing) |

---

## 3. Directory Structure

```
distributed-compute/
├── backend/
│   ├── main.py              # FastAPI app + all endpoints
│   ├── config.py            # Backend settings
│   ├── models.py            # Pydantic models (JobResponse, OutputFileMeta, ...)
│   ├── state.py             # In-memory worker/job/output-file store
│   └── services/
│       ├── job_manager.py   # Job state machine + output file handling
│       └── scheduler.py     # Worker selection + assignment
├── worker/
│   ├── worker.py            # Worker FastAPI service
│   ├── config.py            # Worker settings incl. Docker + file limits
│   ├── file_utils.py        # Filename validation + path-traversal prevention
│   └── execution/
│       ├── base.py          # Abstract ExecutionEngine + ExecutionResult
│       └── docker.py        # DockerExecutionEngine (workspace, volumes, cleanup)
├── workloads/
│   ├── python/
│   │   └── Dockerfile       # Trusted Python 3.12 workload image
│   └── examples/
│       ├── hello.py
│       ├── calculation.py
│       ├── failure.py
│       └── timeout.py
├── scripts/
│   └── submit_job.py        # Controller CLI
├── tests/
│   ├── verify_milestone_3.py
│   ├── verify_milestone_4.py
│   └── verify_milestone_5.py
├── docs/
│   └── architecture.md
├── .env.example
└── README.md
```

---

## 4. Quick Start

### Prerequisites

- Python 3.12+ on both machines
- Docker Desktop installed and running on the **worker** machine
- Both machines on the same Wi-Fi / LAN
- A shared secret token (default: `supersecret`)

---

### Step 0 — Build the Docker workload image (worker machine)

```powershell
cd distributed-compute
docker build -t distributed-compute-python:latest workloads/python/
```

---

### Step 1 — Start the backend (Laptop A)

```powershell
cd distributed-compute
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt

uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

> **Windows Firewall** — allow inbound port 8000 if workers are on other machines:
> ```powershell
> New-NetFirewallRule -DisplayName "DC Backend" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
> ```

---

### Step 2 — Start the worker (Laptop B)

```powershell
cd distributed-compute
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r worker\requirements.txt

$env:BACKEND_URL    = "http://<LAPTOP_A_IP>:8000"   # e.g. http://192.168.1.10:8000
$env:BACKEND_SECRET = "supersecret"

python worker\worker.py
```

Worker output:
```
===================================================================
      PERSONAL DISTRIBUTED COMPUTE NETWORK - WORKER NODE
===================================================================
 Worker Hostname : DESKTOP-XYZ
 Listening Port  : 8001
 Local IP(s)     : 192.168.1.20
===================================================================
```

> **Windows Firewall** on Laptop B:
> ```powershell
> New-NetFirewallRule -DisplayName "DC Worker" -Direction Inbound -LocalPort 8001 -Protocol TCP -Action Allow
> ```

---

### Step 3 — Submit a code-only job

```powershell
# Simple Python snippet
$body = @{
    payload = @{
        type = "python"
        code = "print(2 + 3)"
    }
} | ConvertTo-Json -Depth 5

$headers = @{ Authorization = "Bearer supersecret" }

$job = Invoke-RestMethod -Method Post `
    -Uri "http://localhost:8000/jobs" `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body

$job.job_id   # copy this
```

Poll for the result:
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/jobs/$($job.job_id)" -Headers $headers
```

---

### Step 4 — Submit a job with input files

```python
# scripts/submit_file_job.py  (example)
import base64, httpx, time

BACKEND  = "http://localhost:8000"
HEADERS  = {"Authorization": "Bearer supersecret"}

csv_bytes = b"name,value\nA,10\nB,20\nC,30\n"

code = """
import csv
with open("/workspace/input/input.csv") as f:
    reader = csv.DictReader(f)
    rows = [dict(r, double=str(int(r["value"])*2)) for r in reader]

with open("/workspace/output/result.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["name","value","double"])
    w.writeheader()
    w.writerows(rows)

print("done")
"""

resp = httpx.post(f"{BACKEND}/jobs", headers=HEADERS, json={
    "payload": {
        "type": "python",
        "code": code,
        "inputs": {"input.csv": base64.b64encode(csv_bytes).decode()},
    }
})
job_id = resp.json()["job_id"]

# Poll
while True:
    j = httpx.get(f"{BACKEND}/jobs/{job_id}", headers=HEADERS).json()
    if j["status"] in ("completed", "failed"):
        break
    time.sleep(1)

# Download result
out = httpx.get(f"{BACKEND}/jobs/{job_id}/outputs/result.csv", headers=HEADERS)
print(out.text)
```

---

## 5. Configuration

### Backend (`backend/config.py`)

| Env Var | Default | Description |
|---|---|---|
| `BACKEND_HOST` | `0.0.0.0` | Bind address |
| `BACKEND_PORT` | `8000` | Listen port |
| `BACKEND_SECRET` | `supersecret` | Shared auth token |
| `BACKEND_LOG_LEVEL` | `INFO` | Logging verbosity |

### Worker (`worker/config.py`)

| Env Var | Default | Description |
|---|---|---|
| `WORKER_HOST` | `0.0.0.0` | Bind address |
| `WORKER_PORT` | `8001` | Listen port |
| `WORKER_NAME` | hostname | Display name |
| `BACKEND_URL` | `http://127.0.0.1:8000` | Backend address |
| `BACKEND_SECRET` | `supersecret` | Shared auth token |
| `DOCKER_IMAGE` | `distributed-compute-python:latest` | Workload image |
| `JOB_CPU_LIMIT` | `1.0` | vCPUs per container |
| `JOB_MEMORY_LIMIT` | `512m` | RAM per container |
| `JOB_TIMEOUT_SECONDS` | `30` | Kill container after N seconds |
| `JOB_NETWORK_DISABLED` | `true` | Disable network inside container |
| `MAX_INPUT_FILE_BYTES` | `10485760` | Max single input file (10 MB) |
| `MAX_TOTAL_INPUT_BYTES` | `52428800` | Max total input per job (50 MB) |
| `MAX_OUTPUT_FILE_BYTES` | `52428800` | Max single output file (50 MB) |
| `MAX_INPUT_FILES` | `20` | Max number of input files |
| `MAX_OUTPUT_FILES` | `50` | Max number of output files |

---

## 6. Job Payload Format

```json
{
  "payload": {
    "type": "python",
    "code": "print('hello')",
    "inputs": {
      "data.csv":   "<base64-encoded bytes>",
      "config.json": "<base64-encoded bytes>"
    }
  }
}
```

- `type` — must be `"python"` (only type supported)
- `code` — Python source code; runs as `/workspace/script.py`
- `inputs` — optional dict of `filename -> base64 string`

Inside the container, the workload can:
- Read from `/workspace/input/<filename>`
- Write output files to `/workspace/output/<filename>`
- Print to stdout/stderr (captured and returned)

---

## 7. Job Response Format

`GET /jobs/{id}` returns:

```json
{
  "job_id": "...",
  "status": "completed",
  "stdout": "done\n",
  "stderr": "",
  "exit_code": 0,
  "error": null,
  "outputs": [
    {"filename": "result.csv", "size": 46}
  ]
}
```

`outputs` contains **metadata only** (no file bytes). Download the actual file via:

```
GET /jobs/{id}/outputs/{filename}
Authorization: Bearer supersecret
```

---

## 8. Security Model

| Control | Detail |
|---|---|
| Isolated containers | Workloads never touch the host filesystem |
| Trusted image only | Job payload cannot override the image name |
| Non-root user | Container runs as `runner` user |
| All capabilities dropped | `cap_drop: ["ALL"]` |
| No new privileges | `security_opt: ["no-new-privileges"]` |
| Network disabled | No outbound internet from container |
| Read-only code mount | Script file mounted read-only |
| Read-only input mount | `/workspace/input` mounted read-only |
| Path traversal prevention | `validate_filename()` + `safe_join()` reject `../../`, null bytes, separators |
| File size limits | Per-file and total limits enforced at backend + worker |
| Automatic cleanup | Container + entire job workspace removed after every job (success, failure, timeout) |

> **Limitation:** Docker provides strong isolation but is not a perfect sandbox. A kernel exploit could theoretically escape. For untrusted production workloads, consider gVisor or Firecracker.

---

## 9. Running the Tests

All verification scripts require the backend and worker to be running.

```powershell
# From the project root with venv activated:

# Milestone 3 — Job system
venv\Scripts\python.exe tests\verify_milestone_3.py

# Milestone 4 — Docker execution
venv\Scripts\python.exe tests\verify_milestone_4.py

# Milestone 5 — File-based workloads  (~3 min, includes timeout test)
venv\Scripts\python.exe tests\verify_milestone_5.py
```

Expected outcome for each: `VERIFICATION RESULT: SUCCESS`

---

## 10. Milestones

- [x] **Milestone 1** — Direct worker communication (worker HTTP server)
- [x] **Milestone 2** — Central backend, worker registration, heartbeat
- [x] **Milestone 3** — Job system: state machine, scheduling, assignment, status updates
- [x] **Milestone 4** — Docker execution: CPU/memory/timeout limits, stdout/stderr capture, cleanup
- [x] **Milestone 5** — File-based workloads: input file transfer, isolated workspace, output collection, download API, path-traversal safety
- [ ] **Milestone 6** — Multiple workers + load balancing
- [ ] **Milestone 7** — Log streaming & real-time output
- [ ] **Milestone 8** — Fault tolerance & dead worker detection
- [ ] **Milestone 9** — Persistent storage (PostgreSQL / SQLite)
- [ ] **Milestone 10** — Secure cross-internet mesh (Tailscale / WireGuard)

---

## 11. License

MIT
