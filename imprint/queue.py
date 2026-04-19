"""Command queue + dispatcher for ingest/refresh/retag/ingest-url.

Serializes jobs so only one heavy operation runs at a time (embeddings +
LLM tagging easily OOM a workstation when run in parallel). Jobs are
persisted to ``data/queue.sqlite3`` so the queue survives API restarts,
and a shared ``data/queue.lock`` file blocks direct CLI invocations while
a job is active.

Cancel sends SIGTERM to the subprocess's process group, then SIGKILL 3s
later if still alive. Because the child is spawned with
``start_new_session=True`` the whole process group — including any
in-flight httpx calls to the LLM tagger and any llama-cpp inference
threads — is killed together.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

from . import queue_lock


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


# ── Paths ──────────────────────────────────────────────────────

def _db_path() -> Path:
    from .config import get_data_dir
    return get_data_dir() / "queue.sqlite3"


def _logs_dir() -> Path:
    from .config import get_data_dir
    d = get_data_dir() / "queue_logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── SQLite ─────────────────────────────────────────────────────

_db_lock = threading.Lock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    command     TEXT NOT NULL,
    body_json   TEXT NOT NULL,
    status      TEXT NOT NULL,
    pid         INTEGER,
    pgid        INTEGER,
    exit_code   INTEGER,
    error       TEXT,
    created_at  REAL NOT NULL,
    started_at  REAL,
    ended_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
"""


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_db() -> None:
    with _db_lock, _connect() as conn:
        conn.executescript(_SCHEMA)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    body = d.pop("body_json", "{}")
    try:
        d["body"] = json.loads(body) if body else {}
    except json.JSONDecodeError:
        d["body"] = {}
    return d


# ── Ring buffers (live output) ─────────────────────────────────

_BUFFERS: dict[str, deque[str]] = {}
_BUFFER_EVENTS: dict[str, asyncio.Event] = {}
_BUFFER_DONE: set[str] = set()
_BUFFER_LOCK = threading.Lock()


def _append_output(job_id: str, text: str, loop: asyncio.AbstractEventLoop) -> None:
    with _BUFFER_LOCK:
        buf = _BUFFERS.setdefault(job_id, deque(maxlen=20000))
        buf.append(text)
        ev = _BUFFER_EVENTS.get(job_id)
    if ev is not None:
        loop.call_soon_threadsafe(ev.set)


def _mark_done(job_id: str, loop: asyncio.AbstractEventLoop) -> None:
    with _BUFFER_LOCK:
        _BUFFER_DONE.add(job_id)
        ev = _BUFFER_EVENTS.get(job_id)
    if ev is not None:
        loop.call_soon_threadsafe(ev.set)


# ── Public API ─────────────────────────────────────────────────

def enqueue(command: str, body: dict | None) -> str:
    _init_db()
    job_id = uuid.uuid4().hex
    body_json = json.dumps(body or {})
    now = time.time()
    with _db_lock, _connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, command, body_json, status, created_at) "
            "VALUES (?, ?, ?, 'queued', ?)",
            (job_id, command, body_json, now),
        )
    _notify_dispatcher()
    return job_id


def get_job(job_id: str) -> dict | None:
    with _db_lock, _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _row_to_dict(row) if row else None


def active_job() -> dict | None:
    with _db_lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='running' ORDER BY started_at LIMIT 1"
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_queue(recent_limit: int = 20) -> dict:
    _init_db()
    with _db_lock, _connect() as conn:
        active = conn.execute(
            "SELECT * FROM jobs WHERE status='running' ORDER BY started_at LIMIT 1"
        ).fetchone()
        queued = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at"
        ).fetchall()
        recent = conn.execute(
            "SELECT * FROM jobs WHERE status IN ('done','failed','cancelled') "
            "ORDER BY ended_at DESC LIMIT ?",
            (recent_limit,),
        ).fetchall()
    return {
        "active": _row_to_dict(active) if active else None,
        "queued": [_row_to_dict(r) for r in queued],
        "recent": [_row_to_dict(r) for r in recent],
    }


