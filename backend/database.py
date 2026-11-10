"""
backend/database.py — M9: SQLite persistence layer.

All writes are synchronous (sqlite3, stdlib).  The module is imported once and
the connection/schema are initialised on first import.  A module-level
threading.Lock guards every write so the caller threads (scheduler loop,
FastAPI request handlers) are safe.

Schema
------
workers  — one row per registered worker, JSON blob for extra fields
jobs     — one row per job, JSON blob for payload + result fields
outputs  — one row per output *file* produced by a job (bytes stored on disk)

Output files are written to  <output_dir>/<job_id>/<filename>  so they survive
backend restarts.  The 'outputs' table only stores the path + size metadata.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.config import settings

# ------------------------------------------------------------------
# Paths
# ------------------------------------------------------------------
_DB_PATH = os.path.abspath(settings.db_path)
_OUTPUT_DIR = os.path.abspath(settings.output_dir)

os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
os.makedirs(_OUTPUT_DIR, exist_ok=True)

# ------------------------------------------------------------------
# Connection — single shared connection with check_same_thread=False
# (we guard access with our own lock).
# ------------------------------------------------------------------
_conn: sqlite3.Connection = sqlite3.connect(
    _DB_PATH, check_same_thread=False, isolation_level=None  # autocommit
)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")   # allow concurrent readers
_conn.execute("PRAGMA foreign_keys=ON")

_lock = threading.Lock()

# ------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS workers (
    worker_id       TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    host            TEXT NOT NULL,
    port            INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'online',
    worker_state    TEXT NOT NULL DEFAULT 'AVAILABLE',
    current_job_id  TEXT,
    last_heartbeat  TEXT NOT NULL,
    registered_at   TEXT NOT NULL,
    extra           TEXT NOT NULL DEFAULT '{}'   -- JSON: stats, etc.
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'queued',
    worker_id       TEXT,
    created_at      TEXT NOT NULL,
    assigned_at     TEXT,
    started_at      TEXT,
    completed_at    TEXT,
    stdout          TEXT,
    stderr          TEXT,
    exit_code       INTEGER,
    error           TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    payload         TEXT NOT NULL DEFAULT '{}',   -- JSON
    outputs_meta    TEXT NOT NULL DEFAULT '[]'    -- JSON list of {filename, size}
);

CREATE TABLE IF NOT EXISTS outputs (
    job_id      TEXT NOT NULL,
    filename    TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, filename)
);
"""

