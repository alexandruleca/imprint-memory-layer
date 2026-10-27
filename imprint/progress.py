"""The running job's progress: the one record every surface reads (ADR 0089).

Heavy work runs one job at a time under the queue lock (ADR 0064), and the
job holding it reports how far it is in ``data/progress.json``: the
dashboard's API, the desktop app while the dashboard server is down, and a
command waiting for the lock all read it. Go setup writes the same record.
Atomic writes via tempfile + os.replace prevent partial reads.

``processed`` of ``total`` counts ``unit`` (files, conversations, chunks,
memories, pages, steps); a total of 0 is a step whose size is unknown.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from .processes import is_alive


def progress_path() -> Path:
    """Return path to the progress file: {DATA_DIR}/progress.json."""
    from .config import get_data_dir
    return get_data_dir() / "progress.json"


def write_progress(
    command: str,
    processed: int,
    total: int,
    stored: int,
    skipped: int,
    started_at: float,
    projects: list[str],
    phase: str = "embedding",
    unit: str = "files",
) -> None:
    """Atomically write progress JSON.  Caller should throttle to ~1 call/sec."""
    data = {
        "pid": os.getpid(),
        "command": command,
        "phase": phase,
        "processed": processed,
        "total": total,
        "unit": unit,
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
    if pid and not is_alive(pid):
        clear_progress()
        return None

    return data
