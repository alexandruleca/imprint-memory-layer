"""Dashboard side of ``imprint update`` (ADR 0023).

The CLI resolves releases and knows how this installation updates. This
module asks it for a report, starts it detached (it restarts this server
once the new release is installed, ADR 0050) and reads its progress back
from its log.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config
from . import queue as job_queue

CHECK_TTL_SECONDS = 6 * 60 * 60

# Automatic updates (ADR 0175): the first look a while after the server
# starts, then every AUTO_INTERVAL; GitHub is still asked at most every
# CHECK_TTL_SECONDS. An update runs only when it interrupts nothing.
AUTO_FIRST = 10 * 60
AUTO_INTERVAL = 30 * 60
AGENT_QUIET = 15 * 60        # no agent called an Imprint tool for this long
USER_IDLE = 10 * 60          # no keyboard or mouse input for this long
RECORDING_QUIET = 120        # the recorder sends audio every 5 s while it runs
RETRY_AFTER_FAILURE = 24 * 60 * 60
# Methods that update without the user: the macOS package opens Installer.
UNATTENDED = ("windows-setup", "archive")

_lock = threading.Lock()
_report: dict | None = None
_checked_at = 0.0
_process: subprocess.Popen | None = None
_auto_target = ""            # the release an automatic update last started
_auto_failed: dict[str, float] = {}
_auto_state = ""             # what the last automatic look found, for the dashboard
log = logging.getLogger(__name__)


class UpdateConflict(Exception):
    """An update cannot start now."""


def log_path() -> Path:
    return config.get_data_dir() / "update.log"


def installer_log_path() -> Path:
    """Where ``imprint update`` has the Windows installer log (cmd/update.go)."""
    return config.get_data_dir() / "update-setup.log"


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
        installer_log_path().unlink(missing_ok=True)  # A previous update's installer.
        with open(log_path(), "w", encoding="utf-8") as log:
            _process = subprocess.Popen(
                [job_queue.find_imprint_binary(), "update", "-y"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                env=_env(), **detach,
            )


def status(lines: int = 20) -> dict:
    """Whether an update started by this server is running or failed, and its log tail.

    An updater that exits successfully has handed over: the Windows installer
    and the restart of this server finish the update after it. While that
    installer runs, ``installer`` follows it from its log, which it closes
    after its last step: ``running``, ``succeeded`` or ``failed``.
    """
    try:
        log = log_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        log = []
    code = None if _process is None else _process.poll()
    report = {"running": running(), "failed": bool(code), "log": log[-lines:]}
    if code == 0:
        try:
            installer = installer_log_path().read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except FileNotFoundError:
            return report
        # Inno Setup logs this when the files are in place, then runs the
        # provisioning and server restart, then closes its log.
        closed = any(line.rstrip().endswith("Log closed.") for line in installer)
        succeeded = any("Installation process succeeded." in line for line in installer)
        report["installer"] = "running" if not closed else "succeeded" if succeeded else "failed"
        report["failed"] = report["installer"] == "failed"
        report["log"] = (log + installer)[-lines:]
    return report


# ── Automatic updates (ADR 0175) ─────────────────────────────────

def user_idle_seconds() -> float | None:
    """How long the computer has had no keyboard or mouse input, where the
    system says: Windows and macOS. None where it cannot be told."""
    try:
        if os.name == "nt":
            import ctypes

            class LastInput(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            info = LastInput(ctypes.sizeof(LastInput), 0)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                return None
            # Both are 32-bit millisecond ticks that wrap together every 49.7 days.
            return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000
        if sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4", "-r", "-k", "HIDIdleTime"],
                                 capture_output=True, text=True, timeout=5).stdout
            match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
            return int(match.group(1)) / 1e9 if match else None
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        return None
    return None


def _recording() -> bool:
    """A recording is running: started, not stopped, and its audio grew
    lately. One whose window closed stays "recording" until it is finished,
    and must not hold updates back forever."""
    from . import recordings
    now = time.time()
    for meta in recordings.list_all():
        if meta.get("status") != "recording":
            continue
        audio = recordings.root() / meta["id"] / "audio.webm"
        try:
            latest = audio.stat().st_mtime
        except OSError:
            latest = float(meta.get("started_at") or 0)
        if now - latest < RECORDING_QUIET:
            return True
    return False


def _echo_talking() -> bool:
    """A window holds an Echo conversation, or one goes on answering without
    a window (ADR 0145): the same test voice setup uses before it changes
    Echo's runtime."""
    from . import voice_api
    current = voice_api.sessions.current()
    return bool(voice_api.sessions.active or (current and current.busy()))


def busy() -> str:
    """What an update would interrupt now, or "" when nothing. An update
    restarts the dashboard server, on Windows closes and reopens the desktop
    app, and may stop MCP servers (ADRs 0023, 0050, 0084)."""
    if running():
        return "an update is running"
    if job_queue.active_job():
        return "a job is running"
    if _recording():
        return "a recording is running"
    from . import chat_replies
    if chat_replies.writing():
        return "a chat reply is being written"
    if _echo_talking():
        return "an Echo conversation is open"
    from . import agent_activity
    last = agent_activity.last_call_at()
    if last and time.time() - last < AGENT_QUIET:
        return f"an agent used Imprint in the last {AGENT_QUIET // 60} minutes"
    idle = user_idle_seconds()
    if idle is not None and idle < USER_IDLE:
        return "the computer is in use"
    return ""


def auto_update() -> str:
    """One automatic look: start the update when one is due and nothing
    would be interrupted. Returns what it found, for the log and dashboard."""
    global _auto_target, _auto_state
    from .config_schema import resolve
    if _auto_target and not running() and status().get("failed"):
        _auto_failed[_auto_target] = time.time()
        _auto_target = ""
    if not resolve("update.auto")[0]:
        _auto_state = ""
        return "automatic updates are off"
    report = check()
    target = report.get("latest") or ""
    if report.get("unsupported") or not report.get("update_available"):
        _auto_state = ""
        return "up to date"
    if report.get("method") not in UNATTENDED:
        _auto_state = "This installation updates through the macOS Installer, which needs you: use Update."
        return f"{target} needs the user ({report.get('method')})"
    if time.time() - _auto_failed.get(target, 0) < RETRY_AFTER_FAILURE:
        _auto_state = f"Installing {target} by itself failed; it tries again tomorrow, or use Update."
        return f"{target} failed lately"
    reason = busy()
    if reason:
        _auto_state = f"Installs by itself when Imprint is idle; waiting: {reason}."
        return f"{target} waits: {reason}"
    try:
        start()
    except UpdateConflict as error:
        _auto_state = f"Installs by itself when Imprint is idle; waiting: {error}."
        return f"{target} waits: {error}"
    _auto_target = target
    _auto_state = f"Installing {target}."
    return f"updating to {target}"


def auto_status() -> dict:
    """Whether updates install by themselves, and what the last look found."""
    from .config_schema import resolve
    return {"auto": bool(resolve("update.auto")[0]), "auto_state": _auto_state}


async def auto_loop() -> None:
    """The dashboard server's automatic updates: a first look AUTO_FIRST
    after it starts, then every AUTO_INTERVAL."""
    import asyncio
    await asyncio.sleep(AUTO_FIRST)
    while True:
        try:
            log.info("Automatic update: %s", await asyncio.to_thread(auto_update))
        except Exception as error:  # noqa: BLE001 - a background pass must not stop its server
            log.warning("Automatic update check failed: %s", error)
        await asyncio.sleep(AUTO_INTERVAL)
