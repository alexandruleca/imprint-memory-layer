"""Command queue + dispatcher for ingest/refresh/retag/ingest-url.

Serializes jobs so only one heavy operation runs at a time (embeddings +
LLM tagging easily OOM a workstation when run in parallel). Jobs are
persisted to ``data/queue.sqlite3`` so the queue survives API restarts,
and a shared ``data/queue.lock`` file blocks direct CLI invocations while
a job is active.

Submitting a job identical to one still waiting returns that job instead of
queueing another (ADR 0068). When nothing waits, the dispatcher runs the
background tagging job, which drains the LLM tagging backlog and steps
aside for every other job (ADR 0067).

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
from contextlib import contextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

from . import queue_lock, tag_backlog
from .processes import is_alive

# The background tagging job's command (ADR 0067). The dispatcher starts it
# itself; no client can submit it.
BACKGROUND = tag_backlog.COMMAND

# SIGKILL doesn't exist on Windows; fall back to SIGTERM for the force-kill
# escalation path (os.kill(pid, SIGTERM) terminates unconditionally on Windows).
_SIGKILL: int = getattr(signal, "SIGKILL", signal.SIGTERM)

# ── Platform-safe process helpers ──────────────────────────────


def _get_pgid(pid: int) -> int | None:
    """Return the process group id of pid, or None on Windows / on error."""
    if sys.platform == "win32":
        return None
    try:
        return os.getpgid(pid)
    except (ProcessLookupError, OSError):
        return None


def _killpg_or_pid(pgid: int | None, pid: int | None, sig: int) -> None:
    """Send sig to the process group on Unix; fall back to pid on Windows."""
    if sys.platform != "win32" and pgid:
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, OSError):
            pass
    elif pid:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, OSError):
            pass


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


@contextmanager
def _connect():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        with conn:
            yield conn
    finally:
        # sqlite3's transaction context manager does not close the handle.
        conn.close()


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

class RootNotApproved(ValueError):
    """An ingest or refresh the workspace's roots would refuse (ADR 0015).

    Raised at enqueue time instead of letting the job fail when it runs, so a
    client can offer to approve ``root`` and submit the job again.
    """

    def __init__(self, workspace: str, root: str, overlaps: list[str]):
        super().__init__(f'{root} is not an approved root of workspace {workspace!r}. '
                         'Approve it with imprint workspace roots add <directory>.')
        self.workspace, self.root, self.overlaps = workspace, root, overlaps


def enqueue(command: str, body: dict | None) -> str:
    """Queue a job and return its ID, which is an identical waiting job's
    when there is one (see ``submit``)."""
    return submit(command, body)["job_id"]


def submit(command: str, body: dict | None) -> dict:
    """Queue a job, or merge it into an identical job that is still waiting.

    Returns ``{"job_id", "merged"}``. Identical means the same command, the
    same workspace and the same arguments once normalized (``dedupe_key``).
    Only waiting jobs absorb a submission: one identical to the running job
    queues a single follow-up, because the running job may have read its
    inputs before whatever prompted the new request (ADR 0068).
    """
    _init_db()
    # Capture the workspace now, rather than when this job eventually runs.
    from .workspace_policy import unapproved_root, workspace_name
    workspace = workspace_name((body or {}).get('workspace'))
    # The dashboard names the path in `dir` (see build_command_args). Argv the
    # CLI passes through is checked when the job runs, as a direct call would be.
    if command in ("ingest", "refresh") and (body or {}).get("dir"):
        refused = unapproved_root(body["dir"], workspace)
        if refused:
            raise RootNotApproved(**refused)
    body = {**(body or {}), "_workspace": workspace}
    key = dedupe_key(command, body)
    with _db_lock, _connect() as conn:
        if key is not None:
            for row in conn.execute("SELECT id, body_json FROM jobs WHERE status='queued' AND command=? "
                                    "ORDER BY created_at", (command,)).fetchall():
                waiting = _row_to_dict(row)["body"]
                if dedupe_key(command, waiting) == key:
                    # The Queue page shows how many requests the job answers.
                    waiting["_merged"] = int(waiting.get("_merged") or 0) + 1
                    conn.execute("UPDATE jobs SET body_json=? WHERE id=?", (json.dumps(waiting), row["id"]))
                    return {"job_id": row["id"], "merged": True}
        job_id = _insert_job(conn, command, body)
    _notify_dispatcher()
    return {"job_id": job_id, "merged": False}


def _insert_job(conn, command: str, body: dict) -> str:
    job_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO jobs (id, command, body_json, status, created_at) "
        "VALUES (?, ?, ?, 'queued', ?)",
        (job_id, command, json.dumps(body), time.time()),
    )
    return job_id


# Options that take a value, as the CLI parses them; other options are switches.
_VALUE_FLAGS = frozenset({
    "--batch-size", "--project", "--workspace", "--follow-depth", "--from-file", "--from", "--to",
    "--topic", "--source", "--distribution", "--target", "--interval", "--path",
})
_REPEATABLE_FLAGS = frozenset({"--path"})
_PATH_FLAGS = frozenset({"--path", "--from-file"})
# Commands whose first positional argument is a filesystem path.
_PATH_COMMANDS = frozenset({"ingest", "refresh"})


def _canonical_path(value: str) -> str:
    """An absolute path in one spelling: no trailing separator, no ``.`` or
    ``..`` segments, and the platform's case rules. Symlinks are not resolved
    (two names can index under different project roots), and a relative path
    is left as given: it has no meaning without the submitter's directory."""
    if not os.path.isabs(value):
        return value
    return os.path.normcase(os.path.normpath(value))


