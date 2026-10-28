"""Dashboard side of ``imprint update`` (ADR 0023).

The CLI resolves releases and knows how this installation updates. This
module asks it for a report, starts it detached (it restarts this server
once the new release is installed, ADR 0050) and reads its progress back
from its log.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from . import config
from . import queue as job_queue

CHECK_TTL_SECONDS = 6 * 60 * 60

_lock = threading.Lock()
_report: dict | None = None
_checked_at = 0.0
_process: subprocess.Popen | None = None


class UpdateConflict(Exception):
    """An update cannot start now."""


def log_path() -> Path:
    return config.get_data_dir() / "update.log"


def _env() -> dict[str, str]:
    return {**os.environ, "NO_COLOR": "1"}


def check(refresh: bool = False) -> dict:
    """``imprint update --check --json``, cached so GitHub is asked rarely.

    The CLI resolves the update.channel setting itself (ADR 0108); a report
    for another channel than the one now set is checked again.
    """
    global _report, _checked_at
    from .config_schema import resolve
    channel = resolve("update.channel")[0]
    with _lock:
        if (_report is not None and not refresh and _report.get("channel") == channel
                and time.time() - _checked_at < CHECK_TTL_SECONDS):
            return _report
    result = subprocess.run(
        [job_queue.find_imprint_binary(), "update", "--check", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=_env(),
    )
    if result.returncode != 0:
        lines = (result.stdout + result.stderr).strip().splitlines()
        raise RuntimeError(lines[-1] if lines else f"update check exited with {result.returncode}")
    report = json.loads(result.stdout)
    with _lock:
        _report, _checked_at = report, time.time()
    return report


def running() -> bool:
    return _process is not None and _process.poll() is None


def start() -> None:
    """Run ``imprint update -y`` outside this server's process group."""
    global _process
    with _lock:
        if running():
            raise UpdateConflict("An update is already running")
        if job_queue.active_job():
            raise UpdateConflict("Wait for the running job to finish, then update")
        # A console of its own without a window: the console programs the
        # updater runs (setup, `imprint ui restart`) inherit it instead of
        # each opening a window, as they would under DETACHED_PROCESS.
        detach = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
            if os.name == "nt" else {"start_new_session": True}
        )
        with open(log_path(), "w", encoding="utf-8") as log:
            _process = subprocess.Popen(
                [job_queue.find_imprint_binary(), "update", "-y"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                env=_env(), **detach,
            )


def status(lines: int = 20) -> dict:
    """Whether an update started by this server is running or failed, and its log tail.

    An updater that exits successfully has handed over: the Windows installer
    and the restart of this server finish the update after it.
    """
    try:
        log = log_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        log = []
    code = None if _process is None else _process.poll()
    return {"running": running(), "failed": bool(code), "log": log[-lines:]}
