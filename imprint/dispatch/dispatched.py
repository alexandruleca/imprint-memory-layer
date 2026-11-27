"""One Dispatched session: started for a Task, watched, and its outcome recorded (ADR 0220).

The Dispatcher claims the Task itself as the session ``dispatch:<run id>``
before the client starts, with the child's pid on the lease so a crashed
session makes the Task Stale at once. The prompt hands the session the
claim token, and the session takes the claim over on its first Imprint
call. A worker that ends without moving its Task to Review has failed: the
claim is released, with a comment holding the end of its log.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid

from .. import processes, tasks
from ..tasks.actors import SYSTEM
from . import folders, runners, store, worktrees

DISPATCH = 'dispatch:'
SKILL = 'imprint-dispatch'
WORKABLE = ('backlog', 'ready', 'in_progress')
STOP_GRACE = 3.0
RENEW_EVERY = 60.0
POLL = 2.0
LOG_TAIL = 1500
# Variables of the process that starts a session that must not reach it: a
# client's own session markers, the WSL bridge's, and refused ones.
_DROPPED_ENV = ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_SSE_PORT',
                'IMPRINT_CLIENT_SESSION', *runners.REFUSED_ENV)


def session_of(run_id: str) -> str:
    """The Board session a Dispatched session holds its Task as until it takes the claim over."""
    return f'{DISPATCH}{run_id}'


def _workspace(workspace: str | None) -> str:
    from ..workspace_policy import workspace_name
    return workspace_name(workspace)


def instructions() -> str:
    """The text of ``imprint-dispatch``: the person's fork when there is one, else the built-in."""
    from .. import registry
    text = registry.find(SKILL, 'skill')['files']['SKILL.md']
    if text.startswith('---'):
        end = text.find('\n---', 3)
        if end != -1:
            text = text[end + 4:]
    return text.strip()


def prompt(state: dict, *, workspace: str, worktree: dict, token: str, role: str = 'work',
           note: str = '') -> str:
    """The Dispatched session's prompt: the Task, where to work, the claim
    token, its client's ``note`` about its own sandbox, and the
    instructions of ``imprint-dispatch``."""
    doing = 'work on' if role == 'work' else 'review'
    return '\n'.join([
        f"You are a Dispatched session. Imprint's Dispatcher started you, without the person, to {doing} "
        'one Task of its Board.',
        '',
        f"- Task: {state['id']}: {state['title']}",
        f'- Workspace: {workspace}: pass workspace="{workspace}" on every Imprint call',
        f"- Project: {state['project']}: pass project=\"{state['project']}\" when you create a Task",
        f"- Your worktree: {worktree['cwd']}, on branch {worktree['branch']}, made from {worktree['base']} "
        f"at {worktree['base_commit']}",
        f'- Your claim token: {token}',
        *(['', note] if note else []),
        '',
        'Follow these instructions:',
        '',
        instructions(),
    ]) + '\n'


def _env(workspace: str, run_id: str) -> dict:
    env = {key: value for key, value in os.environ.items() if key not in _DROPPED_ENV
           and not key.startswith('IMPRINT_WSL_')}
    # The session's Imprint server serves the Task's workspace whatever its folder says (ADR 0072's env pin).
    env['IMPRINT_WORKSPACE'] = workspace
    env['IMPRINT_DISPATCH_RUN'] = run_id
    return env


def _group() -> dict:
    """Start the client in its own process group, so stopping it stops its children."""
    if sys.platform == 'win32':
        return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {'start_new_session': True}


def _signal_group(pid: int, force: bool) -> None:
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/PID', str(pid), '/T', *(['/F'] if force else [])], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        return
    try:
        os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
        except OSError:
            pass


