"""
Milestone 5: File-Based Workloads — End-to-end verification suite.

Prerequisites:
- Backend running on http://127.0.0.1:8000
- Worker running on http://127.0.0.1:8001 with Docker available
- Docker image '"'"'distributed-compute-python:latest'"'"' built
"""

import base64
import sys
import time
import docker
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

def encode_file(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


def submit_and_wait(payload: dict, timeout: int = 60) -> dict | None:
    """Submit a job and poll until terminal state or timeout."""
    resp = httpx.post(f"{BACKEND_URL}/jobs", json={"payload": payload}, headers=HEADERS)
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


def _is_timeout_error(s: str) -> bool:
    s = (s or "").lower()
    return "timeout" in s or "timed out" in s


def _no_leftover_containers() -> bool:
    try:
        client = docker.from_env()
        return len(client.containers.list(all=True, filters={"name": "compute-job-"})) == 0
    except Exception:
        return True   # Docker unavailable — assume clean


# ==================================================================
# TEST 1 — Single input -> single output (CSV doubler)
# ==================================================================
def test_single_file_roundtrip():
    print_step("1: Single input file -> output file (CSV doubler)")

    csv_content = b"name,value\nA,10\nB,20\nC,30\n"

    code = """
import csv, os

input_path  = "/workspace/input/input.csv"
output_path = "/workspace/output/result.csv"

rows = []
with open(input_path, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        row["double"] = str(int(row["value"]) * 2)
        rows.append(row)

with open(output_path, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["name", "value", "double"])
    writer.writeheader()
    writer.writerows(rows)

print("csv_processed=ok")
"""

    payload = {
        "type": "python",
        "code": code,
        "inputs": {"input.csv": encode_file(csv_content)},
    }

    result = submit_and_wait(payload)
    if not result:
        fail("Job submission failed")
        return None

    if result["status"] != "completed":
        fail(f"Status={result['status']}, error={result.get('error')}")
        return None

    outputs = result.get("outputs") or []
    if not any(o["filename"] == "result.csv" for o in outputs):
        fail(f"result.csv not in outputs metadata: {outputs}")
        return None

    ok(f"Job completed, outputs={[o['filename'] for o in outputs]}")
    return result["job_id"]


# ==================================================================
# TEST 2 — File download via GET /jobs/{id}/outputs/{filename}
# ==================================================================
def test_file_download(job_id: str):
    print_step("2: Downloading output file via API")

    if not job_id:
        fail("No job_id from previous step")
        return

    resp = httpx.get(
        f"{BACKEND_URL}/jobs/{job_id}/outputs/result.csv",
        headers=HEADERS,
    )

    if resp.status_code != 200:
        fail(f"HTTP {resp.status_code}: {resp.text}")
        return

    ok(f"Downloaded {len(resp.content)} bytes")


# ==================================================================
# TEST 3 — Output file contents are correct
# ==================================================================
def test_output_contents(job_id: str):
    print_step("3: Verifying output file contents")

    if not job_id:
        fail("No job_id from previous step")
        return

    resp = httpx.get(
        f"{BACKEND_URL}/jobs/{job_id}/outputs/result.csv",
        headers=HEADERS,
    )

    if resp.status_code != 200:
        fail(f"HTTP {resp.status_code}")
        return

    text = resp.text
    expected_rows = [
        ("A", "10", "20"),
        ("B", "20", "40"),
        ("C", "30", "60"),
    ]

    all_ok = True
    for name, val, dbl in expected_rows:
        if f"{name},{val},{dbl}" not in text:
            fail(f"Row {name},{val},{dbl} not found in output:\n{text}")
            all_ok = False
            break

    if all_ok:
        ok("CSV contents correct")


# ==================================================================
# TEST 4 — Multiple input files -> multiple output files
# ==================================================================
def test_multiple_files():
    print_step("4: Multiple input files -> multiple output files")

    fa = b"hello from file_a"
    fb = b"hello from file_b"

    code = """
import os

with open("/workspace/input/a.txt") as f:
    text_a = f.read()
with open("/workspace/input/b.txt") as f:
    text_b = f.read()

with open("/workspace/output/out_a.txt", "w") as f:
    f.write(text_a.upper())
with open("/workspace/output/out_b.txt", "w") as f:
    f.write(text_b.upper())

print("multi_file=ok")
"""

    payload = {
        "type": "python",
        "code": code,
        "inputs": {
            "a.txt": encode_file(fa),
            "b.txt": encode_file(fb),
        },
    }

    result = submit_and_wait(payload)
    if not result:
        fail("Job submission failed")
        return

    if result["status"] != "completed":
        fail(f"Status={result['status']}")
        return

    outputs = result.get("outputs") or []
    filenames = {o["filename"] for o in outputs}
    if "out_a.txt" not in filenames or "out_b.txt" not in filenames:
        fail(f"Expected out_a.txt + out_b.txt, got: {filenames}")
        return

    # Verify content of both
    job_id = result["job_id"]
    ra = httpx.get(f"{BACKEND_URL}/jobs/{job_id}/outputs/out_a.txt", headers=HEADERS)
    rb = httpx.get(f"{BACKEND_URL}/jobs/{job_id}/outputs/out_b.txt", headers=HEADERS)

    if ra.text.strip() != fa.decode().upper() or rb.text.strip() != fb.decode().upper():
        fail(f"Content mismatch: a={ra.text!r}, b={rb.text!r}")
        return

    ok("Both files correct")


# ==================================================================
# TEST 5 — No-input job still works (backward compat)
# ==================================================================
def test_no_input_backward_compat():
    print_step("5: No-input job (backward compat)")

    result = submit_and_wait({"type": "python", "code": "print(2 + 3)"})
    if not result:
        fail("Job submission failed")
        return

    if result["status"] == "completed" and "5" in (result.get("stdout") or ""):
        ok()
    else:
        fail(f"Status={result['status']}, stdout={result.get('stdout')}")


# ==================================================================
# TEST 6 — Invalid filename rejected at backend
# ==================================================================
def test_invalid_filename_rejected():
    print_step("6: Path-traversal filename rejected")

    bad_filenames = [
        "../../etc/passwd",
        "..\\Windows\\secret",
        "/etc/shadow",
        "file\x00name",
        ".",
        "..",
    ]

    all_rejected = True
    for bad in bad_filenames:
        resp = httpx.post(
            f"{BACKEND_URL}/jobs",
            json={"payload": {
                "type": "python",
                "code": "pass",
                "inputs": {bad: encode_file(b"evil")},
            }},
            headers=HEADERS,
        )
        if resp.status_code == 200:
            all_rejected = False
            fail(f"Bad filename {bad!r} was accepted (HTTP 200)")
            break

    if all_rejected:
        ok(f"All {len(bad_filenames)} bad filenames rejected")


# ==================================================================
# TEST 7 — Oversized input rejected at backend
# ==================================================================
def test_oversized_input_rejected():
    print_step("7: Oversized input file rejected")

    # 11 MB — exceeds 10 MB per-file limit
    big = b"X" * (11 * 1024 * 1024)
    resp = httpx.post(
        f"{BACKEND_URL}/jobs",
        json={"payload": {
            "type": "python",
            "code": "pass",
            "inputs": {"big.bin": encode_file(big)},
        }},
        headers=HEADERS,
        timeout=30.0,
    )

    if resp.status_code == 400:
        ok("Oversized file correctly rejected")
    else:
        fail(f"Expected HTTP 400, got {resp.status_code}")


# ==================================================================
# TEST 8 — Failed workload — cleanup still happens
# ==================================================================
def test_failure_cleanup():
    print_step("8: Failed workload — workspace cleaned up")

    csv_bytes = b"name,value\nX,1\n"
    code = 'raise RuntimeError("intentional failure")'

    result = submit_and_wait({
        "type": "python",
        "code": code,
        "inputs": {"data.csv": encode_file(csv_bytes)},
    })

    if not result:
        fail("Job submission failed")
        return

    if result["status"] != "failed":
        fail(f"Expected failed, got {result['status']}")
        return

    if not _no_leftover_containers():
        fail("Container not cleaned up after failure")
        return

    ok("Failure handled, no leftover containers")


# ==================================================================
# TEST 9 — Timeout — cleanup still happens
# ==================================================================
def test_timeout_cleanup():
    print_step("9: Timeout — workspace cleaned up")

    result = submit_and_wait(
        {"type": "python", "code": "import time\ntime.sleep(120)"},
        timeout=60,
    )

    if not result:
        fail("Job submission failed")
        return

    error = result.get("error") or ""
    if result["status"] != "failed" or not _is_timeout_error(error):
        fail(f"Status={result['status']}, error={error}")
        return

    if not _no_leftover_containers():
        fail("Container not cleaned up after timeout")
        return

    ok("Timeout handled, no leftover containers")


# ==================================================================
# TEST 10 — Nonexistent output file returns 404
# ==================================================================
def test_missing_output_404():
    print_step("10: Nonexistent output file -> 404")

    result = submit_and_wait({"type": "python", "code": "print('nothing')"})
    if not result:
        fail("Job submission failed")
        return

    job_id = result["job_id"]
    resp = httpx.get(
        f"{BACKEND_URL}/jobs/{job_id}/outputs/does_not_exist.txt",
        headers=HEADERS,
    )

    if resp.status_code == 404:
        ok()
    else:
        fail(f"Expected 404, got {resp.status_code}")


# ==================================================================
# TEST 11 — Worker recovery after file-based job
# ==================================================================
def test_worker_recovery():
    print_step("11: Worker recovery after file-based job")

    time.sleep(2)  # let previous job finish freeing the worker

    result = submit_and_wait({"type": "python", "code": "print(7 * 6)"})
    if not result:
        fail("Recovery job submission failed")
        return

    if result["status"] == "completed" and "42" in (result.get("stdout") or ""):
        ok()
    else:
        fail(f"Status={result['status']}, stdout={result.get('stdout')}")


# ==================================================================
# TEST 12 — Workspace cleaned up (no leftover dirs / containers)
# ==================================================================
def test_workspace_cleanup():
    print_step("12: No leftover containers after all jobs")

    if _no_leftover_containers():
        ok()
    else:
        try:
            client = docker.from_env()
            leftovers = client.containers.list(all=True, filters={"name": "compute-job-"})
            fail(f"{len(leftovers)} leftover container(s): {[c.name for c in leftovers]}")
        except Exception as e:
            fail(f"Docker error: {e}")


# ==================================================================
# TEST 13–16 — Milestone regressions
# ==================================================================
def test_milestone_regression(n: int):
    step = 12 + n
    print_step(f"{step}: Milestone {n} regression")

    try:
        if n == 1:
            resp = httpx.get(f"{WORKER_URL}/")
            if resp.status_code == 200 and "status" in resp.json():
                ok()
            else:
                fail(f"Worker root returned {resp.status_code}")

        elif n == 2:
            resp = httpx.get(f"{WORKER_URL}/health")
            workers_resp = httpx.get(f"{BACKEND_URL}/workers", headers=HEADERS)
            if resp.status_code == 200 and workers_resp.status_code == 200:
                ok()
            else:
                fail(f"Health={resp.status_code}, Workers={workers_resp.status_code}")

        elif n == 3:
            resp = httpx.get(f"{BACKEND_URL}/jobs", headers=HEADERS)
            if resp.status_code == 200:
                ok()
            else:
                fail(f"Jobs list returned {resp.status_code}")

        elif n == 4:
            result = submit_and_wait({"type": "python", "code": "print(1 + 1)"})
            if result and result["status"] == "completed" and "2" in (result.get("stdout") or ""):
                ok()
            else:
                fail(f"M4 regression: {result}")

    except Exception as e:
        fail(f"Error: {e}")


# ==================================================================
# Main
# ==================================================================
if __name__ == "__main__":
    print("========================================")
    print("MILESTONE 5: FILE-BASED WORKLOADS TEST")
    print("========================================")

    job_id = test_single_file_roundtrip()
    test_file_download(job_id)
    test_output_contents(job_id)
    test_multiple_files()
    test_no_input_backward_compat()
    test_invalid_filename_rejected()
    test_oversized_input_rejected()
    test_failure_cleanup()
    test_timeout_cleanup()
    test_missing_output_404()
    test_worker_recovery()
    test_workspace_cleanup()
    test_milestone_regression(1)
    test_milestone_regression(2)
    test_milestone_regression(3)
    test_milestone_regression(4)

    print("\n========================================")
    if failed == 0:
        print("VERIFICATION RESULT: SUCCESS")
    else:
        print(f"VERIFICATION RESULT: {passed} PASSED, {failed} FAILED")
    print("========================================")

    if failed == 0:
        print("\nMilestone 5 COMPLETE!")
    else:
        print(f"\n{failed} test(s) failed.")
        sys.exit(1)

