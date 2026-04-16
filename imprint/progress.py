"""Progress file protocol for ingestion tracking.

Writes a JSON progress file to the data directory so the API can report
active ingestion/refresh jobs to the dashboard.  Atomic writes via
tempfile + os.replace prevent partial reads.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


def progress_path() -> Path:
    """Return path to the progress file: {DATA_DIR}/ingest_progress.json."""
    from .config import get_data_dir
    return get_data_dir() / "ingest_progress.json"


def write_progress(
    command: str,
    processed: int,
    total: int,
    stored: int,
    skipped: int,
    started_at: float,
    projects: list[str],
) -> None:
    """Atomically write progress JSON.  Caller should throttle to ~1 call/sec."""
    data = {
        "pid": os.getpid(),
        "command": command,
        "processed": processed,
        "total": total,
        "stored": stored,
        "skipped": skipped,
        "started_at": started_at,
        "updated_at": time.time(),
        "projects": projects,
    }
    p = progress_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def clear_progress() -> None:
    """Remove the progress file."""
    try:
        progress_path().unlink(missing_ok=True)
    except OSError:
        pass


def read_progress() -> dict | None:
    """Read progress file.  Returns None if missing, unreadable, or PID dead."""
    p = progress_path()
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    pid = data.get("pid")
    if pid:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            # Process gone — stale file
            clear_progress()
            return None
        except PermissionError:
            pass  # Process exists but owned by another user — still valid

    return data