def kill(pid: int, proc: subprocess.Popen | None = None, grace: float = STOP_GRACE, *,
         distro: str | None = None, run_id: str | None = None) -> None:
    """End a client and everything it started: SIGTERM to its process group
    (a tree kill on Windows; the group inside its WSL distribution for a
    project there), then SIGKILL after ``grace`` seconds. Works on a
    session another process started, by its pid."""
    def alive():
        return proc.poll() is None if proc else processes.is_alive(pid)
    if distro and run_id:
        # The group inside the distribution first: ending wsl.exe alone leaves it running.
        from . import wsl
        if wsl.kill(distro, run_id, force=False):
            deadline = time.monotonic() + grace
            while wsl.group_alive(distro, run_id) and time.monotonic() < deadline:
                time.sleep(0.2)
            if wsl.group_alive(distro, run_id):
                wsl.kill(distro, run_id, force=True)
    if not pid or not alive():
        if proc:
            proc.wait()
        return
    _signal_group(pid, force=False)
    deadline = time.monotonic() + grace
    while alive() and time.monotonic() < deadline:
        time.sleep(0.1)
    if alive():
        _signal_group(pid, force=True)
    if proc:
        proc.wait()


def last_message_path(run_id: str):
    """Where a client that writes its final message to a file (Codex) puts it."""
    return store.log_path(run_id).with_suffix('.last')


def log_tail(path, limit: int = LOG_TAIL) -> str:
    """The end of a session's log as people read it: the final message the
    client wrote beside it, else its final result, else its last message,
    else the raw end of the log."""
    try:
        with open(str(path)[:-len('.log')] + '.last' if str(path).endswith('.log') else '', encoding='utf-8',
                  errors='replace') as last:
            text = last.read().strip()
        if text:
            return text[-limit:]
    except OSError:
        pass
    try:
        with open(path, encoding='utf-8', errors='replace') as log:
            lines = log.read().splitlines()
    except OSError:
        return ''
    last_text = ''
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get('type') == 'result' and isinstance(event.get('result'), str) and event['result'].strip():
            return event['result'].strip()[-limit:]
        item = event.get('item') if isinstance(event.get('item'), dict) else {}
        if not last_text and item.get('type') == 'agent_message' and isinstance(item.get('text'), str):
            last_text = item['text'].strip()
        if not last_text and event.get('type') == 'assistant':
            content = (event.get('message') or {}).get('content') or []
            texts = [part.get('text', '') for part in content if isinstance(part, dict) and part.get('type') == 'text']
            last_text = '\n'.join(t for t in texts if t).strip()
    return (last_text or '\n'.join(lines).strip())[-limit:]


def _our_sessions(task: str, run_id: str, workspace: str) -> set[str]:
    """The Board sessions that hold this run's claim: the Dispatcher's, and
    each session that took it over with the token."""
    ours, claims = {session_of(run_id)}, set()
    for event in tasks.history(task, workspace=workspace):
        if event['kind'] == 'claimed' and (event['session'] == session_of(run_id)
                                           or event['data'].get('replaces') in claims):
            claims.add(event['id'])
            ours.add(event['session'])
    return ours


