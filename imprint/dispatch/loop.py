"""The Dispatcher's loop in the dashboard server (ADR 0220).

While the dashboard server runs, a thread watches the Dispatched sessions
this device started and, every pass, starts one more for a workspace the
person switched on when fewer than ``dispatch.concurrency`` run. It takes
Tasks by the Board's own rules for taking work unasked (``tasks.takeable``)
across every project with a folder on this device.

Sessions started elsewhere, such as by ``imprint tasks dispatch run``, are
counted against the limit and left to the process watching them; one whose
watcher went away is adopted by its pid, while that pid is still the
process the session started as. When the server stops, the
sessions it watches are stopped and their claims released.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

from .. import config, processes, tasks
from ..tasks.view import _order as board_order
from . import clients, dispatched, folders, guards, runners, store, worktrees

PASS_SECONDS = 30.0
CLEANUP_SECONDS = 600.0
POLL_SECONDS = 2.0


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
    return [run for run in store.unfinished() if dispatched.alive(run)]


def _choices_where(project: str, workspace: str, known: dict) -> list[str]:
    """The clients allowed where ``project`` lives: on this system, or in
    the WSL distribution its folder is in (ADR 0222). A project without a
    folder here gets this system's; the pass skips it anyway."""
    if project not in known:
        folder = folders.resolve(project, workspace)
        known[project] = clients.allowed(where=folder.get('distro') if folder['ok'] else None)
    return known[project]


def candidates(workspace: str, *, choices: list[str] | None = None) -> list[dict]:
    """The Tasks the Dispatcher would take in ``workspace``, in the Board's
    order, each with the ``client`` that would work it: Ready and
    standalone, open to one of the clients allowed where its project lives
    (``choices`` instead, when given), as the workspace's autonomy allows,
    and not fed by dispatched work (ADR 0227)."""
    device = tasks.device_name()
    known: dict = {}
    found, seen = [], set()
    for client in (choices if choices is not None else runners.RUNNERS):
        for state in tasks.takeable(client=client, device=device, workspace=workspace):
            if state['id'] not in seen and (state['stale'] or state['status'] == 'ready'):
                seen.add(state['id'])
                found.append(state)
    if not found:
        return []
    # Stale Tasks before Ready ones (ADR 0220), each in the Board's order across the clients' lists.
    found.sort(key=lambda state: (not state['stale'], board_order(state)))
    dispatched = tasks.dispatched_sessions(workspace)
    chosen = []
    for state in found:
        here = choices if choices is not None else _choices_where(state['project'], workspace, known)
        client = clients.for_task(state, here, device)
        if client and not guards.fed_by_dispatched(state['id'], workspace, dispatched):
            chosen.append({**state, 'client': client, 'role': 'work'})
    return chosen


def reviews(workspace: str, *, choices: list[str] | None = None) -> list[dict]:
    """Tasks in Review the Dispatcher would review (ADR 0221), in the
    Board's order, each with its reviewer's ``client``: standalone, not the
    person's, not held, not for another device, not fed by dispatched work
    (ADR 0227), as autonomy allows."""
    if tasks.autonomy(workspace) == 'off':
        return []
    device = tasks.device_name()
    known: dict = {}
    found = []
    dispatched = tasks.dispatched_sessions(workspace)
    for state in tasks.listing(status='review', workspace=workspace):
        if 'user' in state['assignees'] or state.get('feature') or state.get('tasks') or state.get('holder'):
            continue
        if guards.fed_by_dispatched(state['id'], workspace, dispatched):
            continue  # dispatched work that put itself in Review (ADR 0227)
        devices = [a for a in state['assignees'] if '@' in a]
        if devices and not any(a.split('@', 1)[1].lower() == device for a in devices):
            continue
        here = choices if choices is not None else _choices_where(state['project'], workspace, known)
        client = guards.reviewer(state, here, workspace)
        if client:
            found.append({**state, 'client': client, 'role': 'review'})
    return sorted(found, key=board_order)


def refinements(workspace: str, *, choices: list[str] | None = None) -> list[dict]:
    """Backlog Tasks a refinement would groom in ``workspace`` (ADR 0230),
    in the Board's order, each with its ``client``: no acceptance criteria,
    not a Feature, not the person's, not held, not for another device, and
    not refined since the person's last change to it."""
    device = tasks.device_name()
    known: dict = {}
    found = []
    for state in tasks.listing(status='backlog', workspace=workspace):
        if state['acceptance'] or 'user' in state['assignees'] or state.get('tasks') or state.get('holder'):
            continue
        devices = [a for a in state['assignees'] if '@' in a]
        if devices and not any(a.split('@', 1)[1].lower() == device for a in devices):
            continue
        if guards.refined_since_change(state['id'], workspace):
            continue
        here = choices if choices is not None else _choices_where(state['project'], workspace, known)
        client = clients.for_task(state, here, device)
        if client:
            found.append({**state, 'client': client, 'role': 'refine'})
    return sorted(found, key=board_order)


