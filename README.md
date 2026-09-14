# Personal Distributed Compute Network

> Turn your idle laptop into an on-demand, isolated compute worker.

---

## 1. Project Overview & Problem Statement

Modern workflows often involve having multiple personal machines (e.g., Laptop A and Laptop B). While working on Laptop A, Laptop B frequently sits idle with unused CPU and RAM resources.

**The Solution**: A lightweight personal distributed compute system where Laptop A (Controller) submits compute jobs over the network, and Laptop B (Worker) executes them inside an isolated sandbox and returns the stdout, stderr, and results to Laptop A.

---

## 2. Milestone 1 Architecture

Milestone 1 establishes direct HTTP communication between Laptop A and Laptop B across the local network (LAN / Wi-Fi).

```text
+------------------------------------+
|        Laptop A (Controller)       |
|                                    |
|   python scripts/test_milestone1.py|
+-----------------+------------------+
                  |
                  | HTTP GET http://<LAPTOP_B_IP>:8001/
                  v
       [ Local Network / Wi-Fi ]
                  |
                  | Windows Firewall (TCP Port 8001)
                  v
+-----------------+------------------+
|          Laptop B (Worker)         |
|                                    |
|   FastAPI / Uvicorn (0.0.0.0:8001) |
|   Returns: "Hello from worker"     |
+------------------------------------+
```

---

## 3. Monorepo Directory Structure

```text
distributed-compute/
├── worker/
│   ├── worker.py             # Worker HTTP service (FastAPI / stdlib fallback)
│   ├── config.py             # Host, Port, and Worker settings
│   ├── requirements.txt      # Worker Python dependencies
│   └── __init__.py
├── workloads/
│   └── examples/
│       ├── hello.py          # Minimal hello-world test
│       └── cpu_test.py       # CPU stress test benchmark
├── scripts/
│   └── test_milestone1.py    # Controller ping and latency test script
├── .env.example              # Sample configuration
├── .gitignore
└── README.md
```

---

## 4. Setup & Running Milestone 1

### Prerequisites
- Python 3.12+ on both Laptop A and Laptop B.
- Both laptops connected to the same Wi-Fi or LAN.

### Step 1: On Laptop B (Worker)
1. Install requirements (optional, fallback standard library mode also supported):
   ```bash
   pip install -r worker/requirements.txt
   ```
2. Start the worker node:
   ```bash
   python worker/worker.py
   ```
   *The startup banner will display the worker's detected LAN IP address.*

3. **Windows Firewall Rule (if blocked)**:
   If Laptop A cannot connect, open PowerShell as Administrator on Laptop B and run:
   ```powershell
   New-NetFirewallRule -DisplayName "Distributed Compute Worker" -Direction Inbound -LocalPort 8001 -Protocol TCP -Action Allow
   ```

### Step 2: On Laptop A (Controller)
1. Run the verification script pointing to Laptop B's IP:
   ```bash
   python scripts/test_milestone1.py --worker-url http://<LAPTOP_B_IP>:8001
   ```
2. Or use PowerShell directly:
   ```powershell
   Invoke-RestMethod -Uri "http://<LAPTOP_B_IP>:8001/"
   ```

### Expected Output
```json
{
  "message": "Hello from worker",
  "status": "online",
  "hostname": "laptop-b",
  "port": 8001,
  "timestamp": "2026-09-14T08:30:00.000000+00:00",
  "milestone": 1
}
```

---

## 5. Development Milestones Roadmap

- [x] **Milestone 1**: Direct Worker Communication (`Laptop A -> Laptop B`)
- [ ] **Milestone 2**: Central FastAPI Backend & Database
- [ ] **Milestone 3**: Dynamic Worker Registration & `psutil` Heartbeats
- [ ] **Milestone 4**: Job Lifecycle State Machine (`QUEUED` -> `RUNNING` -> `COMPLETED`)
- [ ] **Milestone 5**: Sandboxed Docker Container Execution
- [ ] **Milestone 6**: Hardware Constraints (CPU, Memory, Timeouts)
- [ ] **Milestone 7**: Logs & Results Streaming
- [ ] **Milestone 8**: Fault Tolerance & Dead Worker Detection
- [ ] **Milestone 9**: Controller CLI Tool
- [ ] **Milestone 10**: Secure Cross-Internet Mesh (Tailscale/WireGuard)