def dedupe_key(command: str, body: dict) -> str | None:
    """What makes two submissions the same job, or None when a job is never
    merged (a browser capture's body is its content, not arguments).

    The key is the command, the workspace and the CLI arguments the job would
    run with (``build_command_args``), whether the body named them as
    dashboard fields or passed the CLI's own arguments: positional arguments
    in order, option order ignored, a repeated value option's last value
    kept as the CLI keeps it (``--path`` accumulates), and paths canonical.
    """
    if command == "browser-capture":
        return None
    try:
        args = build_command_args(command, body)
    except (TypeError, ValueError):
        return None
    positional: list[str] = []
    options: dict[str, str] = {}
    repeated: set[tuple[str, str]] = set()
    i = 0
    while i < len(args):
        token = args[i]
        if token.startswith("--"):
            name, has_value, value = token.partition("=")
            if not has_value and name in _VALUE_FLAGS and i + 1 < len(args):
                i += 1
                value = args[i]
            if name in _PATH_FLAGS:
                value = _canonical_path(value)
            if name in _REPEATABLE_FLAGS:
                repeated.add((name, value))
            else:
                options[name] = value
        elif command in _PATH_COMMANDS and not positional:
            positional.append(_canonical_path(token))
        else:
            positional.append(token)
        i += 1
    return json.dumps([command, body.get("_workspace", ""), positional, sorted(options.items()),
                       sorted(repeated)])


def foreground_waiting() -> bool:
    """Whether a job other than background tagging waits to run; the
    background job checks between memories and steps aside (ADR 0067)."""
    _init_db()
    with _db_lock, _connect() as conn:
        return conn.execute("SELECT 1 FROM jobs WHERE status='queued' AND command!=? LIMIT 1",
                            (BACKGROUND,)).fetchone() is not None


def get_job(job_id: str) -> dict | None:
    with _db_lock, _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _row_to_dict(row) if row else None


