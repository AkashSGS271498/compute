"""
Resource collection for the worker node.

Collects host CPU and RAM metrics using psutil and derives the worker
state (AVAILABLE / BUSY / UNKNOWN).  If psutil is not installed the
module still works but returns zero values and UNKNOWN state.
"""

import time
from typing import Any, Dict, Optional

_START_TIME: float = time.time()  # set at import; overridden by worker.py

try:
    import psutil as _psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False


def collect_stats(
    current_job_id: Optional[str] = None,
    start_time: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Return a snapshot of host resource usage.

    Parameters
    ----------
    current_job_id:
        The job the worker is currently running, or None if idle.
    start_time:
        Unix timestamp when the worker process started (for uptime).

    Returns
    -------
    dict with keys:
        cpu_cores            int   — physical CPU cores on the host
        cpu_count_logical    int   — logical (hyper-threaded) cores
        cpu_percent          float — 1-second averaged CPU usage %
        memory_total_mb      int   — total installed RAM in MB
        memory_available_mb  int   — free + cache RAM available to new processes
        memory_used_mb       int   — RAM actively in use
        memory_percent       float — RAM usage %
        current_job_id       str|None
        worker_state         str   — AVAILABLE | BUSY | UNKNOWN
        uptime_seconds       float
        psutil_available     bool
    """
    t0 = start_time if start_time is not None else _START_TIME
    uptime = time.time() - t0

    if not _PSUTIL_AVAILABLE:
        state = "BUSY" if current_job_id else "UNKNOWN"
        return {
            "cpu_cores": 0,
            "cpu_count_logical": 0,
            "cpu_percent": 0.0,
            "memory_total_mb": 0,
            "memory_available_mb": 0,
            "memory_used_mb": 0,
            "memory_percent": 0.0,
            "current_job_id": current_job_id,
            "worker_state": state,
            "uptime_seconds": round(uptime, 1),
            "psutil_available": False,
        }

    cpu_cores = _psutil.cpu_count(logical=False) or 1
    cpu_logical = _psutil.cpu_count(logical=True) or cpu_cores

    # cpu_percent(interval=None) returns instantly using the last measurement.
    # We use interval=None to avoid blocking the HTTP request.
    cpu_pct = _psutil.cpu_percent(interval=None)

    vm = _psutil.virtual_memory()
    total_mb = vm.total // (1024 * 1024)
    avail_mb = vm.available // (1024 * 1024)
    used_mb  = vm.used // (1024 * 1024)

    if current_job_id:
        worker_state = "BUSY"
    else:
        worker_state = "AVAILABLE"

    return {
        "cpu_cores": cpu_cores,
        "cpu_count_logical": cpu_logical,
        "cpu_percent": round(cpu_pct, 1),
        "memory_total_mb": total_mb,
        "memory_available_mb": avail_mb,
        "memory_used_mb": used_mb,
        "memory_percent": round(vm.percent, 1),
        "current_job_id": current_job_id,
        "worker_state": worker_state,
        "uptime_seconds": round(uptime, 1),
        "psutil_available": True,
    }