def cleanup() -> int:
    """Remove the worktrees of Tasks that reached Done or Cancelled, or are
    gone, keeping their branches (ADR 0223). A worktree still in use (on
    Windows, a process may keep it as its folder for a while after the
    session ends) is tried again on a later pass. Returns how many went."""
    removed = 0
    for run in store.uncleaned():
        if any(r['task'] == run['task'] for r in running_runs()):
            continue
        if run['role'] == 'refine':  # its throwaway copy goes whatever the Task's status (ADR 0230)
            try:
                folder = folders.resolve(run['project'], run['workspace'])
                if folder['ok']:
                    worktrees.remove_scratch(folder, run['task'])
                store.mark_run_cleaned(run['id'])
                removed += 1
            except Exception as error:
                print(f"[imprint] dispatcher could not remove the copy refinement used for {run['task']} yet: {error}",
                      file=sys.stderr, flush=True)
            continue
        state = dispatched._state_or_none(run['task'], run['workspace'])  # None: deleted, or its workspace is gone
        if state is not None and state['status'] not in ('done', 'cancelled'):
            continue
        try:
            folder = folders.resolve(run['project'], run['workspace'])
            if folder['ok']:
                worktrees.remove(folder, run['task'])
            store.mark_cleaned(run['workspace'], run['task'])
            removed += 1
        except Exception as error:  # in use, or git is unavailable now: next time
            print(f"[imprint] dispatcher could not remove the worktree of {run['task']} yet: {error}",
                  file=sys.stderr, flush=True)
    return removed


class Dispatcher:
    def __init__(self, *, interval: float = PASS_SECONDS, poll: float = POLL_SECONDS):
        self.interval, self.poll = interval, poll
        self.sessions: dict[str, dispatched.Session] = {}
        self.skipped: dict[str, str] = {}  # project -> why it was passed over on the last pass
        self._stopping, self._wake = threading.Event(), threading.Event()
        self._thread: threading.Thread | None = None
        self._next_pass = self._next_cleanup = 0.0
        self._lock = threading.Lock()

    # ── Lifecycle ──────────────────────────────────────────────

    def start(self) -> None:
        self.recover()  # never raises: one run's trouble must not keep the dashboard server from starting
        threading.Thread(target=clients.detected, kwargs={'refresh': True}, daemon=True).start()
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
        record how a gone one ended. One run's error is logged and the
        others are still taken on."""
        try:
            unfinished = store.unfinished()
        except Exception as error:
            print(f'[imprint] dispatcher could not read its runs: {error}', file=sys.stderr, flush=True)
            return
        for run in unfinished:
            try:
                if run['id'] in self.sessions or (run['watcher'] != os.getpid() and dispatched.watching(run)):
                    continue
                if dispatched.alive(run):  # its own process, never one that got its pid later
                    mine = {'watcher': os.getpid(), 'watcher_started': processes.started(os.getpid())}
                    store.update(run['id'], **mine)
                    self.sessions[run['id']] = dispatched.Session({**run, **mine})
                else:
                    dispatched.finish(run['id'], None, reason='ended while no Dispatcher watched it')
            except Exception as error:
                print(f"[imprint] dispatcher could not take on run {run['id']}: {error}", file=sys.stderr, flush=True)

    def tick(self) -> None:
        self.watch()
        if time.monotonic() >= self._next_pass:
            self._next_pass = time.monotonic() + self.interval
            self.recover()
            self.dispatch()
        if time.monotonic() >= self._next_cleanup:
            self._next_cleanup = time.monotonic() + CLEANUP_SECONDS
            cleanup()

    def watch(self) -> None:
        for run_id, session in list(self.sessions.items()):
            try:
                ended = session.step()
            except Exception as error:  # one session's trouble must not stop the others being watched
                print(f'[imprint] dispatcher could not check run {run_id}: {error}', file=sys.stderr, flush=True)
                continue
            if ended is None:
                continue
            del self.sessions[run_id]
            if ended['outcome'] == 'failed':
                guards.switch_off_after_failures(ended)

    def dispatch(self) -> dict | None:
        """Start one Dispatched session when there is room and work: the
        started run, or None. Reviews, then Stale and Ready Tasks, in the
        workspaces switched on; only when none of them started, a Backlog
        refinement in the workspaces it is switched on for (ADR 0230)."""
        if len(running_runs()) >= max(0, int(_setting('dispatch.concurrency'))) or guards.held():
            return None
        self.skipped = {}
        for workspace in store.switched_on_workspaces():
            for state in [*reviews(workspace), *candidates(workspace)]:  # reviews first (ADR 0221)
                started = self._start(state, workspace)
                if started is not _PASSED:
                    return started
        for workspace in store.refining_workspaces():
            for state in refinements(workspace):
                started = self._start(state, workspace)
                if started is not _PASSED:
                    return started
        return None

    def _start(self, state: dict, workspace: str):
        """Start a session for ``state``: its run; None when the pass must
        end (a failed start, ADR 0228); ``_PASSED`` when it was passed over."""
        if state['project'] in self.skipped:
            return _PASSED
        folder = folders.resolve(state['project'], workspace)
        if not folder['ok']:
            self.skipped[state['project']] = folder['reason']
            return _PASSED
        if state['role'] == 'review' and not guards.takeover(state, folder, workspace, doing='review this Task')[0]:
            return _PASSED
        if state.get('stale') and not guards.takeover(state, folder, workspace)[0]:
            return _PASSED
        try:
            run, proc = dispatched.start(state['id'], workspace=workspace, client=state['client'],
                                         role=state['role'],
                                         on_wait=self.watch)  # its sessions are watched while the claim travels
        except dispatched.StartFailed as failed:
            # A claimed Task that would not start: it counts toward switching off, and ends
            # the pass, so a broken setup fails one Task a pass, not every one (ADR 0228).
            print(f"[imprint] dispatcher {failed}", file=sys.stderr, flush=True)
            guards.switch_off_after_failures(failed.run)
            return None
        except (LookupError, PermissionError, ValueError, OSError, RuntimeError) as error:
            print(f"[imprint] dispatcher passed over {state['id']}: {error}", file=sys.stderr, flush=True)
            return _PASSED
        self.sessions[run['id']] = dispatched.Session({**run, 'watcher': os.getpid()}, proc)
        return run


_PASSED = object()  # a Task the pass went past, unlike None, which ends the pass


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