def cancel(job_id: str) -> dict:
    """Cancel a queued or running job.

    For queued jobs the status flips to ``cancelled`` atomically; the
    dispatcher will skip it. For running jobs we send SIGTERM to the
    subprocess's process group and schedule a SIGKILL after 3 seconds.
    If the recorded PID is already dead, the row is finalized immediately.
    """
    _init_db()
    with _db_lock, _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "not found"}
        status = row["status"]
        if status == "queued":
            conn.execute(
                "UPDATE jobs SET status='cancelled', ended_at=? WHERE id=?",
                (time.time(), job_id),
            )
            return {"ok": True, "was_running": False}
        if status != "running":
            return {"ok": False, "error": f"job is {status}"}
        pgid = row["pgid"]
        pid = row["pid"]
        existing_err = row["error"] or ""

    # Short-circuit: the recorded process is already dead (e.g. dispatcher
    # got wedged, SIGKILL already happened). Mark the row cancelled so the
    # UI releases it.
    if _pid_dead(pid) and _pgid_dead(pgid):
        with _db_lock, _connect() as conn:
            conn.execute(
                "UPDATE jobs SET status='cancelled', ended_at=?, "
                "error=COALESCE(error,'') || ? WHERE id=?",
                (time.time(), "cancelled (pid already gone); ", job_id),
            )
        return {"ok": True, "was_running": False, "pid_was_dead": True}

    # Running — signal the process group. The dispatcher observes proc.wait()
    # and transitions the row to cancelled once it reaps.
    if pgid:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        _schedule_sigkill(pgid)
    elif pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    # Idempotent: only append the marker once per job.
    if "cancelled by user" not in existing_err:
        with _db_lock, _connect() as conn:
            conn.execute(
                "UPDATE jobs SET error=COALESCE(error,'') || ? "
                "WHERE id=? AND status='running'",
                ("cancelled by user; ", job_id),
            )
    return {"ok": True, "was_running": True}


def _pid_dead(pid: int | None) -> bool:
    if not pid:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _pgid_dead(pgid: int | None) -> bool:
    if not pgid:
        return True
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _schedule_sigkill(pgid: int, delay: float = 3.0) -> None:
    """Send SIGKILL to ``pgid`` after ``delay`` seconds if still alive."""
    def _kill():
        time.sleep(delay)
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    t = threading.Thread(target=_kill, daemon=True)
    t.start()


# ── Startup recovery ───────────────────────────────────────────

def recover_on_startup() -> None:
    """Mark orphaned ``running`` rows as failed and clear stale lock files."""
    _init_db()
    now = time.time()
    with _db_lock, _connect() as conn:
        rows = conn.execute("SELECT id, pid FROM jobs WHERE status='running'").fetchall()
        for row in rows:
            pid = row["pid"]
            alive = False
            if pid:
                try:
                    os.kill(pid, 0)
                    alive = True
                except ProcessLookupError:
                    alive = False
                except PermissionError:
                    alive = True  # can't tell, assume alive
            if not alive:
                conn.execute(
                    "UPDATE jobs SET status='failed', error=?, ended_at=? WHERE id=?",
                    ("api_restart", now, row["id"]),
                )
    queue_lock.clear_stale()


# ── Dispatcher ─────────────────────────────────────────────────

_dispatcher_wake: asyncio.Event | None = None
_dispatcher_loop: asyncio.AbstractEventLoop | None = None


def _notify_dispatcher() -> None:
    loop = _dispatcher_loop
    ev = _dispatcher_wake
    if loop is not None and ev is not None:
        loop.call_soon_threadsafe(ev.set)


async def reaper_loop(interval: float = 5.0) -> None:
    """Finalize running rows whose process group is dead.

    Acts as a safety net when the dispatcher's proc.wait() can't reap the
    subprocess for any reason (external kill, stuck pipe, orphaned child).
    Runs forever; cheap — one SQLite scan every few seconds.
    """
    while True:
        try:
            _reap_dead_running()
        except Exception:
            pass
        await asyncio.sleep(interval)


def _reap_dead_running() -> None:
    with _db_lock, _connect() as conn:
        rows = conn.execute(
            "SELECT id, pid, pgid FROM jobs WHERE status='running'"
        ).fetchall()
    now = time.time()
    for row in rows:
        if _pid_dead(row["pid"]) and _pgid_dead(row["pgid"]):
            with _db_lock, _connect() as conn:
                conn.execute(
                    "UPDATE jobs SET status='cancelled', ended_at=?, "
                    "error=COALESCE(error,'') || ? WHERE id=? AND status='running'",
                    (now, "reaped: pid gone; ", row["id"]),
                )


