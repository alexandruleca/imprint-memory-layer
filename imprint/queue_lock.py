"""Cross-process lock file for the command queue.

Serializes ingest/refresh/retag/ingest-url across the Go CLI and the FastAPI
dispatcher. Both processes acquire an advisory lock on
`{DATA_DIR}/queue.lock` before spawning a job. The lock file body is JSON
describing the current holder so the CLI can print a useful error when
another process already holds it.

Locking backend: fcntl.flock on Unix, msvcrt.locking on Windows.
"""

from __future__ import annotations

import errno
import json
import os
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    import msvcrt

    def _lock(fd: int, block: bool) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        mode = msvcrt.LK_LOCK if block else msvcrt.LK_NBLCK
        try:
            msvcrt.locking(fd, mode, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl

    def _lock(fd: int, block: bool) -> bool:
        flags = fcntl.LOCK_EX if block else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(fd, flags)
        except OSError as e:
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                return False
            raise
        return True

    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


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
    if not _lock(fd, block):
        os.close(fd)
        return None
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
    _unlock(fd)
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
    attempt to unlink while the lock is held — if the non-blocking acquire
    succeeds we simply release immediately.
    """
    holder = read_holder()
    if holder is not None:
        return
    p = lock_path()
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass
