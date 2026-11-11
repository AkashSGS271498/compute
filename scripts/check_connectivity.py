#!/usr/bin/env python3
"""
scripts/check_connectivity.py — M10: End-to-end connectivity probe.

Usage:
    python scripts/check_connectivity.py [--backend http://...] [--worker http://...]

Checks:
  1. Backend reachable
  2. Backend auth works (Bearer token)
  3. Worker(s) registered and ONLINE
  4. Can submit a simple job
  5. Job completes successfully
  6. Network mode reported per worker (Tailscale vs LAN)
"""

import argparse
import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)


def _ok(label, detail=""):
    print(f"  ✅  {label}" + (f"  ({detail})" if detail else ""))

def _fail(label, detail=""):
    print(f"  ❌  {label}" + (f"  — {detail}" if detail else ""))

def _warn(label, detail=""):
    print(f"  ⚠️   {label}" + (f"  — {detail}" if detail else ""))


def main():
    parser = argparse.ArgumentParser(description="Distributed Compute connectivity probe")
    parser.add_argument("--backend", default="http://127.0.0.1:8000", help="Backend URL")
    parser.add_argument("--token",   default="supersecret",           help="Auth token")
    parser.add_argument("--timeout", type=int, default=60,            help="Job timeout (s)")
    args = parser.parse_args()

    BACKEND = args.backend.rstrip("/")
    HEADERS = {"Authorization": f"Bearer {args.token}"}

    print()
    print("=" * 60)
    print("  Distributed Compute — Connectivity Check")
    print(f"  Backend : {BACKEND}")
    print("=" * 60)
    print()

    all_ok = True

    # ── 1. Backend reachable ─────────────────────────────────────
    print("Step 1: Backend reachable...")
    try:
        r = httpx.get(f"{BACKEND}/workers", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            _ok("Backend reachable", f"HTTP 200")
        elif r.status_code == 401:
            _fail("Backend auth failed", "wrong token?")
            all_ok = False
        else:
            _fail("Backend returned unexpected status", f"HTTP {r.status_code}")
            all_ok = False
    except Exception as e:
        _fail("Cannot reach backend", str(e))
        print()
        print("  Hint: Is the backend running?")
        print(f"    python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000")
        sys.exit(1)

    # ── 2. Workers registered ────────────────────────────────────
    print("\nStep 2: Workers registered and online...")
    workers = r.json()
    online = {wid: w for wid, w in workers.items() if w.get("status") == "online"}
    offline = {wid: w for wid, w in workers.items() if w.get("status") != "online"}

    if online:
        _ok(f"{len(online)} worker(s) ONLINE")
    else:
        _warn("No workers currently ONLINE")
        all_ok = False

    if offline:
        _warn(f"{len(offline)} worker(s) OFFLINE", f"IDs: {list(offline.keys())[:3]}")

    # ── 3. Network mode per worker ───────────────────────────────
    print("\nStep 3: Network mode per worker...")
    for wid, w in online.items():
        host = w.get("host", "?")
        stats = w.get("stats", {})
        name = w.get("name", wid[:8])
        # Detect if registered via Tailscale IP (100.64.x.x – 100.127.x.x)
        is_ts = host.startswith("100.") and 64 <= int(host.split(".")[1]) <= 127
        mode = "🌐 Tailscale" if is_ts else "🏠 LAN"
        _ok(f"Worker {name[:20]}", f"{mode}  {host}:{w.get('port', '?')}")

    if not online:
        _warn("No online workers to check")

    # ── 4. Submit a test job ─────────────────────────────────────
    print("\nStep 4: Submit a test job...")
    if not online:
        _warn("Skipped — no online workers")
    else:
        try:
            resp = httpx.post(
                f"{BACKEND}/jobs",
                headers=HEADERS,
                json={"payload": {"type": "python", "code": "import socket; print('hello from', socket.gethostname())"}},
                timeout=10,
            )
            resp.raise_for_status()
            job_id = resp.json()["job_id"]
            _ok("Job submitted", job_id[:8])
        except Exception as e:
            _fail("Job submission failed", str(e))
            all_ok = False
            job_id = None

        # ── 5. Wait for completion ───────────────────────────────
        print("\nStep 5: Job completes on a worker...")
        if job_id:
            deadline = time.time() + args.timeout
            job = None
            while time.time() < deadline:
                try:
                    j = httpx.get(f"{BACKEND}/jobs/{job_id}", headers=HEADERS, timeout=5).json()
                    if j["status"] in ("completed", "failed"):
                        job = j
                        break
                except Exception:
                    pass
                time.sleep(1)

            if job and job["status"] == "completed":
                stdout = (job.get("stdout") or "").strip()
                _ok("Job completed", f"stdout={stdout!r}")
            elif job:
                _fail("Job failed", job.get("error") or job["status"])
                all_ok = False
            else:
                _fail("Job timed out", f">{args.timeout}s")
                all_ok = False

    # ── 6. Cluster stats ─────────────────────────────────────────
    print("\nStep 6: Cluster stats...")
    try:
        r = httpx.get(f"{BACKEND}/stats", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            data = r.json()
            _ok("Cluster stats available", (
                f"workers={data['workers']['online']}/{data['workers']['total']} online, "
                f"jobs_total={data['jobs']['total']}, "
                f"db_rows={data['storage']['jobs']}"
            ))
        else:
            _warn("Could not fetch /stats", f"HTTP {r.status_code}")
    except Exception as e:
        _warn("Could not fetch /stats", str(e))

    # ── Summary ──────────────────────────────────────────────────
    print()
    print("=" * 60)
    if all_ok and online:
        print("✅  All checks passed — cluster is healthy!")
    elif not online:
        print("⚠️   Backend is up but no workers are online.")
        print("     Start a worker with:  python worker\\worker.py")
    else:
        print("❌  Some checks failed — see above for details.")
    print("=" * 60)
    print()

    if not (all_ok and online):
        sys.exit(1)


if __name__ == "__main__":
    main()
