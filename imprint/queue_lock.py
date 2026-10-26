"""Cross-process lock file for the command queue.

Serializes ingest/refresh/retag/ingest-url across the Go CLI and the FastAPI
dispatcher. Both processes acquire an advisory lock on
`{DATA_DIR}/queue.lock` before spawning a job. The lock file body is JSON
describing the current holder so the CLI can print a useful error when
another process already holds it.

A holder can declare itself preemptible: the background tagging job does
(ADR 0067). Any other process that wants the lock stops a preemptible holder
and takes the lock instead of waiting behind it or refusing; the holder loses
at most the memory it was tagging. ``internal/queuelock`` does the same.

``try_lock`` and ``unlock`` lock other files with the same backend.

Locking backend: fcntl.flock on Unix, msvcrt.locking on Windows. On Windows
the locked byte sits past the holder's metadata (``_WINDOWS_LOCK_OFFSET``):
Windows locks are mandatory, and a lock on byte 0 would stop every other
process from reading who holds the lock. Go's lock covers the whole file, so
the two still exclude each other.
"""

from __future__ import annotations

import errno
import json
import os
import signal
import sys
import time
from pathlib import Path

from .processes import is_alive

# How long a taker waits for a stopped preemptible holder to release the lock,
# and when it escalates from SIGTERM to SIGKILL (Unix; Windows terminates at once).
PREEMPT_WAIT = 6.0
PREEMPT_KILL_AFTER = 3.0

if sys.platform == "win32":
    import msvcrt

    # Past any holder metadata, below 2 GiB for the C runtime's 32-bit offsets.
    _WINDOWS_LOCK_OFFSET = 1 << 30

    def _lock(fd: int, block: bool) -> bool:
        os.lseek(fd, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
        mode = msvcrt.LK_LOCK if block else msvcrt.LK_NBLCK
        try:
            msvcrt.locking(fd, mode, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
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


def acquire(command: str, job_id: str, block: bool = False, preemptible: bool = False) -> int | None:
    """Acquire the queue lock.

    Returns a file descriptor on success (caller must eventually pass it to
    release()). Returns None when non-blocking and the lock is held.

    A caller that is not itself ``preemptible`` takes the lock from a
    preemptible holder (see ``preempt``). A ``preemptible`` caller says so in
    the lock body, so other takers may stop it.
    """
    p = lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_RDWR | os.O_CREAT, 0o644)
    acquired = _lock(fd, False)
    if not acquired and not preemptible and preempt():
        acquired = _wait_for_lock(fd)
    if not acquired and block:
        acquired = _lock(fd, True)
    if not acquired:
        os.close(fd)
        return None
    holder = {
        "pid": os.getpid(),
        "job_id": job_id,
        "command": command,
        "started_at": time.time(),
    }
    if preemptible:
        holder["preemptible"] = True
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, json.dumps(holder).encode("utf-8"))
    os.fsync(fd)
    return fd


def preempt() -> bool:
    """Stop the current holder when it declared itself preemptible.

    Returns True when a preemptible holder was signalled; the caller then
    waits for the lock. Only a live holder whose metadata says
    ``preemptible`` is ever signalled.
    """
    holder = read_holder()
    if not holder or not holder.get("preemptible"):
        return False
    pid = holder.get("pid")
    if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return False
    _signal(pid, signal.SIGTERM)
    return True


def _signal(pid: int, sig: int) -> None:
    # On Windows os.kill terminates the process with exit code `sig`.
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _wait_for_lock(fd: int) -> bool:
    """Poll for the lock while a preempted holder exits; escalate once."""
    holder = read_holder()
    pid = holder.get("pid") if holder else None
    deadline = time.monotonic() + PREEMPT_WAIT
    escalate = time.monotonic() + PREEMPT_KILL_AFTER
    while time.monotonic() < deadline:
        if _lock(fd, False):
            return True
        if pid and time.monotonic() >= escalate:
            _signal(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            pid = None
        time.sleep(0.05)
    return False


def release(fd: int | None) -> None:
    """Clear the holder's metadata, then unlock. A holder that keeps running
    after it releases (the dashboard after a purge) would otherwise still be
    named as holding the lock."""
    if fd is None:
        return
    try:
        os.ftruncate(fd, 0)
    except OSError:
        pass
    _unlock(fd)
    try:
        os.close(fd)
    except OSError:
        pass


def try_lock(path: Path) -> int | None:
    """Lock another file the way the queue lock is locked, without waiting.

    Returns the descriptor, or None while another process holds the lock. The
    Stop hook's learners hold one per transcript (``session_learn``).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    if _lock(fd, False):
        return fd
    os.close(fd)
    return None


def unlock(fd: int, path: Path) -> None:
    """Release a ``try_lock`` lock and remove its file.

    A process that opened the file before it is removed can still lock it
    while another locks a new file at the path, so two processes may hold
    it at once: use this only where that costs no more than repeated work.
    """
    _unlock(fd)
    os.close(fd)
    try:
        path.unlink()
    except OSError:
        pass  # Windows keeps a file another process has open.


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
    if isinstance(pid, int) and pid > 0 and not is_alive(pid):
        return None
    return data


def clear_stale() -> None:
    """Clear stale metadata only while holding the original lock inode."""
    holder = read_holder()
    if holder is not None:
        return
    p = lock_path()
    try:
        fd = os.open(str(p), os.O_RDWR)
    except FileNotFoundError:
        return
    try:
        if _lock(fd, False):
            try:
                os.ftruncate(fd, 0)
            finally:
                _unlock(fd)
    finally:
        os.close(fd)
