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


# ── Showing progress (ADR 0089) ─────────────────────────────────────────────

# Phase ids as people read them; others show with underscores as spaces.
PHASES = {"llm_tagging": "tagging", "embedding": "embedding", "reading": "reading"}


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


class Task:
    """One command's progress, the same everywhere: on a terminal one line
    redrawn in place; without one (the dashboard queue's log, a pipe) a line
    when the phase changes, at every tenth of the way and at least every few
    seconds; and, when ``record`` is on, the progress record the dashboard,
    the desktop app and waiting commands read, at most once a second.

    Only a job holding the queue lock may write the record (ADR 0064): pass
    ``record=False`` from a command that runs without it.

        with Task("retag", phase="llm_tagging", unit="memories", total=n) as task:
            for item in items:
                ...
                task.advance(stored=1)
    """

    REDRAW = 0.1      # seconds between terminal redraws
    LOG_EVERY = 5.0   # seconds between log lines without a terminal
    RECORD_EVERY = 1.0

    def __init__(self, command: str, *, phase: str = "", unit: str = "items", total: int = 0,
                 projects: list[str] | tuple[str, ...] = (), record: bool = True, stream=None):
        import sys
        self.command, self.phase, self.unit, self.total = command, phase, unit, total
        self.projects = list(projects)
        self.processed = self.stored = self.skipped = 0
        self.detail = ""
        self.record = record
        self.stream = stream if stream is not None else sys.stderr
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.started = time.time()
        self._drawn = self._logged = self._recorded = 0.0
        self._decile = -1
        self._line = False   # a redrawn line is on screen
        self._logged_phase = None
        self.update(force=True)

    # ── Reporting ───────────────────────────────────────────────────────
    def update(self, processed: int | None = None, *, total: int | None = None, phase: str | None = None,
               unit: str | None = None, stored: int | None = None, skipped: int | None = None,
               detail: str | None = None, force: bool = False) -> None:
        """Set where the work is; any argument left out keeps its value."""
        changed_phase = phase is not None and phase != self.phase
        if changed_phase:
            self._decile = -1  # each phase logs its own tenths
        for name, value in (("processed", processed), ("total", total), ("phase", phase), ("unit", unit),
                            ("stored", stored), ("skipped", skipped), ("detail", detail)):
            if value is not None:
                setattr(self, name, value)
        self._show(force or changed_phase)

    def advance(self, n: int = 1, *, stored: int = 0, skipped: int = 0, detail: str | None = None) -> None:
        """Count ``n`` more done, of which ``stored`` stored and ``skipped`` skipped."""
        self.update(self.processed + n, stored=self.stored + stored, skipped=self.skipped + skipped, detail=detail)

    def note(self, text: str) -> None:
        """Print a line of its own above the progress line."""
        self._clear()
        print(text, file=self.stream, flush=True)
        self._show(True)

    def finish(self, summary: str = "") -> None:
        """End the line with how long the work took, and drop the record."""
        self._clear()
        took = _duration(time.time() - self.started)
        counted = f"{self.processed:,}" + (f"/{self.total:,}" if self.total and self.total != self.processed else "")
        text = summary or f"{self.command}: {counted} {self.unit}"
        print(f"  {text} ({took})", file=self.stream, flush=True)
        if self.record:
            clear_progress()
        self.record = False

    def __enter__(self) -> "Task":
        return self

    def __exit__(self, kind, error, trace) -> None:
        if self.record or self._line:
            self._clear()
            if self.record:
                clear_progress()
            self.record = False

    # ── Drawing ─────────────────────────────────────────────────────────
    def line(self, width: int = 100) -> str:
        """The progress line: what, how far, how long, and how long still."""
        phase = PHASES.get(self.phase, self.phase.replace("_", " "))
        label = f"{self.command}: {phase}" if phase else self.command
        elapsed = time.time() - self.started
        parts = []
        if self.total:
            done = min(self.processed, self.total) / self.total
            parts.append(f"{self.processed:,}/{self.total:,} {self.unit}")
            parts.append(f"{int(done * 100)}%")
            parts.append(_duration(elapsed))
            if 0 < done < 1 and elapsed > 2:
                parts.append(f"~{_duration(elapsed / done * (1 - done))} left")
        else:
            parts.append(f"{self.processed:,} {self.unit}" if self.processed else "working")
            parts.append(_duration(elapsed))
        stats = " · ".join(parts)
        if self.tty and self.total:
            room = max(10, min(30, width - len(label) - len(stats) - 8))
            filled = int(room * min(self.processed, self.total) / self.total)
            text = f"  {label}  {'█' * filled}{'░' * (room - filled)}  {stats}"
        else:
            text = f"  {label} · {stats}"
        if self.detail and len(text) + 3 < width:
            text += f" · {self.detail}"[: width - len(text)]
        return text[:width]

    def _show(self, force: bool) -> None:
        now = time.time()
        if self.record and (force or now - self._recorded >= self.RECORD_EVERY):
            self._recorded = now
            write_progress(self.command, self.processed, self.total, self.stored, self.skipped,
                           self.started, self.projects, phase=self.phase or self.command, unit=self.unit)
        if self.tty:
            if force or now - self._drawn >= self.REDRAW:
                self._drawn = now
                import shutil
                width = shutil.get_terminal_size((100, 20)).columns - 1
                print(f"\r\033[K{self.line(width)}", end="", file=self.stream, flush=True)
                self._line = True
            return
        decile = int(10 * self.processed / self.total) if self.total else -1
        if (force and self.phase != self._logged_phase) or decile > self._decile or now - self._logged >= self.LOG_EVERY:
            self._logged, self._decile, self._logged_phase = now, max(decile, self._decile), self.phase
            print(self.line(160), file=self.stream, flush=True)

    def _clear(self) -> None:
        if self.tty and self._line:
            print("\r\033[K", end="", file=self.stream, flush=True)
            self._line = False
