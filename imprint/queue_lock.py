"""Cross-process lock file for the command queue.

Serializes ingest/refresh/retag/ingest-url across the Go CLI and the FastAPI
dispatcher. Both processes acquire an advisory `fcntl.flock` on
`{DATA_DIR}/queue.lock` before spawning a job. The lock file body is JSON
describing the current holder so the CLI can print a useful error when
another process already holds it.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import time
from pathlib import Path


def lock_path() -> Path:
    from .config import get_data_dir
    return get_data_dir() / "queue.lock"


def acquire(command: str, job_id: str, block: bool = False) -> int | None:
    """Acquire the queue lock.

    Returns a file descriptor on success (caller must eventually pass it to
    release()). Returns None when non-blocking and the lock is held.
    """
    p = lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_RDWR | os.O_CREAT, 0o644)
    flags = fcntl.LOCK_EX if block else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        fcntl.flock(fd, flags)
    except OSError as e:
        if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            os.close(fd)
            return None
        os.close(fd)
        raise
    payload = json.dumps({
        "pid": os.getpid(),
        "job_id": job_id,
        "command": command,
        "started_at": time.time(),
    })
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, payload.encode("utf-8"))
    os.fsync(fd)
    return fd


def release(fd: int | None) -> None:
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def read_holder() -> dict | None:
    """Best-effort read of the lock file body.

    Returns None if the file is missing, empty, unreadable, or the holder
    PID is dead (in which case the file is considered stale).
    """
    p = lock_path()
    try:
        text = p.read_text().strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    pid = data.get("pid")
    if isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass
    return data


def clear_stale() -> None:
    """Remove the lock file if its recorded PID is gone.

    Safe to call on startup before the dispatcher takes over. Does not
    attempt to unlink while the lock is held — if flock acquires in
    non-blocking mode succeeds we simply release immediately.
    """
    holder = read_holder()
    if holder is not None:
        return
    p = lock_path()
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass
