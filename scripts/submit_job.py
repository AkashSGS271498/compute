import argparse
import sys
import httpx
import json
from pathlib import Path
import time

# Backend configuration (matches backend/config.py defaults)
BACKEND_URL = "http://127.0.0.1:8000"
AUTH_TOKEN = "supersecret"
HEADERS = {"Authorization": f"Bearer {AUTH_TOKEN}", "Content-Type": "application/json"}

def get_workers():
    resp = httpx.get(f"{BACKEND_URL}/workers", headers=HEADERS)
    resp.raise_for_status()
    return resp.json()

def submit_job(command: str, args: list[str]):
    payload = {"job_name": "cli_job", "command": command, "args": args}
    resp = httpx.post(f"{BACKEND_URL}/submit", json=payload, headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()
    job_id = data["job_id"]
    print(f"Job submitted, id={job_id}")
    return job_id

def poll_result(job_id: str):
    while True:
        resp = httpx.get(f"{BACKEND_URL}/result/{job_id}", headers=HEADERS)
        resp.raise_for_status()
        result = resp.json()
        if result["status"] in ("completed", "error"):
            return result
        time.sleep(2)

def dispatch_to_worker(worker_info: dict, command: str, args: list[str]):
    worker_url = f"http://{worker_info['host']}:{worker_info['port']}/run"
    payload = {"command": command, "args": args}
    resp = httpx.post(worker_url, json=payload, timeout=30.0)
    resp.raise_for_status()
    return resp.json()

def main():
    parser = argparse.ArgumentParser(description="Submit a command job via the distributed‑compute backend.")
    parser.add_argument("--command", required=True, help="Executable to run, e.g., python")
    parser.add_argument("--args", nargs=argparse.REMAINDER, default=[], help="Arguments for the command")
    args = parser.parse_args()

    workers = get_workers()
    if not workers:
        print("No workers registered with the backend.")
        sys.exit(1)
    # Pick the first registered worker (MVP simple load‑balancing)
    worker_id, worker_info = next(iter(workers.items()))
    print(f"Using worker {worker_id} at {worker_info['host']}:{worker_info['port']}")

    # Submit job metadata to backend (for tracking only)
    job_id = submit_job(args.command, args.args)

    # Directly invoke the worker /run endpoint (backend does not auto‑dispatch yet)
    result = dispatch_to_worker(worker_info, args.command, args.args)
    print("Worker execution result:")
    print(json.dumps(result, indent=2))

    # Optionally poll the backend for stored result (not persisted in this MVP)
    # final = poll_result(job_id)
    # print("Backend‑recorded result:")
    # print(json.dumps(final, indent=2))

if __name__ == "__main__":
    main()