def start(task: str, *, workspace: str | None = None, client: str | None = None,
          role: str = 'work') -> tuple[dict, subprocess.Popen]:
    """Claim ``task`` and start a Dispatched session of ``client`` on it
    (by default, the client the Task is assigned to, else the default
    client, ADR 0222): (the run's record, the client's process)."""
    workspace = _workspace(workspace)
    state = tasks.get(task, workspace=workspace)
    if role == 'work' and state['status'] not in WORKABLE:
        raise ValueError(f"{state['id']} is {tasks.STATUS_LABELS.get(state['status'], state['status'])}: "
                         'a Dispatched session works on Backlog, Ready or Stale Tasks')
    folder = folders.resolve(state['project'], workspace)
    if not folder['ok']:
        raise LookupError(folder['reason'])
    from . import clients
    distro = folder.get('distro')
    where = f' in {distro}' if distro else ''
    if client is None:
        client = clients.for_task(state, clients.allowed(where=distro), tasks.device_name())
        if client is None:
            raise LookupError(f"No client this device may start for {state['id']}{where}: "
                              '`imprint tasks dispatch status` says which are available')
    runner = runners.get(client)
    binary = clients.binary(runner, distro)  # before claiming: a missing client claims nothing
    if not binary:
        raise LookupError(f'{runner.label} was not found{where}: put {runner.binary} on PATH or set {runner.setting}')
    worktree = worktrees.prepare(folder, state['id'], state['title'])
    run_id = uuid.uuid4().hex[:12]
    conversation = str(uuid.uuid4())
    last_message = str(last_message_path(run_id))
    if distro:
        from . import wsl
        last_message = wsl.linux_path(distro, last_message)
    argv = runners.check(runner.argv(binary, {'session_id': conversation, 'cwd': worktree['cwd'],
                                              'git_dir': worktree['git_dir'], 'last_message': last_message}))
    if distro:
        argv = wsl.launch(distro, worktree['cwd'], argv, run_id)
    actor = tasks.agent(session_of(run_id), client=runner.client)
    run = store.add({'id': run_id, 'workspace': workspace, 'task': state['id'], 'title': state['title'],
                     'project': state['project'], 'client': runner.client, 'role': role,
                     'session': session_of(run_id), 'conversation': conversation, 'worktree': worktree['path'],
                     'branch': worktree['branch'], 'base': worktree['base'], 'started_at': time.time(),
                     'watcher': os.getpid(), 'distro': distro})
    try:
        claimed = tasks.claim(state['id'], actor=actor, pid=os.getpid(), workspace=workspace)
    except (PermissionError, ValueError) as error:
        store.update(run_id, ended_at=time.time(), outcome='refused', detail=str(error))
        raise
    try:
        tasks.checkpoint(state['id'], f"Dispatched to {runner.label} on this device{where}: worktree {worktree['path']} "
                         f"on {worktree['branch']}, made from {worktree['base']} at {worktree['base_commit']}.",
                         branch=worktree['branch'], commit=worktree['base_commit'],
                         next_step='The Dispatched session takes the claim over and works the Task.',
                         actor=actor, workspace=workspace)
        text = prompt(claimed['task'], workspace=workspace, worktree=worktree, token=claimed['token'], role=role,
                      note=runner.note)
        log = store.log_path(run_id)
        log.parent.mkdir(parents=True, exist_ok=True)
        env = _env(workspace, run_id)
        if distro:
            env = wsl.launch_env(env)  # wsl.exe starts the client in its worktree inside the distribution
        with open(log, 'wb') as output:
            proc = subprocess.Popen(argv, cwd=None if distro else worktree['cwd'], stdin=subprocess.PIPE,
                                    stdout=output, stderr=subprocess.STDOUT, env=env, **_group())
        tasks.renew(session_of(run_id), pid=proc.pid)
        store.update(run_id, pid=proc.pid)
        try:
            proc.stdin.write(text.encode('utf-8'))
            proc.stdin.close()
        except OSError:
            pass  # The client ended at once; its log says why.
    except Exception as error:
        finish(run_id, None, reason=f'could not start: {error}')
        raise
    return {**run, 'pid': proc.pid}, proc


class Session:
    """A running Dispatched session this process watches: one it started
    (``proc``), or one it adopted by pid after the process that started it
    went away. ``step`` renews the Dispatcher's lease until the session
    takes the claim over, stops the session past its time limit, and
    records the outcome once it has ended."""

    def __init__(self, run: dict, proc: subprocess.Popen | None = None, *, minutes: float | None = None):
        from ..config_schema import resolve
        self.run, self.proc, self.pid = run, proc, run['pid']
        self.limit = 60 * float(minutes if minutes is not None else resolve('dispatch.session_minutes')[0])
        self.renewed, self.reason, self.stopped = time.monotonic(), None, False

    def running(self) -> bool:
        return self.proc.poll() is None if self.proc else processes.is_alive(self.pid)

    def stop(self, reason: str, *, stopped: bool = True) -> None:
        """End the session: ``stopped`` (by the person or the server's
        shutdown) unless it is a failure, such as its time limit."""
        if not self.reason:
            self.reason, self.stopped = reason, stopped
        kill(self.pid, self.proc, distro=self.run.get('distro'), run_id=self.run['id'])

    def step(self) -> dict | None:
        """None while the session runs; the run's record once it has ended."""
        if self.running():
            if time.time() - self.run['started_at'] > self.limit:
                self.stop(f'ran past its {self.limit / 60:g}-minute limit and was stopped', stopped=False)
            elif time.monotonic() - self.renewed >= RENEW_EVERY:
                tasks.renew(self.run['session'], pid=self.pid)
                self.renewed = time.monotonic()
            if self.running():
                return None
        code = self.proc.wait() if self.proc else None  # An adopted session's exit code is not known.
        return finish(self.run['id'], code, reason=self.reason, stopped=self.stopped)


