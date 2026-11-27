"""The Dispatcher's loop in the dashboard server (ADR 0220).

While the dashboard server runs, a thread watches the Dispatched sessions
this device started and, every pass, starts one more for a workspace the
person switched on when fewer than ``dispatch.concurrency`` run. It takes
Tasks by the Board's own rules for taking work unasked (``tasks.takeable``)
across every project with a folder on this device.

Sessions started elsewhere, such as by ``imprint tasks dispatch run``, are
counted against the limit and left to the process watching them; one whose
watcher went away is adopted by its pid. When the server stops, the
sessions it watches are stopped and their claims released.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

from .. import config, processes, tasks
from . import dispatched, folders, guards, store

PASS_SECONDS = 30.0
POLL_SECONDS = 2.0
CLIENT = 'claude-code'  # The only runner until clients are detected (Dispatcher 5).


def _setting(key: str):
    from ..config_schema import resolve
    return resolve(key)[0]


def server_running() -> bool:
    """Whether a dashboard server serves this data directory now: the Dispatcher runs only in it."""
    try:
        registered = json.loads((config.get_data_dir() / 'imprint_ui.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return processes.is_alive(registered.get('pid'))


def running_runs() -> list[dict]:
    """Runs on this device whose client still runs, whoever started them."""
    return [run for run in store.unfinished() if processes.is_alive(run['pid'])]


def candidates(workspace: str) -> list[dict]:
    """The Tasks the Dispatcher would take in ``workspace``, in order: Ready
    and standalone, open to an agent of its client on this device, as the
    workspace's autonomy allows, and not readied by a Dispatched session."""
    found = [state for state in tasks.takeable(client=CLIENT, device=tasks.device_name(), workspace=workspace)
             if state['status'] == 'ready' and not state['stale']]
    if not found:
        return []
    dispatched = tasks.dispatched_sessions(workspace)
    return [state for state in found if not guards.readied_by_dispatched(state['id'], workspace, dispatched)]


class Dispatcher:
    def __init__(self, *, interval: float = PASS_SECONDS, poll: float = POLL_SECONDS):
        self.interval, self.poll = interval, poll
        self.sessions: dict[str, dispatched.Session] = {}
        self.skipped: dict[str, str] = {}  # project -> why it was passed over on the last pass
        self._stopping, self._wake = threading.Event(), threading.Event()
        self._thread: threading.Thread | None = None
        self._next_pass = 0.0
        self._lock = threading.Lock()

    # ── Lifecycle ──────────────────────────────────────────────

    def start(self) -> None:
        self.recover()
        self._thread = threading.Thread(target=self._run, name='imprint-dispatcher', daemon=True)
        self._thread.start()

    def stop(self, reason: str = 'was stopped because the dashboard server stopped') -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=10)
        with self._lock:
            for session in list(self.sessions.values()):
                session.stop(reason)
            self.watch()

    def poke(self) -> None:
        """Run a pass now, after the switch changed."""
        self._next_pass = 0.0
        self._wake.set()

    def busy(self) -> bool:
        return bool(self.sessions)

    def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                with self._lock:
                    self.tick()
            except Exception as error:  # The loop outlives any one pass.
                print(f'[imprint] dispatcher pass failed: {error}', file=sys.stderr, flush=True)
            self._wake.wait(self.poll)
            self._wake.clear()

    # ── Passes ─────────────────────────────────────────────────

    def recover(self) -> None:
        """Take on runs nobody watches: adopt a running one by its pid, and
        record how a gone one ended."""
        for run in store.unfinished():
            if run['id'] in self.sessions or (run['watcher'] != os.getpid() and processes.is_alive(run['watcher'])):
                continue
            if processes.is_alive(run['pid']):
                self.sessions[run['id']] = dispatched.Session(run)
            else:
                dispatched.finish(run['id'], None, reason='ended while no Dispatcher watched it')

    def tick(self) -> None:
        self.watch()
        if time.monotonic() >= self._next_pass:
            self._next_pass = time.monotonic() + self.interval
            self.recover()
            self.dispatch()

    def watch(self) -> None:
        for run_id, session in list(self.sessions.items()):
            ended = session.step()
            if ended is None:
                continue
            del self.sessions[run_id]
            if ended['outcome'] == 'failed':
                guards.switch_off_after_failures(ended)

    def dispatch(self) -> dict | None:
        """Start one Dispatched session when there is room and work: the
        started run, or None."""
        if len(running_runs()) >= max(0, int(_setting('dispatch.concurrency'))) or guards.held():
            return None
        self.skipped = {}
        for workspace in store.switched_on_workspaces():
            for state in candidates(workspace):
                if state['project'] in self.skipped:
                    continue
                folder = folders.resolve(state['project'], workspace)
                if not folder['ok']:
                    self.skipped[state['project']] = folder['reason']
                    continue
                try:
                    run, proc = dispatched.start(state['id'], workspace=workspace, client=CLIENT)
                except (LookupError, PermissionError, ValueError, OSError, RuntimeError) as error:
                    print(f"[imprint] dispatcher passed over {state['id']}: {error}", file=sys.stderr, flush=True)
                    continue
                self.sessions[run['id']] = dispatched.Session({**run, 'watcher': os.getpid()}, proc)
                return run
        return None


_dispatcher: Dispatcher | None = None


def start() -> Dispatcher:
    """Start the dashboard server's Dispatcher."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = Dispatcher()
        _dispatcher.start()
    return _dispatcher


def stop() -> None:
    global _dispatcher
    if _dispatcher is not None:
        _dispatcher.stop()
        _dispatcher = None


def poke() -> None:
    if _dispatcher is not None:
        _dispatcher.poke()


def busy() -> bool:
    """Whether Dispatched sessions this server watches still run: an
    auto-shutdown server waits for them."""
    return _dispatcher is not None and _dispatcher.busy()
