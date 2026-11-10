# Personal Distributed Compute Network

> Turn your idle laptops into an on-demand distributed compute cluster.

---

## 1. Project Overview

Modern workflows often involve multiple personal machines sitting idle. This project turns them into a lightweight distributed compute cluster:

- A **Backend** (Laptop A) coordinates jobs and workers via a REST API.
- One or more **Workers** (other laptops) auto-register, execute jobs inside Docker containers, and send heartbeats.
- A **Controller** (Laptop A) submits jobs — including input files — and retrieves results, output files, and live logs.

**Current capability (Milestone 9):** Resource-aware scheduling, live log streaming, fault tolerance with auto-retry, and full SQLite persistence — jobs, workers, and output files survive backend restarts.

---

## 2. Architecture

```
Controller (Laptop A)
       |
       |  job + input files (base64)   [POST /jobs]
       v
  Backend :8000
       |
       |  resource check (CPU cores, free RAM)
       |  assign job to best available worker
       v
  Worker :8001
       |
       |  decode inputs → isolated workspace
       v
  Docker Container
       |
       |  reads  /workspace/input/<filename>
       |  writes /workspace/output/<filename>
       |  stdout/stderr streamed in real time → LogBuffer
       v
  Worker
       |
       |  collect outputs → report to backend
       |  SSE log stream  → GET /jobs/{id}/stream
       v
  Backend stores state to SQLite + output bytes to disk
       |
       |  GET /jobs/{id}/outputs/{filename}   (download result)
       |  GET /jobs/{id}/stream               (SSE live logs)
       v
  Controller downloads result / watches live output
```

### Fault tolerance flow

```
Worker dies mid-job
       ↓
Backend scheduler detects heartbeat timeout (60 s)
       ↓
Job re-queued → QUEUED (retry_count++)
       ↓
Scheduler assigns to next available worker
       ↓
Job completes normally   (or fails after MAX_RETRIES = 3)
```

---

## 3. API Reference

### Backend API (`:8000`)

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/register` | POST | None | Worker self-registration |
| `/heartbeat` | POST | None | Worker liveness ping |
| `/jobs` | POST | Bearer | Submit a job (+ optional input files + resource hints) |
| `/jobs` | GET | Bearer | List all jobs |
| `/jobs/{id}` | GET | Bearer | Get job status, stdout, output metadata |
| `/jobs/{id}/status` | POST | None | Worker reports status update |
| `/jobs/{id}/outputs/{filename}` | GET | Bearer | Download an output file |
| `/jobs/{id}/stream` | GET | Bearer | **SSE live log stream** (proxied from worker) |
| `/workers` | GET | Bearer | List workers with status, state, resource stats |
| `/workers/{id}/stats` | GET | Bearer | Detailed resource stats for one worker |
| `/db/stats` | GET | Bearer | **SQLite row counts** `{workers, jobs, output_files}` |
| `/stats` | GET | Bearer | Cluster-wide summary (workers, jobs, storage) |

### Worker API (`:8001`)

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Worker identity + status |
| `/health` | GET | Liveness probe |
| `/jobs/assign` | POST | Receive a job from backend |
| `/jobs/clear` | POST | Reset worker state (testing) |
| `/jobs/{id}/stream` | GET | **SSE log stream** from live Docker container |
| `/stats` | GET | CPU %, RAM %, cores, current job, uptime |

---

## 4. Directory Structure

```
distributed-compute/
├── backend/
│   ├── main.py              # FastAPI app + all endpoints + background scheduler loop
│   ├── config.py            # Backend settings (incl. BACKEND_DB_PATH, BACKEND_OUTPUT_DIR)
│   ├── database.py          # M9: SQLite CRUD layer (workers, jobs, outputs tables)
│   ├── models.py            # Pydantic models (JobResponse, OutputFileMeta, ...)
│   ├── state.py             # Thin shim → delegates to database.py (same public API)
│   └── services/
│       ├── job_manager.py   # Job state machine, retry logic, output file handling
│       └── scheduler.py     # Resource-aware worker selection + assignment
├── worker/
│   ├── worker.py            # Worker FastAPI service + SSE stream endpoint
│   ├── config.py            # Worker settings incl. Docker + file limits
│   ├── file_utils.py        # Filename validation + path-traversal prevention
│   ├── log_buffer.py        # Thread-safe ring buffer for live container logs
│   ├── resources.py         # psutil-based CPU/RAM reporting
│   └── execution/
│       ├── base.py          # Abstract ExecutionEngine + ExecutionResult
│       └── docker.py        # DockerExecutionEngine (workspace, volumes, cleanup, live logs)
├── workloads/
│   ├── python/
│   │   └── Dockerfile       # Trusted Python 3.12 workload image
│   └── examples/
│       ├── hello.py
│       ├── calculation.py
│       ├── failure.py
│       └── timeout.py
├── data/                    # M9: Persistent storage (auto-created on first run)
│   ├── backend.db           # SQLite database (workers + jobs)
│   └── outputs/             # Output files: outputs/<job_id>/<filename>
├── scripts/
│   └── submit_job.py        # Controller CLI helper
├── tests/
│   ├── verify_milestone_3.py
│   ├── verify_milestone_4.py
│   ├── verify_milestone_5.py
│   ├── verify_milestone_6.py
│   ├── verify_milestone_7.py
│   ├── verify_milestone_8.py
│   └── verify_milestone_9.py
├── docs/
│   └── architecture.md
├── .env.example
└── README.md
```

---

## 5. Quick Start

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

The `data/` directory is created automatically on first run.

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

Worker startup banner:
```
===================================================================
      PERSONAL DISTRIBUTED COMPUTE NETWORK - WORKER NODE