with _lock:
    _conn.executescript(_SCHEMA)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _output_path(job_id: str, filename: str) -> str:
    job_dir = os.path.join(_OUTPUT_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    return os.path.join(job_dir, filename)


# ------------------------------------------------------------------
# Worker CRUD
# ------------------------------------------------------------------

def db_register_worker(worker_id: str, data: Dict[str, Any]) -> None:
    now = _now()
    extra = json.dumps({})
    with _lock:
        _conn.execute(
            """
            INSERT INTO workers
                (worker_id, name, host, port, status, worker_state,
                 current_job_id, last_heartbeat, registered_at, extra)
            VALUES (?, ?, ?, ?, 'online', 'AVAILABLE', NULL, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                name=excluded.name,
                host=excluded.host,
                port=excluded.port,
                status='online',
                worker_state='AVAILABLE',
                last_heartbeat=excluded.last_heartbeat,
                extra=excluded.extra
            """,
            (
                worker_id,
                data.get("name", worker_id),
                data.get("host", "127.0.0.1"),
                data.get("port", 8001),
                now,
                now,
                extra,
            ),
        )


def db_update_heartbeat(worker_id: str, stats: Optional[Dict[str, Any]] = None) -> None:
    with _lock:
        row = _conn.execute(
            "SELECT extra FROM workers WHERE worker_id=?", (worker_id,)
        ).fetchone()
        if row is None:
            return
        extra = json.loads(row["extra"])
        if stats:
            extra["stats"] = stats
            extra["worker_state"] = stats.get("worker_state", "AVAILABLE")
        new_state = extra.get("worker_state", "AVAILABLE")
        _conn.execute(
            """
            UPDATE workers
            SET last_heartbeat=?, status='online', worker_state=?, extra=?
            WHERE worker_id=?
            """,
            (_now(), new_state, json.dumps(extra), worker_id),
        )


def db_update_worker(worker_id: str, data: Dict[str, Any]) -> None:
    """Merge arbitrary key/value pairs into a worker record."""
    with _lock:
        row = _conn.execute(
            "SELECT * FROM workers WHERE worker_id=?", (worker_id,)
        ).fetchone()
        if row is None:
            return

        # Fields that have dedicated columns
        col_map = {
            "status": "status",
            "worker_state": "worker_state",
            "current_job_id": "current_job_id",
            "last_heartbeat": "last_heartbeat",
        }
        set_clauses = []
        params: List[Any] = []
        extra = json.loads(row["extra"])

        for key, val in data.items():
            if key in col_map:
                set_clauses.append(f"{col_map[key]}=?")
                params.append(val)
            else:
                extra[key] = val

        set_clauses.append("extra=?")
        params.append(json.dumps(extra))
        params.append(worker_id)

        if set_clauses:
            sql = f"UPDATE workers SET {', '.join(set_clauses)} WHERE worker_id=?"
            _conn.execute(sql, params)


def db_get_worker(worker_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        row = _conn.execute(
            "SELECT * FROM workers WHERE worker_id=?", (worker_id,)
        ).fetchone()
    return _row_to_worker(row) if row else None


def db_list_workers() -> Dict[str, Dict[str, Any]]:
    with _lock:
        rows = _conn.execute("SELECT * FROM workers").fetchall()
    return {row["worker_id"]: _row_to_worker(row) for row in rows}


def _row_to_worker(row: sqlite3.Row) -> Dict[str, Any]:
    extra = json.loads(row["extra"])
    return {
        "worker_id": row["worker_id"],
        "name": row["name"],
        "host": row["host"],
        "port": row["port"],
        "status": row["status"],
        "worker_state": row["worker_state"],
        "current_job_id": row["current_job_id"],
        "last_heartbeat": row["last_heartbeat"],
        "registered_at": row["registered_at"],
        **extra,   # stats, etc.
    }


# ------------------------------------------------------------------
# Job CRUD
# ------------------------------------------------------------------

def db_add_job(job_id: str, data: Dict[str, Any]) -> None:
    with _lock:
        _conn.execute(
            """
            INSERT OR IGNORE INTO jobs
                (job_id, status, worker_id, created_at, assigned_at,
                 started_at, completed_at, stdout, stderr, exit_code,
                 error, retry_count, payload, outputs_meta)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job_id,
                data.get("status", "queued"),
                data.get("worker_id"),
                data.get("created_at", _now()),
                data.get("assigned_at"),
                data.get("started_at"),
                data.get("completed_at"),
                data.get("stdout"),
                data.get("stderr"),
                data.get("exit_code"),
                data.get("error"),
                data.get("retry_count", 0),
                json.dumps(data.get("payload", {})),
                json.dumps(data.get("outputs", []) or []),
            ),
        )


def db_update_job(job_id: str, data: Dict[str, Any]) -> None:
    if not data:
        return
    col_map = {
        "status", "worker_id", "assigned_at", "started_at",
        "completed_at", "stdout", "stderr", "exit_code",
        "error", "retry_count",
    }
    with _lock:
        row = _conn.execute(
            "SELECT payload, outputs_meta FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None:
            return

        set_clauses = []
        params: List[Any] = []

        for key, val in data.items():
            if key in col_map:
                set_clauses.append(f"{key}=?")
                params.append(val)
            elif key == "payload":
                set_clauses.append("payload=?")
                params.append(json.dumps(val))
            elif key == "outputs":
                set_clauses.append("outputs_meta=?")
                params.append(json.dumps(val or []))

        if not set_clauses:
            return

        params.append(job_id)
        sql = f"UPDATE jobs SET {', '.join(set_clauses)} WHERE job_id=?"
        _conn.execute(sql, params)


def db_get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        row = _conn.execute(
            "SELECT * FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
    return _row_to_job(row) if row else None


def db_list_jobs() -> Dict[str, Dict[str, Any]]:
    with _lock:
        rows = _conn.execute("SELECT * FROM jobs").fetchall()
    return {row["job_id"]: _row_to_job(row) for row in rows}


def _row_to_job(row: sqlite3.Row) -> Dict[str, Any]:
    outputs_raw = json.loads(row["outputs_meta"] or "[]")
    return {
        "job_id": row["job_id"],
        "status": row["status"],
        "worker_id": row["worker_id"],
        "created_at": row["created_at"],
        "assigned_at": row["assigned_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "stdout": row["stdout"],
        "stderr": row["stderr"],
        "exit_code": row["exit_code"],
        "error": row["error"],
        "retry_count": row["retry_count"],
        "payload": json.loads(row["payload"] or "{}"),
        "outputs": outputs_raw,
    }


# ------------------------------------------------------------------
# Output file storage (disk-backed)
# ------------------------------------------------------------------

def db_store_output_file(job_id: str, filename: str, content: bytes) -> None:
    path = _output_path(job_id, filename)
    with open(path, "wb") as fh:
        fh.write(content)
    size = len(content)
    with _lock:
        _conn.execute(
            """
            INSERT OR REPLACE INTO outputs (job_id, filename, file_path, size)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, filename, path, size),
        )


def db_get_output_file(job_id: str, filename: str) -> Optional[bytes]:
    with _lock:
        row = _conn.execute(
            "SELECT file_path FROM outputs WHERE job_id=? AND filename=?",
            (job_id, filename),
        ).fetchone()
    if row is None:
        return None
    path = row["file_path"]
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


# ------------------------------------------------------------------
# DB-level diagnostics (used by /stats endpoint)
# ------------------------------------------------------------------

def db_counts() -> Dict[str, int]:
    with _lock:
        workers = _conn.execute("SELECT COUNT(*) FROM workers").fetchone()[0]
        jobs    = _conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        outputs = _conn.execute("SELECT COUNT(*) FROM outputs").fetchone()[0]
    return {"workers": workers, "jobs": jobs, "output_files": outputs}
