#!/usr/bin/env python3
"""
scripts/setup_tailscale.py — M10: Tailscale setup checker and onboarding guide.

Usage:
    python scripts/setup_tailscale.py

Checks whether Tailscale is installed and connected, then prints a ready-to-run
configuration for adding this machine to the distributed compute cluster.
"""

import sys
import os

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from worker.network import get_tailscale_status, get_lan_ip, _get_all_ipv4s
except ImportError:
    print("ERROR: Run this script from the project root directory.")
    sys.exit(1)


def _box(title: str, lines: list[str]) -> str:
    width = max(len(title), max(len(l) for l in lines)) + 4
    top    = "┌" + "─" * width + "┐"
    t_line = "│  " + title.ljust(width - 2) + "│"
    sep    = "├" + "─" * width + "┤"
    body   = ["│  " + l.ljust(width - 2) + "│" for l in lines]
    bot    = "└" + "─" * width + "┘"
    return "\n".join([top, t_line, sep] + body + [bot])


def main():
    print()
    print("=" * 60)
    print("  Personal Distributed Compute — Tailscale Setup Checker")
    print("=" * 60)
    print()

    ts = get_tailscale_status()
    all_ips = _get_all_ipv4s()
    lan_ip  = get_lan_ip()

    # ── Status ──────────────────────────────────────────────────
    print("📡  Network status\n")

    if ts["installed"]:
        print(f"  ✅  Tailscale CLI   : installed")
    else:
        print(f"  ❌  Tailscale CLI   : NOT FOUND  →  https://tailscale.com/download")

    if ts["running"]:
        print(f"  ✅  Tailscale daemon: running")
    elif ts["installed"]:
        print(f"  ⚠️   Tailscale daemon: not running  →  run: tailscale up")
    else:
        print(f"  ❌  Tailscale daemon: n/a")

    ts_ip = ts["ip"]
    if ts_ip:
        print(f"  ✅  Tailscale IP    : {ts_ip}")
    else:
        print(f"  ❌  Tailscale IP    : not detected")

    if ts.get("hostname"):
        print(f"  ✅  MagicDNS host   : {ts['hostname']}")

    print(f"  ℹ️   LAN IP           : {lan_ip or 'none'}")
    print(f"  ℹ️   All IPs          : {', '.join(all_ips) if all_ips else 'none'}")
    print(f"  ℹ️   Tailscale peers  : {ts['peers']}")

    # ── Recommended config ───────────────────────────────────────
    print()
    print("─" * 60)
    print("🔧  Recommended configuration\n")

    if ts_ip:
        reg_ip  = ts_ip
        net_mode = "tailscale"
        backend_url_hint = f"http://<BACKEND_TAILSCALE_IP>:8000"
        if ts.get("hostname"):
            backend_url_hint = f"http://<backend-magicDNS-name>.ts.net:8000"
    else:
        reg_ip  = lan_ip or "127.0.0.1"
        net_mode = "lan"
        backend_url_hint = f"http://<BACKEND_LAN_IP>:8000"

    print(_box(
        "Worker environment variables",
        [
            f"WORKER_NETWORK_MODE = {net_mode}",
            f"BACKEND_URL         = {backend_url_hint}",
            f"BACKEND_SECRET      = supersecret",
        ]
    ))
    print()

    if ts_ip:
        print(_box(
            "PowerShell — start worker (Tailscale mode)",
            [
                f"$env:WORKER_NETWORK_MODE = 'tailscale'",
                f"$env:BACKEND_URL         = '{backend_url_hint}'",
                f"$env:BACKEND_SECRET      = 'supersecret'",
                f"python worker\\worker.py",
            ]
        ))
    else:
        print(_box(
            "PowerShell — start worker (LAN mode)",
            [
                f"$env:BACKEND_URL    = '{backend_url_hint}'",
                f"$env:BACKEND_SECRET = 'supersecret'",
                f"python worker\\worker.py",
            ]
        ))

    # ── Install guide if not installed ──────────────────────────
    if not ts["installed"]:
        print()
        print("─" * 60)
        print("📦  How to install Tailscale\n")
        print("  Windows  : https://tailscale.com/download/windows")
        print("  macOS    : https://tailscale.com/download/mac")
        print("  Linux    : curl -fsSL https://tailscale.com/install.sh | sh")
        print()
        print("  After install:")
        print("    1. Run:  tailscale up")
        print("    2. Sign in at https://login.tailscale.com (free for personal use)")
        print("    3. Repeat on EVERY machine that joins the cluster")
        print("    4. Re-run this script to verify")

    # ── ACL policy hint ─────────────────────────────────────────
    print()
    print("─" * 60)
    print("🔒  Recommended Tailscale ACL (admin.tailscale.com → Access controls)\n")
    print('  {')
    print('    "acls": [')
    print('      {')
    print('        "action": "accept",')
    print('        "src":    ["tag:dc-cluster"],')
    print('        "dst":    ["tag:dc-cluster:8000", "tag:dc-cluster:8001"]')
    print('      }')
    print('    ],')
    print('    "tagOwners": {')
    print('      "tag:dc-cluster": ["autogroup:member"]')
    print('    }')
    print('  }')
    print()
    print("  Tag your backend/worker machines with 'dc-cluster' in the Tailscale")
    print("  admin console so only cluster members can reach ports 8000/8001.")

    print()
    print("=" * 60)

    if ts_ip:
        print("✅  Ready for cross-internet cluster (Tailscale mode)")
    elif lan_ip:
        print("⚠️   Ready for LAN cluster only. Install Tailscale for internet mesh.")
    else:
        print("❌  No usable network IP found.")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
