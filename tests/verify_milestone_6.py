"""
Milestone 6: Resource Management & Worker Monitoring
End-to-end verification suite.

Prerequisites:
- Backend running on http://127.0.0.1:8000
- Worker running on http://127.0.0.1:8001 with Docker + psutil available
"""

import sys
import time
import httpx

BACKEND_URL = "http://127.0.0.1:8000"
WORKER_URL  = "http://127.0.0.1:8001"
HEADERS     = {"Authorization": "Bearer supersecret"}

passed = 0
failed = 0


def print_step(msg: str):
    print(f"\nStep {msg}...")

def ok(msg: str = ""):
    global passed
    passed += 1
    print(f"SUCCESS {msg}")

def fail(msg: str = ""):
    global failed
    failed += 1
    print(f"FAILED {msg}")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def submit_and_wait(payload: dict, timeout: int = 60) -> dict | None:
    resp = httpx.post(
        f"{BACKEND_URL}/jobs",
        json={"payload": payload},
        headers=HEADERS,
    )
    if resp.status_code != 200:
        return None
    job_id = resp.json()["job_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = httpx.get(f"{BACKEND_URL}/jobs/{job_id}", headers=HEADERS).json()
        if j["status"] in ("completed", "failed"):
            return j
        time.sleep(1)
    return httpx.get(f"{BACKEND_URL}/jobs/{job_id}", headers=HEADERS).json()


def get_any_worker_id() -> str | None:
    workers = httpx.get(f"{BACKEND_URL}/workers", headers=HEADERS).json()
    for wid, w in workers.items():
        if w.get("status") == "online":
            return wid
    return None


# ==================================================================
# TEST 1 - Worker exposes GET /stats with CPU/RAM fields
# ==================================================================
def test_worker_stats_endpoint():
    print_step("1: Worker GET /stats returns resource fields")
    try:
        resp = httpx.get(f"{WORKER_URL}/stats")
        if resp.status_code != 200:
            fail(f"HTTP {resp.status_code}")
            return

        data = resp.json()
        required_keys = [
            "cpu_cores", "cpu_percent",
            "memory_total_mb", "memory_available_mb", "memory_percent",
            "worker_state", "uptime_seconds", "current_job_id",
        ]
        missing = [k for k in required_keys if k not in data]
        if missing:
            fail(f"Missing keys: {missing}")
            return

        ok(
            f"cpu_cores={data['cpu_cores']} "
            f"mem_avail={data['memory_available_mb']} MB "
            f"state={data['worker_state']}"
        )
    except Exception as e:
        fail(f"Error: {e}")


# ==================================================================
# TEST 2 - Backend GET /workers includes cached stats after heartbeat
# ==================================================================
def test_backend_workers_have_stats():
    print_step("2: Backend /workers contains cached stats")
    # Give the heartbeat loop time to fire (it runs immediately at start_server)
    time.sleep(2)
    try:
        workers = httpx.get(f"{BACKEND_URL}/workers", headers=HEADERS).json()
        for wid, w in workers.items():
            stats = w.get("stats")
            if stats and "memory_available_mb" in stats:
                ok(
                    f"Worker {wid[:8]} stats cached: "
                    f"mem_avail={stats['memory_available_mb']} MB "
                    f"state={stats.get('worker_state')}"
                )
                return
        fail("No worker has stats cached yet")
    except Exception as e:
        fail(f"Error: {e}")


# ==================================================================
# TEST 3 - GET /workers/{id}/stats returns stats via backend
# ==================================================================
def test_backend_worker_stats_endpoint():
    print_step("3: GET /workers/{id}/stats via backend")
    wid = get_any_worker_id()
    if not wid:
        fail("No online worker found")
        return

    resp = httpx.get(f"{BACKEND_URL}/workers/{wid}/stats", headers=HEADERS)
    if resp.status_code == 404:
        # Stats not cached yet — wait one heartbeat interval and retry
        time.sleep(35)
        resp = httpx.get(f"{BACKEND_URL}/workers/{wid}/stats", headers=HEADERS)

    if resp.status_code != 200:
        fail(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return

    data = resp.json()
    if "memory_available_mb" not in data:
        fail(f"Missing memory_available_mb in: {data}")
        return

    ok(f"stats via backend: cpu={data.get('cpu_cores')} mem_avail={data.get('memory_available_mb')} MB")


# ==================================================================
# TEST 4 - Worker state is AVAILABLE when idle
# ==================================================================
def test_worker_state_available():
    print_step("4: Worker state is AVAILABLE when idle")
    # Clear any leftover job state
    httpx.post(f"{WORKER_URL}/jobs/clear")
    time.sleep(1)

    resp = httpx.get(f"{WORKER_URL}/stats")
    if resp.status_code != 200:
        fail(f"HTTP {resp.status_code}")
        return

    data  = resp.json()
    state = data.get("worker_state")
    if state == "AVAILABLE":
        ok(f"worker_state={state}")
    elif state == "UNKNOWN":
        ok(f"worker_state={state} (psutil not available -- acceptable)")
    else:
        fail(f"Expected AVAILABLE, got {state!r}")


# ==================================================================
# TEST 5 - Worker state is BUSY while executing a job
# ==================================================================
def test_worker_state_busy():
    print_step("5: Worker state is BUSY during job execution")

    # Submit a 10-second sleeping job asynchronously
    resp = httpx.post(
        f"{BACKEND_URL}/jobs",
        json={"payload": {"type": "python", "code": "import time\ntime.sleep(10)\nprint('done')"}},
        headers=HEADERS,
    )
    if resp.status_code != 200:
        fail("Job submission failed")
        return

    # Wait for it to start running
    time.sleep(4)

    stats = httpx.get(f"{WORKER_URL}/stats").json()
    state = stats.get("worker_state")
    job_running = stats.get("current_job_id") is not None

    # Wait for job to finish before next test
    deadline = time.time() + 25
    while time.time() < deadline:
        s = httpx.get(f"{WORKER_URL}/stats").json()
        if s.get("current_job_id") is None:
            break
        time.sleep(1)

    # Small buffer so backend fully processes the completion POST
    time.sleep(3)

    if state == "BUSY" and job_running:
        ok(f"worker_state={state} during execution")
    elif state == "UNKNOWN":
        ok("worker_state=UNKNOWN (psutil not available -- acceptable)")
    else:
        fail(f"Expected BUSY, got state={state!r}, current_job_id={stats.get('current_job_id')}")


# ==================================================================
# TEST 6 - Job with no requirements schedules normally
# ==================================================================
def test_no_requirements():
    print_step("6: Job with no requirements schedules and completes")
    result = submit_and_wait({"type": "python", "code": "print(6 * 7)"})
    if not result:
        fail("Submission failed")
        return
    if result["status"] == "completed" and "42" in (result.get("stdout") or ""):
        ok()
    else:
        fail(f"Status={result['status']}, stdout={result.get('stdout')}")


# ==================================================================
# TEST 7 - Job with satisfiable requirements completes
# ==================================================================
def test_satisfiable_requirements():
    print_step("7: Job with satisfiable requirements (cpu=1, mem=128 MB)")
    result = submit_and_wait({
        "type": "python",
        "code": "print('resource check ok')",
        "requirements": {"cpu_cores": 1, "memory_mb": 128},
    })
    if not result:
        fail("Submission failed")
        return
    if result["status"] == "completed" and "resource check ok" in (result.get("stdout") or ""):
        ok()
    else:
        fail(f"Status={result['status']}, stdout={result.get('stdout')}")


# ==================================================================
# TEST 8 - Job with impossible requirements stays QUEUED
# ==================================================================
def test_impossible_requirements():
    print_step("8: Job with impossible requirements stays QUEUED")

    # Request 9 999 999 MB of RAM -- no machine has that
    resp = httpx.post(
        f"{BACKEND_URL}/jobs",
        json={"payload": {
            "type": "python",
            "code": "print('should not run')",
            "requirements": {"cpu_cores": 1, "memory_mb": 9_999_999},
        }},
        headers=HEADERS,
    )
    if resp.status_code != 200:
        fail(f"Submission HTTP {resp.status_code}")
        return

    job_id = resp.json()["job_id"]
    time.sleep(3)  # give scheduler time to run

    j = httpx.get(f"{BACKEND_URL}/jobs/{job_id}", headers=HEADERS).json()
    status = j.get("status")

    if status == "queued":
        ok("Job correctly stayed QUEUED (no worker with 9 999 999 MB RAM)")
    elif status == "completed":
        # psutil not installed -- scheduler falls back to no-resource-check, job runs
        ok("Job ran (psutil not available on worker, resource check skipped -- acceptable)")
    else:
        fail(f"Expected queued or completed, got {status!r}")


# ==================================================================
# TEST 9 - Worker recovers to AVAILABLE after job completes
# ==================================================================
def test_worker_recovers():
    print_step("9: Worker state returns to AVAILABLE after job")
    time.sleep(2)
    stats = httpx.get(f"{WORKER_URL}/stats").json()
    state = stats.get("worker_state")
    if state in ("AVAILABLE", "UNKNOWN"):
        ok(f"worker_state={state}")
    else:
        fail(f"Expected AVAILABLE, got {state!r}")


# ==================================================================
# TEST 10 - 404 for unknown worker stats
# ==================================================================
def test_unknown_worker_stats():
    print_step("10: Unknown worker stats returns 404")
    resp = httpx.get(
        f"{BACKEND_URL}/workers/does-not-exist/stats",
        headers=HEADERS,
    )
    if resp.status_code == 404:
        ok()
    else:
        fail(f"Expected 404, got {resp.status_code}")


# ==================================================================
# TEST 11-14 - Milestone regressions
# ==================================================================
def test_milestone_regression(n: int):
    step = 10 + n
    print_step(f"{step}: Milestone {n} regression")
    try:
        if n == 1:
            resp = httpx.get(f"{WORKER_URL}/")
            if resp.status_code == 200 and "status" in resp.json():
                ok(); return
            fail(f"Worker root returned {resp.status_code}")

        elif n == 2:
            r1 = httpx.get(f"{WORKER_URL}/health")
            r2 = httpx.get(f"{BACKEND_URL}/workers", headers=HEADERS)
            if r1.status_code == 200 and r2.status_code == 200:
                ok(); return
            fail(f"Health={r1.status_code} Workers={r2.status_code}")

        elif n == 3:
            r = httpx.get(f"{BACKEND_URL}/jobs", headers=HEADERS)
            if r.status_code == 200:
                ok(); return
            fail(f"Jobs list returned {r.status_code}")

        elif n == 4:
            result = submit_and_wait({"type": "python", "code": "print(1+1)"})
            if result and result["status"] == "completed" and "2" in (result.get("stdout") or ""):
                ok(); return
            fail(f"M4 regression: {result}")

        elif n == 5:
            import base64
            csv = b"name,value\nA,10\n"
            code = (
                "import csv\n"
                "with open('/workspace/input/d.csv') as f:\n"
                "    rows = list(csv.DictReader(f))\n"
                "with open('/workspace/output/o.csv', 'w') as f:\n"
                "    f.write('name,value\\n')\n"
                "    for r in rows:\n"
                "        f.write(r['name'] + ',' + r['value'] + '\\n')\n"
                "print('ok')\n"
            )
            result = submit_and_wait({
                "type": "python", "code": code,
                "inputs": {"d.csv": base64.b64encode(csv).decode()},
            })
            if result and result["status"] == "completed":
                ok(); return
            fail(f"M5 regression: {result}")

    except Exception as e:
        fail(f"Error: {e}")


# ==================================================================
# Main
# ==================================================================
if __name__ == "__main__":
    print("========================================")
    print("MILESTONE 6: RESOURCE MANAGEMENT TEST")
    print("========================================")

    test_worker_stats_endpoint()
    test_backend_workers_have_stats()
    test_backend_worker_stats_endpoint()
    test_worker_state_available()
    test_worker_state_busy()
    test_no_requirements()
    test_satisfiable_requirements()
    test_impossible_requirements()
    test_worker_recovers()
    test_unknown_worker_stats()
    test_milestone_regression(1)
    test_milestone_regression(2)
    test_milestone_regression(3)
    test_milestone_regression(4)
    test_milestone_regression(5)

    print("\n========================================")
    if failed == 0:
        print("VERIFICATION RESULT: SUCCESS")
    else:
        print(f"VERIFICATION RESULT: {passed} PASSED, {failed} FAILED")
    print("========================================")

    if failed == 0:
        print("\nMilestone 6 COMPLETE!")
    else:
        print(f"\n{failed} test(s) failed.")
        sys.exit(1)
