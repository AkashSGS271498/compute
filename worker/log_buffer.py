"""
Thread-safe, condition-variable-based log buffer for live job output streaming.

Usage (producer side – runs inside the Docker execution thread):
    buf = LogBuffer(job_id, maxlen=10_000)
    _live_logs[job_id] = buf
    buf.append("stdout", "Hello, world\\n")
    buf.seal("completed")           # job finished

Usage (consumer side – runs inside an SSE handler thread):
    for entry in buf.iter_from(0, timeout=120):
        yield entry                 # {"seq": int, "source": str, "line": str}
    # When iter_from returns, the job is done.
    yield {"event": "done", "status": buf.final_status}
"""

import threading
from typing import Iterator, Optional


class LogBuffer:
    """
    Append-only ring buffer of log lines produced by a running job.

    Thread-safe: multiple consumers can call iter_from() concurrently while
    the producer calls append().  When the job finishes, the producer calls
    seal() and all blocked consumers wake up.

    Args:
        job_id:   The job this buffer belongs to (for diagnostics).
        maxlen:   Maximum number of log entries retained (oldest dropped).
    """

    def __init__(self, job_id: str, maxlen: int = 10_000) -> None:
        self.job_id = job_id
        self.maxlen = maxlen
        self.final_status: Optional[str] = None   # set by seal()

        self._lock = threading.Condition(threading.Lock())
        self._entries: list[dict] = []  # {seq, source, line}
        self._sealed = False            # True once the job is done

    # ------------------------------------------------------------------
    # Producer API
    # ------------------------------------------------------------------

    def append(self, source: str, line: str) -> None:
        """Add a log line.  source is 'stdout' or 'stderr'."""
        with self._lock:
            seq = len(self._entries)
            self._entries.append({"seq": seq, "source": source, "line": line})
            # Trim if over limit (keep most recent)
            if len(self._entries) > self.maxlen:
                self._entries = self._entries[-self.maxlen:]
            self._lock.notify_all()

    def seal(self, final_status: str) -> None:
        """
        Mark the job as finished.  All waiting consumers will unblock.
        Call exactly once, after the last append().
        """
        with self._lock:
            self.final_status = final_status
            self._sealed = True
            self._lock.notify_all()

    # ------------------------------------------------------------------
    # Consumer API
    # ------------------------------------------------------------------

    def iter_from(
        self,
        start_seq: int = 0,
        timeout: float = 300.0,
        poll_interval: float = 0.1,
    ) -> Iterator[dict]:
        """
        Yield log entries starting at *start_seq*, blocking until new lines
        arrive or the buffer is sealed.

        Args:
            start_seq:     First sequence number to yield (allows resume).
            timeout:       Give up after this many seconds (safety valve).
            poll_interval: Maximum time to hold the lock per iteration.

        Yields:
            dicts with keys: seq (int), source (str), line (str)

        Returns when:
            - the buffer is sealed (job finished), OR
            - timeout is reached.
        """
        import time
        deadline = time.monotonic() + timeout
        cursor = start_seq

        while True:
            with self._lock:
                # Drain all available entries from cursor onward
                available = self._entries[cursor:]
                sealed = self._sealed

                if not available:
                    if sealed:
                        return
                    # Block until something changes or timeout
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return
                    self._lock.wait(timeout=min(remaining, poll_interval))
                    continue

            for entry in available:
                yield entry
            cursor += len(available)

            if sealed and cursor >= len(self._entries):
                return

            if time.monotonic() >= deadline:
                return

    @property
    def is_sealed(self) -> bool:
        with self._lock:
            return self._sealed

    def snapshot(self) -> list[dict]:
        """Return a copy of all buffered entries (for debugging/tests)."""
        with self._lock:
            return list(self._entries)