def watch(run: dict, proc: subprocess.Popen, *, minutes: float | None = None) -> dict:
    """Wait for the session to end, then record its outcome (``Session.step``)."""
    session = Session(run, proc, minutes=minutes)
    try:
        while (ended := session.step()) is None:
            time.sleep(POLL)
    except KeyboardInterrupt:
        session.stop('was stopped by the person')  # a stop, not a failure
        ended = session.step()
    return ended


def request_stop(run_id: str, reason: str = 'was stopped by the person') -> dict:
    """Stop a running session from any process: the reason is recorded for
    whoever watches it, then its process group is ended."""
    run = store.get(run_id)
    if run is None:
        raise LookupError(f'No Dispatched session {run_id} on this device')
    if run['ended_at'] is not None:
        raise ValueError(f"Dispatched session {run_id} already ended: {run['detail']}")
    store.update(run_id, stop_reason=reason)
    kill(run['pid'], distro=run.get('distro'), run_id=run_id)
    return store.get(run_id)


def finish(run_id: str, exit_code: int | None, *, reason: str | None = None, stopped: bool = False) -> dict:
    """Record how the run ended. A worker whose Task is in Review succeeded;
    any other end releases the claim this run holds, with a comment. A
    session stopped by the person or the server's shutdown is ``stopped``,
    any other end ``failed`` (ADR 0220)."""
    run = store.get(run_id)
    if run['ended_at'] is not None:
        return run  # Already recorded, by whoever saw it end first.
    workspace, task = run['workspace'], run['task']
    if run.get('stop_reason'):
        reason, stopped = run['stop_reason'], True
    state = tasks.get(task, workspace=workspace)
    if not reason and run['role'] == 'work' and state['status'] == 'review':
        outcome, detail = 'review', 'moved the Task to Review'
    else:
        outcome = 'stopped' if stopped else 'failed'
        detail = reason or (f'ended (exit code {exit_code}) without moving the Task to Review'
                            if run['role'] == 'work' else f'ended (exit code {exit_code})')
        # A holder whose process ended is Stale at once, and the Board shows it as the previous holder.
        holder = state.get('holder') or (state.get('previous_holder') if state.get('stale') else None)
        released = False
        if holder and holder['session'] in _our_sessions(task, run_id, workspace) and state['status'] != 'review':
            tasks.release(task, actor=SYSTEM, workspace=workspace)
            released = True
        tail = log_tail(store.log_path(run_id))
        label = runners.RUNNERS[run['client']].label if run['client'] in runners.RUNNERS else run['client']
        text = f'The Dispatched session ({label}) {detail}.'
        if released:
            text += ' Its claim is released.'
        if tail:
            text += f'\n\nThe end of its log:\n\n{tail}'
        tasks.comment(task, text, actor=SYSTEM, workspace=workspace)
    store.update(run_id, ended_at=time.time(), exit_code=exit_code, outcome=outcome, detail=detail)
    if run.get('distro'):
        from . import wsl
        try:
            wsl.run(run['distro'], ['rm', '-f', wsl.pid_file(run_id)], timeout=10)
        except RuntimeError:
            pass  # A file in the distribution's /tmp; it goes with the next restart.
    ended = store.get(run_id)
    if outcome == 'failed':
        from . import guards
        guards.after_failure(ended)
    return ended


def run(task: str, *, workspace: str | None = None, client: str | None = None, minutes: float | None = None,
        on_start=None) -> dict:
    """Dispatch ``task`` now and wait for its session to end: the run's record."""
    started, proc = start(task, workspace=workspace, client=client)
    if on_start:
        on_start(started)
    return watch(started, proc, minutes=minutes)