async def dispatcher_loop() -> None:
    """Run forever: pick up queued jobs one at a time and execute them."""
    global _dispatcher_wake, _dispatcher_loop
    _dispatcher_loop = asyncio.get_running_loop()
    _dispatcher_wake = asyncio.Event()

    while True:
        job = _next_queued()
        if job is None:
            _dispatcher_wake.clear()
            try:
                await asyncio.wait_for(_dispatcher_wake.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            continue

        try:
            await _run_job(job)
        except Exception as e:
            _finalize(job["id"], status="failed", exit_code=None, error=f"dispatcher: {e}")


def _next_queued() -> dict | None:
    # Skip if something is already running in the DB.
    with _db_lock, _connect() as conn:
        running = conn.execute(
            "SELECT 1 FROM jobs WHERE status='running' LIMIT 1"
        ).fetchone()
        if running:
            return None

    # Also skip while a CLI-launched job holds the queue lock. If we picked
    # a queued row now we'd flip it to 'running' immediately while its
    # subprocess actually waits behind the CLI — the UI would show the row
    # as active when it's really still queued.
    holder = queue_lock.read_holder()
    if holder is not None:
        return None

    with _db_lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
    return _row_to_dict(row) if row else None


async def _run_job(job: dict) -> None:
    job_id = job["id"]
    command = job["command"]
    body = job.get("body") or {}

    loop = asyncio.get_running_loop()

    # The Go subprocess we spawn will itself acquire the queue flock — we
    # don't grab it here, because that would deadlock the child. We rely on
    # the DB's status='running' row to serialize the dispatcher loop.
    try:
        with _db_lock, _connect() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["status"] != "queued":
                return
            conn.execute(
                "UPDATE jobs SET status='running', started_at=? WHERE id=?",
                (time.time(), job_id),
            )

        argv = _build_argv(command, body)
        log_path = _logs_dir() / f"{job_id}.log"

        env = os.environ.copy()
        # The dispatcher-owned subprocess must never enqueue itself back into
        # the API if the lock is briefly busy (e.g. during a cancel race);
        # force it into wait-and-acquire mode so it just runs.
        env["IMPRINT_QUEUE_FOREGROUND"] = "1"

        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
            env=env,
        )
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            pgid = proc.pid

        with _db_lock, _connect() as conn:
            conn.execute(
                "UPDATE jobs SET pid=?, pgid=? WHERE id=?",
                (proc.pid, pgid, job_id),
            )

        def _pump():
            with open(log_path, "w", encoding="utf-8") as logf:
                assert proc.stdout is not None
                for line in proc.stdout:
                    clean = _strip_ansi(line)
                    logf.write(clean)
                    logf.flush()
                    _append_output(job_id, clean, loop)

        await loop.run_in_executor(None, _pump)
        exit_code = await loop.run_in_executor(None, proc.wait)

        # Determine final status: if the DB row was flagged cancelled during
        # run, honour that; otherwise map by exit code.
        with _db_lock, _connect() as conn:
            err_row = conn.execute("SELECT error FROM jobs WHERE id=?", (job_id,)).fetchone()
            was_cancel_flag = bool(err_row and err_row["error"] and "cancelled" in err_row["error"])

        if was_cancel_flag or exit_code in (-signal.SIGTERM, -signal.SIGKILL, 130, 143, 137):
            status = "cancelled"
        elif exit_code == 0:
            status = "done"
        else:
            status = "failed"

        _finalize(job_id, status=status, exit_code=exit_code, error=None)

        # Reset in-process caches after commands that mutate collections.
        if status == "done" and command in ("wipe", "migrate", "workspace", "retag"):
            try:
                from .api import _reset_after_wipe  # lazy import
                _reset_after_wipe()
            except Exception:
                pass
    finally:
        _mark_done(job_id, loop)