===================================================================
 Worker Hostname : DESKTOP-XYZ
 Listening Port  : 8001
 Local IP(s)     : 192.168.1.20
 CPU Cores       : 8
 Total RAM       : 16.0 GB
===================================================================
```

> **Windows Firewall** on Laptop B:
> ```powershell
> New-NetFirewallRule -DisplayName "DC Worker" -Direction Inbound -LocalPort 8001 -Protocol TCP -Action Allow
> ```

---

### Step 3 — Submit a code-only job

```powershell
$body = @{
    payload = @{
        type = "python"
        code = "print(2 + 3)"
    }
    resources = @{
        cpu       = 1
        memory_mb = 512
        timeout   = 30
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
    },
    "resources": {"cpu": 1, "memory_mb": 512, "timeout": 60},
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

### Step 5 — Stream live logs

```python
import httpx

BACKEND = "http://localhost:8000"
HEADERS = {"Authorization": "Bearer supersecret"}

with httpx.stream("GET", f"{BACKEND}/jobs/{job_id}/stream", headers=HEADERS, timeout=120) as r:
    for line in r.iter_lines():
        if line.startswith("data:"):
            print(line[5:].strip())   # live stdout from the container
        if line.startswith("event: done"):
            break
```

Each SSE event is one of:

| SSE event | Meaning |
|---|---|
| `event: log` / `data: <text>` | A line of stdout/stderr from the container |
| `event: done` / `data: completed` | Job finished successfully |
| `event: done` / `data: failed` | Job failed or timed out |

---

## 6. Configuration

### Backend (`backend/config.py`)

| Env Var | Default | Description |
|---|---|---|
| `BACKEND_HOST` | `0.0.0.0` | Bind address |
| `BACKEND_PORT` | `8000` | Listen port |
| `BACKEND_SECRET` | `supersecret` | Shared auth token |
| `BACKEND_LOG_LEVEL` | `INFO` | Logging verbosity |
| `BACKEND_DB_PATH` | `data/backend.db` | SQLite database file path |
| `BACKEND_OUTPUT_DIR` | `data/outputs` | Directory for output file storage on disk |

### Worker (`worker/config.py`)

| Env Var | Default | Description |
|---|---|---|
| `WORKER_HOST` | `0.0.0.0` | Bind address |
| `WORKER_PORT` | `8001` | Listen port |
| `WORKER_NAME` | hostname | Display name |
| `WORKER_REGISTER_HOST` | *(auto-detect LAN IP)* | Override registration IP (useful for loopback testing) |
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

## 7. Job Payload Format

```json
{
  "payload": {
    "type": "python",
    "code": "print('hello')",
    "inputs": {
      "data.csv":    "<base64-encoded bytes>",
      "config.json": "<base64-encoded bytes>"
    }
  },
  "resources": {
    "cpu":       2,
    "memory_mb": 4096,
    "timeout":   300
  }
}
```

| Field | Required | Description |
|---|---|---|
| `payload.type` | ✅ | Must be `"python"` |
| `payload.code` | ✅ | Python source; runs as `/workspace/script.py` |
| `payload.inputs` | ❌ | `filename → base64` dict of input files |
| `resources.cpu` | ❌ | Minimum CPU cores required (default: 1) |
| `resources.memory_mb` | ❌ | Minimum free RAM in MB required (default: 512) |
| `resources.timeout` | ❌ | Container kill timeout in seconds (default: 30) |

Inside the container the workload can:
- Read from `/workspace/input/<filename>`
- Write output files to `/workspace/output/<filename>`
- Print to stdout/stderr (captured, streamed, and returned)

---

## 8. Job Response Format

`GET /jobs/{id}` returns:

```json
{
  "job_id":      "...",
  "status":      "completed",
  "stdout":      "done\n",
  "stderr":      "",
  "exit_code":   0,
  "error":       null,
  "retry_count": 0,
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

## 9. Resource-Aware Scheduling (M6)

Before assigning a job, the scheduler queries each `AVAILABLE` worker's cached stats and checks:

1. **CPU cores** ≥ `resources.cpu`
2. **Free RAM** ≥ `resources.memory_mb`
3. **Worker state** = `AVAILABLE` (not `BUSY` or `OFFLINE`)

Workers report stats every heartbeat via `/stats`:

```json
{
  "cpu_cores":    8,
  "cpu_percent":  37.0,
  "ram_total_mb": 16384,
  "ram_used_mb":  6349,
  "ram_free_mb":  10035,
  "current_jobs": 1,
  "status":       "BUSY",
  "uptime_s":     3721
}
```

Worker state transitions:

```
OFFLINE ──(heartbeat)──▶ AVAILABLE
AVAILABLE ──(job assigned)──▶ BUSY
BUSY ──(job done)──▶ AVAILABLE
AVAILABLE/BUSY ──(heartbeat timeout 60s)──▶ OFFLINE
```

---

## 10. Live Log Streaming (M7)

Log streaming uses **Server-Sent Events (SSE)**:

```
GET /jobs/{id}/stream
Authorization: Bearer supersecret
```

Flow:
1. Docker container stdout/stderr is captured line-by-line in a background thread into a `LogBuffer` (thread-safe ring buffer).
2. Worker serves `/jobs/{id}/stream` — it iterates `LogBuffer.iter_from(0)` and emits each line as an SSE `log` event.
3. Backend's `/jobs/{id}/stream` proxies the worker's SSE stream verbatim via `httpx.stream`.
4. If the buffer has already been evicted (job completed long ago), the backend synthesises a final `event: done` from the stored job status.

---

## 11. Fault Tolerance (M8)

### Dead worker detection

The backend scheduler loop runs every **2 seconds**:
- Any worker whose last heartbeat is older than **60 seconds** is marked `OFFLINE`.
- All jobs assigned to that worker are automatically re-queued (`ASSIGNED → QUEUED`).
- `retry_count` is incremented on each re-queue.
- After **3 retries** the job is permanently marked `failed` with a descriptive error.

### Worker restart

A worker that crashed and restarts will re-register with the backend and receive a new worker ID. It will begin accepting jobs immediately.

### Scheduler recovery

If the scheduler fails to reach a worker while assigning a job (e.g. the worker crashed between heartbeat and assignment), the job is **re-queued** rather than permanently failed, and the scheduler retries on the next 2-second tick.

---

## 12. Persistent Storage (M9)

All state is persisted to a SQLite database (`data/backend.db`) and output files are written to disk (`data/outputs/`). The backend can be restarted at any time without losing data.

### What survives a backend restart

| Data | Storage |
|---|---|
| Worker registrations | SQLite `workers` table |
| Job history (all states, stdout, stderr) | SQLite `jobs` table |
| Output file metadata | SQLite `outputs` table |
| Output file bytes | `data/outputs/<job_id>/<filename>` on disk |

Workers automatically re-register on their next heartbeat if the backend was restarted (the existing heartbeat loop already handles 404 → re-register).

### SQLite schema

```sql
workers  (worker_id PK, name, host, port, status, worker_state,
          current_job_id, last_heartbeat, registered_at, extra JSON)

jobs     (job_id PK, status, worker_id, created_at, assigned_at,
          started_at, completed_at, stdout, stderr, exit_code,
          error, retry_count, payload JSON, outputs_meta JSON)

outputs  (job_id, filename PK, file_path, size)
```

> Uses WAL journal mode for concurrent readers and Python's stdlib `sqlite3` — no extra dependencies.

### Storage endpoints

```
GET /db/stats   → {"workers": 3, "jobs": 42, "output_files": 7}
GET /stats      → cluster summary with workers, jobs, and storage sections
```

---

## 13. Security Model

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

## 14. Running the Tests

> **Windows note:** All tests must be run with the backend and worker already running. Because of network isolation in the sandbox, start backend + worker + test in a **single PowerShell command** when running from scripts.

```powershell
# From the project root — start services, run tests, stop services:
cd distributed-compute

$b = Start-Process -NoNewWindow -PassThru -FilePath venv\Scripts\python.exe `
     -ArgumentList "-m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --log-level warning"
Start-Sleep 4

$w = Start-Process -NoNewWindow -PassThru -FilePath venv\Scripts\python.exe `
     -ArgumentList "worker\worker.py"
Start-Sleep 10

# Pick the milestone you want to verify:
venv\Scripts\python.exe tests\verify_milestone_3.py
venv\Scripts\python.exe tests\verify_milestone_4.py
venv\Scripts\python.exe tests\verify_milestone_5.py
venv\Scripts\python.exe tests\verify_milestone_6.py
venv\Scripts\python.exe tests\verify_milestone_7.py
venv\Scripts\python.exe tests\verify_milestone_8.py
venv\Scripts\python.exe tests\verify_milestone_9.py

$b.Kill(); $w.Kill()
```

> **M9 note:** Delete the `data/` folder before running M9 tests for a clean slate:
> ```powershell
> Remove-Item -Recurse -Force data -ErrorAction SilentlyContinue
> ```

Expected outcome for each: `VERIFICATION RESULT: SUCCESS`

---

## 15. Milestones

| # | Description | Status |
|---|---|---|
| M1 | Direct worker communication (worker HTTP server) | ✅ |
| M2 | Central backend, worker registration, heartbeat | ✅ |
| M3 | Job system: state machine, scheduling, assignment, status updates | ✅ |
| M4 | Docker execution: CPU/memory/timeout limits, stdout/stderr capture, cleanup | ✅ |
| M5 | File-based workloads: input transfer, isolated workspace, output collection, download API, path-traversal safety | ✅ |
| M6 | Resource-aware scheduling: psutil stats, BUSY/AVAILABLE states, CPU + RAM checks before assignment | ✅ |
| M7 | Live log streaming: SSE from Docker → worker LogBuffer → backend proxy → controller | ✅ |
| M8 | Fault tolerance: dead worker detection (60 s), job recovery, retry with limit (MAX=3), scheduler resilience | ✅ |
| M9 | Persistent storage: SQLite for workers/jobs, disk-backed output files, survive backend restarts | ✅ |
| M10 | Secure cross-internet mesh (Tailscale / WireGuard) | 🔲 |

---

## 16. License

MIT
