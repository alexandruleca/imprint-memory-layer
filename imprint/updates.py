"""Dashboard side of ``imprint update`` (ADR 0023).

The CLI resolves releases and knows how this installation updates. This
module asks it for a report, starts it detached (it may stop and restart
this server) and reads its progress back from its log.
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
    """``imprint update --check --json``, cached so GitHub is asked rarely."""
    global _report, _checked_at
    with _lock:
        if _report is not None and not refresh and time.time() - _checked_at < CHECK_TTL_SECONDS:
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
        detach = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
            if os.name == "nt" else {"start_new_session": True}
        )
        with open(log_path(), "w", encoding="utf-8") as log:
            _process = subprocess.Popen(
                [job_queue.find_imprint_binary(), "update", "-y"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                env=_env(), **detach,
            )


def status(lines: int = 20) -> dict:
    """Whether an update started by this server is running, and its log tail."""
    try:
        log = log_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        log = []
    return {"running": running(), "log": log[-lines:]}