def active_job() -> dict | None:
    """The running job, other than background tagging."""
    with _db_lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='running' AND command!=? ORDER BY started_at LIMIT 1",
            (BACKGROUND,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def background_job() -> dict | None:
    """The running background tagging run, else the latest one."""
    _init_db()
    with _db_lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE command=? ORDER BY status='running' DESC, created_at DESC LIMIT 1",
            (BACKGROUND,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_queue(recent_limit: int = 20) -> dict:
    """The running, waiting and finished jobs. Background tagging runs are
    reported apart (``background_job``, ``background_state``)."""
    _init_db()
    with _db_lock, _connect() as conn:
        active = conn.execute(
            "SELECT * FROM jobs WHERE status='running' AND command!=? ORDER BY started_at LIMIT 1",
            (BACKGROUND,),
        ).fetchone()
        queued = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' AND command!=? ORDER BY created_at",
            (BACKGROUND,),
        ).fetchall()
        recent = conn.execute(
            "SELECT * FROM jobs WHERE status IN ('done','failed','cancelled') AND command!=? "
            "ORDER BY ended_at DESC LIMIT ?",
            (BACKGROUND, recent_limit),
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

    # Running — signal the process group (Unix) or pid (Windows).
    # The dispatcher observes proc.wait() and transitions the row to
    # cancelled once it reaps.
    _killpg_or_pid(pgid, pid, signal.SIGTERM)
    _schedule_sigkill(pgid, pid)

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
    return not is_alive(pid)


def _pgid_dead(pgid: int | None) -> bool:
    if not pgid or sys.platform == "win32":
        return True
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _schedule_sigkill(pgid: int | None, pid: int | None, delay: float = 3.0) -> None:
    """Escalate to force-kill after ``delay`` seconds if the process is still alive."""
    def _kill():
        time.sleep(delay)
        _killpg_or_pid(pgid, pid, _SIGKILL)

    t = threading.Thread(target=_kill, daemon=True)
    t.start()


# ── Startup recovery ───────────────────────────────────────────

def recover_on_startup() -> None:
    """Mark orphaned ``running`` rows as failed and clear stale lock files.

    A background tagging run the previous server left behind is stopped and
    recorded as yielded: it would otherwise hold the queue until its backlog
    was empty. This server starts it again when the queue is idle. It is
    stopped the way any lock taker stops it, through the lock, which only a
    live holder can hold; the PID in its row may belong to another process
    by now (after a reboot, for one).
    """
    _init_db()
    now = time.time()
    with _db_lock, _connect() as conn:
        rows = conn.execute("SELECT id, pid, command FROM jobs WHERE status='running'").fetchall()
    if any(row["command"] == BACKGROUND for row in rows):
        queue_lock.release(queue_lock.acquire("recover", "", block=False))
    with _db_lock, _connect() as conn:
        for row in rows:
            pid = row["pid"]
            alive = is_alive(pid)
            if row["command"] == BACKGROUND:
                conn.execute(
                    "UPDATE jobs SET status='yielded', error=?, ended_at=? WHERE id=?",
                    ("api_restart", now, row["id"]),
                )
            elif not alive:
                conn.execute(
                    "UPDATE jobs SET status='failed', error=?, ended_at=? WHERE id=?",
                    ("api_restart", now, row["id"]),
                )
        # A run inserted but never started; the dispatcher inserts its own.
        conn.execute("UPDATE jobs SET status='cancelled', error=?, ended_at=? WHERE status='queued' AND command=?",
                     ("api_restart", now, BACKGROUND))
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


async def dispatcher_loop(after_job=None) -> None:
    """Run forever: pick up queued jobs one at a time and execute them, and
    background tagging when none waits.
    ``after_job(command, status)`` runs in the server once each job ends."""
    global _dispatcher_wake, _dispatcher_loop
    _dispatcher_loop = asyncio.get_running_loop()
    _dispatcher_wake = asyncio.Event()
    terminal_job = False

    while True:
        job = _next_queued()
        if job is not None:
            await _run_logged(job, after_job)
            _background["due"] = 0.0  # it may have stored memories to tag
            continue

        # A job started in a terminal may have stored memories as well.
        holder = queue_lock.read_holder()
        if holder is not None and not holder.get("preemptible"):
            terminal_job = True
        elif terminal_job:
            terminal_job = False
            _background["due"] = 0.0

        if await _run_background_if_due(after_job):
            continue

        _dispatcher_wake.clear()
        try:
            await asyncio.wait_for(_dispatcher_wake.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass


async def _run_logged(job: dict, after_job) -> None:
    try:
        await _run_job(job, after_job)
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
    # as active when it's really still queued. A preemptible holder (a
    # background tagging run left by an earlier server) does not count: the
    # job's own lock acquisition stops it.
    holder = queue_lock.read_holder()
    if holder is not None and not holder.get("preemptible"):
        return None

    with _db_lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' AND command!=? ORDER BY created_at LIMIT 1",
            (BACKGROUND,),
        ).fetchone()
    return _row_to_dict(row) if row else None


# ── Background tagging (ADR 0067) ──────────────────────────────

# When the dispatcher next looks at the backlog, and what holds it back:
# after a cancel, the backlog size it was cancelled at (it resumes once that
# changes); after a failure, a time.
_background: dict[str, Any] = {"due": 0.0, "hold_count": None, "hold_until": 0.0}
BACKLOG_POLL_SECONDS = 30.0
FAILURE_PAUSE_SECONDS = 1800.0
_KEEP_BACKGROUND_RUNS = 20


def backlog_changed() -> None:
    """Something that decides whether background tagging runs changed: the
    tagging settings, or the person asked it to resume. Look again now."""
    _background.update(due=0.0, hold_count=None, hold_until=0.0)
    _notify_dispatcher()


def _pending_total() -> int | None:
    try:
        return sum(tag_backlog.pending_by_workspace().values())
    except Exception:
        return None


def _held(pending: int) -> bool:
    if time.time() < _background["hold_until"]:
        return True
    return _background["hold_count"] is not None and pending == _background["hold_count"]


def _startable_backlog() -> int | None:
    """The backlog size when background tagging should start now, else None:
    LLM tagging is on, no job runs or waits, no other process holds the lock,
    nothing holds it back, and memories wait."""
    try:
        if not tag_backlog.deferring():
            return None
    except Exception:
        return None
    _init_db()
    with _db_lock, _connect() as conn:
        if conn.execute("SELECT 1 FROM jobs WHERE status='running' OR (status='queued' AND command!=?) LIMIT 1",
                        (BACKGROUND,)).fetchone():
            return None
    if queue_lock.read_holder() is not None:
        return None
    pending = _pending_total()
    if not pending or _held(pending):
        return None
    _background["hold_count"] = None
    return pending


async def _run_background_if_due(after_job) -> bool:
    """Run background tagging to its end when it is due and startable.
    Returns whether it ran."""
    now = time.time()
    if now < _background["due"]:
        return False
    _background["due"] = now + BACKLOG_POLL_SECONDS
    loop = asyncio.get_running_loop()
    if await loop.run_in_executor(None, _startable_backlog) is None:
        return False
    with _db_lock, _connect() as conn:
        job_id = _insert_job(conn, BACKGROUND, {})
    _prune_background()
    job = get_job(job_id)
    await _run_logged(job, after_job)
    # Not again before the next poll: after a yield, the queued job it yielded
    # to runs next and makes it due when it ends, and a process that stopped
    # it to take the lock is still waiting for that lock; starting again at
    # once would race that process for it.
    _background["due"] = time.time() + BACKLOG_POLL_SECONDS
    status = (get_job(job_id) or {}).get("status")
    if status == "cancelled":
        _background["hold_count"] = await loop.run_in_executor(None, _pending_total)
    elif status == "failed":
        _background["hold_until"] = time.time() + FAILURE_PAUSE_SECONDS
    return True


def _background_status(exit_code: int | None, cancelled_by_user: bool) -> str:
    """How a background tagging run ended. It yields by exiting with
    ``tag_backlog.YIELDED`` when a job is queued, or is stopped by another
    lock taker: SIGTERM on Unix, exit code 15 on Windows (ADR 0067)."""
    if cancelled_by_user:
        return "cancelled"
    if exit_code == 0:
        return "done"
    if exit_code in (tag_backlog.YIELDED, -signal.SIGTERM, 128 + signal.SIGTERM, 15):
        return "yielded"
    return "failed"


def _prune_background(keep: int = _KEEP_BACKGROUND_RUNS) -> None:
    """Keep the latest background runs and their logs; it starts often."""
    with _db_lock, _connect() as conn:
        old = [row["id"] for row in conn.execute(
            "SELECT id FROM jobs WHERE command=? AND status NOT IN ('queued','running') "
            "ORDER BY created_at DESC LIMIT -1 OFFSET ?", (BACKGROUND, keep))]
        conn.executemany("DELETE FROM jobs WHERE id=?", [(job_id,) for job_id in old])
    for job_id in old:
        with contextlib.suppress(OSError):
            (_logs_dir() / f"{job_id}.log").unlink()


_pending_cache: dict[str, Any] = {"at": 0.0, "value": None}


def background_state() -> dict:
    """What the Queue page shows of background tagging: whether LLM tagging
    is on, how many memories wait across workspaces, and its state."""
    enabled = bool(tag_backlog.deferring())
    now = time.time()
    if now - _pending_cache["at"] > 2.0:
        _pending_cache.update(at=now, value=_pending_total())
    pending = _pending_cache["value"]
    job = background_job()
    resume_at = None
    if not enabled:
        state = "off"
    elif job and job["status"] == "running":
        state = "running"
    elif not pending:
        state = "idle"
    elif now < _background["hold_until"]:
        state, resume_at = "failed", _background["hold_until"]
    elif _background["hold_count"] is not None and pending == _background["hold_count"]:
        state = "paused"
    elif job and job["status"] == "yielded":
        state = "yielded"
    else:
        state = "waiting"
    return {"enabled": enabled, "pending": pending, "state": state, "job": job, "resume_at": resume_at}


async def _run_job(job: dict, after_job=None) -> None:
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

        argv = (_build_argv(command, body, job_id=job_id) if command in ('browser-capture', BACKGROUND)
                else _build_argv(command, body))
        log_path = _logs_dir() / f"{job_id}.log"

        env = os.environ.copy()
        # The dispatcher-owned subprocess must never enqueue itself back into
        # the API if the lock is briefly busy (e.g. during a cancel race);
        # force it into wait-and-acquire mode so it just runs.
        env["IMPRINT_QUEUE_FOREGROUND"] = "1"
        if body.get("_workspace"):
            env["IMPRINT_WORKSPACE"] = body["_workspace"]
        # The job's output is UTF-8 end to end: the Go CLI writes UTF-8, and
        # its Python children run in UTF-8 mode. Decoding with the platform's
        # locale (cp1252 on Windows) failed on the first "═" of a header.
        env["PYTHONUTF8"] = "1"
        # The log shows lines as they are printed. Writing to a pipe, Python
        # holds its stdout back in blocks, so a scan's first lines appeared
        # only after minutes of work.
        env["PYTHONUNBUFFERED"] = "1"

        # A job has no terminal: a prompt reads end-of-file instead of
        # waiting for an answer nobody can give.
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=True,
            env=env,
        )
        pgid = _get_pgid(proc.pid)

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

        if command == BACKGROUND:
            status = _background_status(exit_code, was_cancel_flag)
        elif was_cancel_flag or exit_code in (-signal.SIGTERM, -_SIGKILL, 130, 143, 137):
            status = "cancelled"
        elif exit_code == 0:
            status = "done"
        else:
            status = "failed"

        _finalize(job_id, status=status, exit_code=exit_code, error=None)

        # The server running this dispatcher refreshes what the job changed.
        # It hands in the callback: `python -m imprint.api` serves from
        # __main__, so importing imprint.api here would reach a second copy.
        if after_job is not None:
            try:
                after_job(command, status)
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
            if job and job.get("status") in ("done", "failed", "cancelled", "yielded"):
                return


# ── Argv builders ──────────────────────────────────────────────

INSTALL_ROOT = Path(__file__).resolve().parent.parent


def find_imprint_binary(root: Path = INSTALL_ROOT) -> str:
    """The CLI that belongs to this installation, else one on PATH.

    A release keeps it at ``bin/imprint[.exe]`` beside this package and a
    checkout's ``make all`` builds ``bin/imprint-<os>-<arch>[.exe]``. PATH
    comes last: the Windows installer leaves PATH alone by default, and a
    PATH hit may belong to a different install.
    """
    import platform as plat
    ext = ".exe" if os.name == "nt" else ""
    system = plat.system().lower()
    arch = "amd64" if plat.machine().lower() in ("x86_64", "amd64") else "arm64"
    for name in (f"imprint{ext}", f"imprint-{system}-{arch}{ext}"):
        candidate = root / "bin" / name
        if candidate.is_file():
            return str(candidate)
    return shutil.which("imprint") or "imprint"


def _build_argv(command: str, body: dict, *, job_id: str | None = None) -> list[str]:
    if command == 'associate-projects':
        return [sys.executable, '-m', 'imprint.project_associations', *build_command_args(command, body)]
    if command in ('browser-capture', BACKGROUND):
        if not job_id or not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise ValueError(f'{command} requires a queue job ID')
        module = 'imprint.browser_worker' if command == 'browser-capture' else 'imprint.tag_backlog'
        return [sys.executable, '-m', module, job_id]
    imprint_bin = find_imprint_binary()
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
    if command == 'associate-projects':
        if body.get('model'):
            args.append('--model')
    elif command in ("ingest", "refresh"):
        if body.get("dir"):
            args.append(body["dir"])
        if body.get("force"):
            args.append("--force")
        if body.get("batch_size") is not None:
            size = int(body["batch_size"])
            if size < 1:
                raise ValueError("batch_size must be positive")
            args.extend(["--batch-size", str(size)])
    elif command == "ingest-url":
        url = body.get("url", "")
        if url:
            args.append(url)
        if body.get("project"):
            args.extend(["--project", body["project"]])
        if body.get("force"):
            args.append("--force")
        if body.get("follow"):
            args.append("--follow")
        if body.get("no_follow"):
            args.append("--no-follow")
        if body.get("follow_depth") is not None:
            args.extend(["--follow-depth", str(int(body["follow_depth"]))])
    elif command == "learn":
        if body.get("desktop"):
            args.append("--desktop")
    elif command in ("connect", "disconnect"):
        if body.get("target"):
            args.append(body["target"])
    elif command == "doctor":
        if body.get("probe_mcp"):
            args.append("--probe-mcp")
    elif command == "scrub":
        if body.get("apply"):
            args.append("--apply")
    elif command == "retag":
        if body.get("project"):
            args.extend(["--project", body["project"]])
        if body.get("dry_run"):
            args.append("--dry-run")
        if body.get("all"):
            args.append("--all")
        if body.get("pending"):
            args.append("--pending")
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
        if body.get("copy"):
            args.append("--copy")
        if body.get("dry_run"):
            args.append("--dry-run")
    elif command == "workspace":
        action = body.get("action", "")
        if action:
            args.append(action)
        if body.get("name"):
            args.append(body["name"])
    elif command == "wsl":
        # setup or remove one distribution's default-user profile of a client
        if body.get("action"):
            args.append(body["action"])
        if body.get("distribution"):
            args.extend(["--distribution", body["distribution"]])
        if body.get("target"):
            args.extend(["--target", body["target"]])
    return args