def _finalize(job_id: str, *, status: str, exit_code: int | None, error: str | None) -> None:
    now = time.time()
    with _db_lock, _connect() as conn:
        if error:
            conn.execute(
                "UPDATE jobs SET status=?, exit_code=?, error=COALESCE(error,'') || ?, ended_at=? "
                "WHERE id=?",
                (status, exit_code, error, now, job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET status=?, exit_code=?, ended_at=? WHERE id=?",
                (status, exit_code, now, job_id),
            )


# ── Output streaming ───────────────────────────────────────────

async def tail_output(job_id: str) -> AsyncIterator[str]:
    """Yield all buffered output for ``job_id`` followed by live lines.

    Replays the on-disk log first (so late subscribers see history), then
    attaches to the live buffer until the job finalizes.
    """
    log_path = _logs_dir() / f"{job_id}.log"
    # Replay stored log first.
    if log_path.exists():
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    yield line
        except OSError:
            pass

    with _BUFFER_LOCK:
        ev = _BUFFER_EVENTS.get(job_id)
        if ev is None:
            ev = asyncio.Event()
            _BUFFER_EVENTS[job_id] = ev
        cursor = len(_BUFFERS.get(job_id, []))

    while True:
        await ev.wait()
        with _BUFFER_LOCK:
            buf = _BUFFERS.get(job_id, deque())
            lines = list(buf)[cursor:]
            cursor = len(buf)
            done = job_id in _BUFFER_DONE
            ev.clear()
        for line in lines:
            yield line
        if done:
            # One final flush of anything written after the done flag set.
            job = get_job(job_id)
            if job and job.get("status") in ("done", "failed", "cancelled"):
                return


# ── Argv builders ──────────────────────────────────────────────

def _find_imprint_binary() -> str:
    found = shutil.which("imprint")
    if found:
        return found
    import platform as plat
    system = plat.system().lower()
    machine = plat.machine().lower()
    arch = "amd64" if machine in ("x86_64", "amd64") else "arm64"
    bin_name = f"imprint-{system}-{arch}"
    bin_path = Path(__file__).parent.parent / "bin" / bin_name
    if bin_path.exists():
        return str(bin_path)
    return "imprint"


def _build_argv(command: str, body: dict) -> list[str]:
    imprint_bin = _find_imprint_binary()
    argv = [imprint_bin, command]
    argv.extend(build_command_args(command, body))
    return argv


def build_command_args(command: str, body: dict) -> list[str]:
    """Translate a JSON body into CLI flags for the given command.

    If ``body["args"]`` is a list it's used verbatim (the CLI-enqueue path
    passes its own effective argv this way). Otherwise we fall back to the
    per-command structured keys the UI uses.
    """
    passthrough = body.get("args") if isinstance(body, dict) else None
    if isinstance(passthrough, list):
        return [str(x) for x in passthrough]

    args: list[str] = []
    if command == "ingest":
        if body.get("dir"):
            args.append(body["dir"])
    elif command == "ingest-url":
        url = body.get("url", "")
        if url:
            args.append(url)
        if body.get("project"):
            args.extend(["--project", body["project"]])
        if body.get("force"):
            args.append("--force")
    elif command == "refresh":
        if body.get("dir"):
            args.append(body["dir"])
    elif command == "retag":
        if body.get("project"):
            args.extend(["--project", body["project"]])
        if body.get("dry_run"):
            args.append("--dry-run")
        if body.get("all"):
            args.append("--all")
    elif command == "config":
        action = body.get("action", "")
        if action:
            args.append(action)
        if body.get("key"):
            args.append(body["key"])
        if body.get("value") is not None:
            args.append(str(body["value"]))
    elif command == "wipe":
        if body.get("force"):
            args.append("--force")
        if body.get("all"):
            args.append("--all")
    elif command == "sync":
        action = body.get("action", "")
        if action:
            args.append(action)
    elif command == "migrate":
        if body.get("from"):
            args.extend(["--from", body["from"]])
        if body.get("to"):
            args.extend(["--to", body["to"]])
        if body.get("project"):
            args.extend(["--project", body["project"]])
        if body.get("topic"):
            args.extend(["--topic", body["topic"]])
        if body.get("source"):
            args.extend(["--source", body["source"]])
        if body.get("dry_run"):
            args.append("--dry-run")
    elif command == "workspace":
        action = body.get("action", "")
        if action:
            args.append(action)
        if body.get("name"):
            args.append(body["name"])
    return args
