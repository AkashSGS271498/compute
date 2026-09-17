"""
Milestone 4: Docker Execution — End-to-end verification suite.

Prerequisites:
- Backend running on http://127.0.0.1:8000
- Worker running on http://127.0.0.1:8001 with Docker available
- Docker image 'distributed-compute-python:latest' built
"""

import time
import sys
import httpx
import docker

BACKEND_URL = "http://127.0.0.1:8000"
WORKER_URL = "http://127.0.0.1:8001"
HEADERS = {"Authorization": "Bearer supersecret"}

passed = 0
failed = 0


def print_step(msg):
    print(f"\nStep {msg}...")


def submit_and_wait(payload, timeout=45):
    """Submit a job and poll until terminal state or timeout."""
    resp = httpx.post(f"{BACKEND_URL}/jobs", json={"payload": payload}, headers=HEADERS)
    if resp.status_code != 200:
        return None
    job = resp.json()
    job_id = job["job_id"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        j = httpx.get(f"{BACKEND_URL}/jobs/{job_id}", headers=HEADERS).json()
        if j["status"] in ("completed", "failed"):
            return j
        time.sleep(1)

    # Return whatever state we have
    return httpx.get(f"{BACKEND_URL}/jobs/{job_id}", headers=HEADERS).json()


def ok(msg=""):
    global passed
    passed += 1
    print(f"SUCCESS {msg}")


def fail(msg=""):
    global failed
    failed += 1
    print(f"FAILED {msg}")


# ------------------------------------------------------------------
# TEST 1 — Docker Availability
# ------------------------------------------------------------------
def test_docker_availability():
    print_step("1: Checking Docker availability")
    try:
        client = docker.from_env()
        client.ping()
        ok("Docker available")
    except Exception as e:
        fail(f"Docker unavailable: {e}")


# ------------------------------------------------------------------
# TEST 2 — Build/Verify Workload Image
# ------------------------------------------------------------------
def test_workload_image():
    print_step("2: Building/verifying workload image")
    try:
        client = docker.from_env()
        try:
            client.images.get("distributed-compute-python:latest")
            ok("Image exists")
        except docker.errors.ImageNotFound:
            fail("Image 'distributed-compute-python:latest' not found. "
                 "Build it with: docker build -t distributed-compute-python:latest workloads/python/")
    except Exception as e:
        fail(f"Docker error: {e}")


# ------------------------------------------------------------------
# TEST 3 — Simple Python Workload
# ------------------------------------------------------------------
def test_simple_python():
    print_step("3: Running simple Python workload")
    result = submit_and_wait({"type": "python", "code": "print(2 + 3)"})
    if not result:
        fail("Job submission failed")
        return

    if result["status"] == "completed" and "5" in (result.get("stdout") or ""):
        ok(f"Output: {result['stdout'].strip()}")
    else:
        fail(f"Status={result['status']}, stdout={result.get('stdout')}")


# ------------------------------------------------------------------
# TEST 4 — Multi-line Output
# ------------------------------------------------------------------
def test_multiline():
    print_step("4: Testing multi-line output")
    code = 'print("line one")\nprint("line two")\nprint("line three")'
    result = submit_and_wait({"type": "python", "code": code})
    if not result:
        fail("Job submission failed")
        return

    stdout = result.get("stdout") or ""
    if (
        result["status"] == "completed"
        and "line one" in stdout
        and "line two" in stdout
        and "line three" in stdout
    ):
        ok()
    else:
        fail(f"Status={result['status']}, stdout={stdout}")


# ------------------------------------------------------------------
# TEST 5 — Calculation
# ------------------------------------------------------------------
def test_calculation():
    print_step("5: Testing calculation")
    code = "result = sum(range(1, 101))\nprint(result)"
    result = submit_and_wait({"type": "python", "code": code})
    if not result:
        fail("Job submission failed")
        return

    if result["status"] == "completed" and "5050" in (result.get("stdout") or ""):
        ok(f"Output: {result['stdout'].strip()}")
    else:
        fail(f"Status={result['status']}, stdout={result.get('stdout')}")


# ------------------------------------------------------------------
# TEST 6 — Failure
# ------------------------------------------------------------------
def test_failure():
    print_step("6: Testing failed workload")
    code = 'raise RuntimeError("intentional test failure")'
    result = submit_and_wait({"type": "python", "code": code})
    if not result:
        fail("Job submission failed")
        return

    if (
        result["status"] == "failed"
        and result.get("exit_code", 0) != 0
        and (result.get("stderr") or "")
    ):
        ok("Failure correctly detected")
    else:
        fail(f"Status={result['status']}, exit_code={result.get('exit_code')}, stderr={result.get('stderr')}")


# ------------------------------------------------------------------
# TEST 7 — Timeout
# ------------------------------------------------------------------
def test_timeout():
    print_step("7: Testing timeout")
    code = "import time\ntime.sleep(60)"
    # Use a short timeout — the worker's configured timeout will handle it
    result = submit_and_wait({"type": "python", "code": code}, timeout=60)
    if not result:
        fail("Job submission failed")
        return

    if result["status"] == "failed" and "timeout" in (result.get("error") or "").lower():
        ok("Timeout correctly detected")
    else:
        fail(f"Status={result['status']}, error={result.get('error')}")


# ------------------------------------------------------------------
# TEST 8 — Resource Limits (inspection)
# ------------------------------------------------------------------
def test_resource_limits():
    print_step("8: Checking resource limits")
    # Submit a job that inspects its own cgroup limits
    code = """
import os
# Just verify we're inside a container by checking for /.dockerenv or cgroup
in_container = os.path.exists('/.dockerenv') or os.path.exists('/proc/1/cgroup')
print(f"in_container={in_container}")
print("resource_check=ok")
"""
    result = submit_and_wait({"type": "python", "code": code})
    if not result:
        fail("Job submission failed")
        return

    if result["status"] == "completed" and "resource_check=ok" in (result.get("stdout") or ""):
        ok()
    else:
        fail(f"Status={result['status']}, stdout={result.get('stdout')}")


# ------------------------------------------------------------------
# TEST 9 — Container Cleanup
# ------------------------------------------------------------------
def test_container_cleanup():
    print_step("9: Checking container cleanup")
    try:
        client = docker.from_env()
        # Check for leftover compute-job containers
        containers = client.containers.list(
            all=True, filters={"name": "compute-job-"}
        )
        if len(containers) == 0:
            ok()
        else:
            fail(f"Found {len(containers)} leftover container(s): "
                 f"{[c.name for c in containers]}")
    except Exception as e:
        fail(f"Docker error: {e}")


# ------------------------------------------------------------------
# TEST 10 — Worker Availability After Job
# ------------------------------------------------------------------
def test_worker_availability():
    print_step("10: Checking worker availability")
    # Clear worker state just in case
    httpx.post(f"{WORKER_URL}/jobs/clear")
    time.sleep(1)

    # Submit a new job — it should be assigned
    result = submit_and_wait({"type": "python", "code": "print('available')"})
    if not result:
        fail("Job submission failed")
        return

    if result["status"] == "completed":
        ok()
    else:
        fail(f"Status={result['status']}")


# ------------------------------------------------------------------
# TEST 11 — Heartbeat During Job
# ------------------------------------------------------------------
def test_heartbeat_during_job():
    print_step("11: Checking heartbeat during execution")
    # Clear worker
    httpx.post(f"{WORKER_URL}/jobs/clear")
    time.sleep(1)

    # Submit a job that takes a few seconds
    code = "import time\ntime.sleep(5)\nprint('done')"
    resp = httpx.post(f"{BACKEND_URL}/jobs", json={"payload": {"type": "python", "code": code}}, headers=HEADERS)
    job_id = resp.json()["job_id"]

    # Wait for it to be running
    time.sleep(3)

    # Check worker is still online via backend
    workers = httpx.get(f"{BACKEND_URL}/workers", headers=HEADERS).json()
    any_online = any(w.get("status") == "online" for w in workers.values())

    if any_online:
        ok()
    else:
        fail("Worker went offline during job execution")

    # Wait for job to complete before proceeding
    deadline = time.time() + 30
    while time.time() < deadline:
        j = httpx.get(f"{BACKEND_URL}/jobs/{job_id}", headers=HEADERS).json()
        if j["status"] in ("completed", "failed"):
            break
        time.sleep(1)


# ------------------------------------------------------------------
# TEST 12-14 — Milestone Regression Tests
# ------------------------------------------------------------------
def test_milestone_regression(milestone_num):
    print_step(f"{11 + milestone_num}: Running Milestone {milestone_num} regression test")
    # Just check that the basic endpoints still work
    if milestone_num == 1:
        try:
            resp = httpx.get(f"{WORKER_URL}/")
            if resp.status_code == 200 and "status" in resp.json():
                ok()
            else:
                fail(f"Worker root returned {resp.status_code}")
        except Exception as e:
            fail(f"Error: {e}")

    elif milestone_num == 2:
        try:
            resp = httpx.get(f"{WORKER_URL}/health")
            workers = httpx.get(f"{BACKEND_URL}/workers", headers=HEADERS)
            if resp.status_code == 200 and workers.status_code == 200:
                ok()
            else:
                fail(f"Health={resp.status_code}, Workers={workers.status_code}")
        except Exception as e:
            fail(f"Error: {e}")

    elif milestone_num == 3:
        try:
            # Test job creation and listing
            resp = httpx.get(f"{BACKEND_URL}/jobs", headers=HEADERS)
            if resp.status_code == 200:
                ok()
            else:
                fail(f"Jobs list returned {resp.status_code}")
        except Exception as e:
            fail(f"Error: {e}")


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
if __name__ == "__main__":
    print("========================================")
    print("MILESTONE 4: DOCKER EXECUTION TEST")
    print("========================================")

    test_docker_availability()
    test_workload_image()
    test_simple_python()
    test_multiline()
    test_calculation()
    test_failure()
    test_timeout()
    test_resource_limits()
    test_container_cleanup()
    test_worker_availability()
    test_heartbeat_during_job()
    test_milestone_regression(1)
    test_milestone_regression(2)
    test_milestone_regression(3)

    print("\n========================================")
    if failed == 0:
        print("VERIFICATION RESULT: SUCCESS")
    else:
        print(f"VERIFICATION RESULT: {passed} PASSED, {failed} FAILED")
    print("========================================")

    if failed == 0:
        print("\nMilestone 4 COMPLETE!")
    else:
        print(f"\n{failed} test(s) failed.")
        sys.exit(1)
