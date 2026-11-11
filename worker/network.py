"""
worker/network.py — M10: Smart network interface detection.

Priority order for registration IP:
  1. WORKER_REGISTER_HOST env var (explicit override — highest priority)
  2. Tailscale IP (100.64.x.x or 100.x.x.x) — cross-internet mesh
  3. Primary LAN IP (the one the OS would use to reach 8.8.8.8)
  4. Any non-loopback IPv4 from hostname resolution
  5. 127.0.0.1 (last resort)

WORKER_NETWORK_MODE controls which IPs are considered:
  auto      — full priority order above (default)
  tailscale — only return Tailscale IP; error if not found
  lan       — skip Tailscale; return LAN IP
  custom    — always use WORKER_REGISTER_HOST (must be set)
"""

import os
import socket
from typing import List, Optional, Tuple


# Tailscale uses the CGNAT range 100.64.0.0/10
_TAILSCALE_PREFIX = "100."


def _is_tailscale_ip(ip: str) -> bool:
    """Return True if the IP looks like a Tailscale address (100.64.x.x – 100.127.x.x)."""
    if not ip.startswith(_TAILSCALE_PREFIX):
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        second = int(parts[1])
        return 64 <= second <= 127
    except ValueError:
        return False


def _get_all_ipv4s() -> List[str]:
    """Return every non-loopback IPv4 address the machine currently has."""
    ips: List[str] = []

    # Method 1: UDP trick — reveals the primary outbound IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            ips.append(ip)
    except Exception:
        pass

    # Method 2: hostname resolution
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if ":" not in ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass

    # Method 3: scan network interfaces via socket (portable, no psutil needed)
    try:
        import socket as _s
        # On Windows, getaddrinfo with "" returns all bound addresses
        for info in _s.getaddrinfo("", None, _s.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass

    return ips


def get_tailscale_ip() -> Optional[str]:
    """Return the machine's Tailscale IP, or None if Tailscale is not active."""
    for ip in _get_all_ipv4s():
        if _is_tailscale_ip(ip):
            return ip
    return None


def get_lan_ip() -> Optional[str]:
    """Return the primary LAN IP (non-Tailscale, non-loopback)."""
    for ip in _get_all_ipv4s():
        if not _is_tailscale_ip(ip):
            return ip
    return None


def get_tailscale_hostname() -> Optional[str]:
    """
    Try to resolve the local Tailscale MagicDNS hostname.
    Returns e.g. 'my-laptop.tail12345.ts.net' or None.
    Requires Tailscale CLI to be in PATH.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            # Self hostname is under data["Self"]["DNSName"]
            dns_name = data.get("Self", {}).get("DNSName", "")
            return dns_name.rstrip(".") if dns_name else None
    except Exception:
        pass
    return None


def get_tailscale_status() -> dict:
    """
    Return a dict with Tailscale connectivity information:
      installed   — bool: tailscale CLI found in PATH
      running     — bool: tailscale daemon is up
      ip          — str | None: Tailscale IP of this machine
      hostname    — str | None: MagicDNS hostname
      peers       — int: number of online peers
    """
    status = {
        "installed": False,
        "running": False,
        "ip": get_tailscale_ip(),
        "hostname": None,
        "peers": 0,
    }

    import subprocess, shutil
    if shutil.which("tailscale") is None:
        return status

    status["installed"] = True

    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            status["running"] = True
            status["hostname"] = data.get("Self", {}).get("DNSName", "").rstrip(".")
            status["peers"] = sum(
                1 for p in data.get("Peer", {}).values()
                if p.get("Online", False)
            )
    except Exception:
        pass

    return status


NetworkInfo = Tuple[str, str, List[str]]
# (registration_ip, network_mode, all_ips)


def resolve_registration_ip(
    mode: str = "auto",
    override: Optional[str] = None,
) -> NetworkInfo:
    """
    Return (registration_ip, network_mode, all_ips).

    Args:
        mode: 'auto' | 'tailscale' | 'lan' | 'custom'
        override: explicit IP/hostname (used when mode='custom' or WORKER_REGISTER_HOST is set)
    """
    all_ips = _get_all_ipv4s() or ["127.0.0.1"]

    # Explicit override always wins
    if override:
        return override, "custom", all_ips

    if mode == "custom":
        raise ValueError(
            "WORKER_NETWORK_MODE=custom requires WORKER_REGISTER_HOST to be set"
        )

    ts_ip = get_tailscale_ip()

    if mode == "tailscale":
        if ts_ip:
            return ts_ip, "tailscale", all_ips
        raise RuntimeError(
            "WORKER_NETWORK_MODE=tailscale but no Tailscale IP found. "
            "Is Tailscale installed and connected? (run: tailscale up)"
        )

    if mode == "lan":
        lan = get_lan_ip()
        return (lan or "127.0.0.1"), "lan", all_ips

    # mode == "auto": prefer Tailscale, fall back to LAN
    if ts_ip:
        return ts_ip, "tailscale", all_ips

    lan = get_lan_ip()
    return (lan or "127.0.0.1"), "lan", all_ips
