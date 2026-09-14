"""
Milestone 1 Verification Script
Run this script from Laptop A (Controller) to test communication with Laptop B (Worker).
"""

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def parse_url(url: str):
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme:
        url = "http://" + url
        parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (80 if parsed.scheme == "http" else 443)
    return url, host, port


def test_tcp_reachability(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.close()
        return True
    except Exception as e:
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Verify communication between Laptop A and Laptop B for Milestone 1."
    )
    parser.add_argument(
        "--worker-url",
        default="http://127.0.0.1:8001",
        help="Base URL of the worker node (e.g. http://192.168.1.50:8001)",
    )
    args = parser.parse_args()

    base_url, host, port = parse_url(args.worker_url)

    print("===================================================================")
    print("      MILESTONE 1: CONTROLLER -> WORKER CONNECTIVITY TEST          ")
    print("===================================================================")
    print(f" Target Worker Node : {base_url}")
    print(f" Target Host/IP     : {host}")
    print(f" Target Port        : {port}")
    print("-------------------------------------------------------------------")

    # Step 1: TCP Level Check
    print(f"[*] Step 1: Testing TCP socket connection to {host}:{port}...", end=" ", flush=True)
    if not test_tcp_reachability(host, port, timeout=4.0):
        print("FAILED!")
        print("\n[ERROR] Unable to open TCP connection to worker node.")
        print("\nTroubleshooting Tips:")
        print("1. Is the worker process running on Laptop B? (Run: python worker/worker.py)")
        print(f"2. Is '{host}' the correct local IP for Laptop B? (Check `ipconfig` on Laptop B)")
        print("3. Windows Defender Firewall on Laptop B may be blocking inbound TCP port 8001.")
        print("   To unblock on Laptop B (run in PowerShell as Administrator):")
        print("   New-NetFirewallRule -DisplayName \"Distributed Compute Worker\" -Direction Inbound -LocalPort 8001 -Protocol TCP -Action Allow")
        sys.exit(1)
    print("SUCCESS (TCP Connected)")

    # Step 2: HTTP GET / Check
    print(f"[*] Step 2: Sending HTTP GET request to {base_url}/...", end=" ", flush=True)
    start_time = time.time()
    req = urllib.request.Request(
        f"{base_url}/",
        headers={"User-Agent": "DistributedCompute-Controller/0.1.0"}
    )

    try:
        with urllib.request.urlopen(req, timeout=5.0) as response:
            latency_ms = (time.time() - start_time) * 1000.0
            status_code = response.getcode()
            body = response.read().decode("utf-8")
            data = json.loads(body)
            print("SUCCESS (HTTP 200)")
    except urllib.error.URLError as e:
        print(f"FAILED! ({e})")
        sys.exit(1)

    # Step 3: Display Verification Results
    print("\n-------------------------------------------------------------------")
    print("                   VERIFICATION RESULT: SUCCESS                   ")
    print("-------------------------------------------------------------------")
    print(f" [+] Received Message : {data.get('message')}")
    print(f" [+] Worker Hostname  : {data.get('hostname')}")
    print(f" [+] Worker Status    : {data.get('status')}")
    print(f" [+] Round-Trip Time  : {latency_ms:.2f} ms")
    print("\nRaw Response Payload:")
    print(json.dumps(data, indent=2))
    print("===================================================================")
    print(" Milestone 1 COMPLETE! You can now proceed to Milestone 2.")
    print("===================================================================")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
