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

from .. import tasks
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


def prompt(state: dict, *, workspace: str, worktree: dict, token: str, role: str = 'work') -> str:
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


def stop(proc: subprocess.Popen, grace: float = STOP_GRACE) -> None:
    """End the client and everything it started: SIGTERM, then SIGKILL after ``grace`` seconds."""
    if proc.poll() is not None:
        return
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T'], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            proc.terminate()
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
    proc.wait()


def log_tail(path, limit: int = LOG_TAIL) -> str:
    """The end of a session's log as people read it: its final result, else
    its last message, else the raw end of the log."""
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


def start(task: str, *, workspace: str | None = None, client: str = 'claude-code', role: str = 'work') -> tuple[dict, subprocess.Popen]:
    """Claim ``task`` and start a Dispatched session of ``client`` on it:
    (the run's record, the client's process)."""
    workspace = _workspace(workspace)
    state = tasks.get(task, workspace=workspace)
    if role == 'work' and state['status'] not in WORKABLE:
        raise ValueError(f"{state['id']} is {tasks.STATUS_LABELS.get(state['status'], state['status'])}: "
                         'a Dispatched session works on Backlog, Ready or Stale Tasks')
    folder = folders.resolve(state['project'], workspace)
    if not folder['ok']:
        raise LookupError(folder['reason'])
    runner = runners.get(client)
    conversation = str(uuid.uuid4())
    argv = runners.command(runner, conversation)  # before claiming: a missing client claims nothing
    worktree = worktrees.prepare(folder, state['id'], state['title'])
    run_id = uuid.uuid4().hex[:12]
    actor = tasks.agent(session_of(run_id), client=runner.client)
    run = store.add({'id': run_id, 'workspace': workspace, 'task': state['id'], 'title': state['title'],
                     'project': state['project'], 'client': runner.client, 'role': role,
                     'session': session_of(run_id), 'conversation': conversation, 'worktree': worktree['path'],
                     'branch': worktree['branch'], 'base': worktree['base'], 'started_at': time.time()})
    try:
        claimed = tasks.claim(state['id'], actor=actor, pid=os.getpid(), workspace=workspace)
    except (PermissionError, ValueError) as error:
        store.update(run_id, ended_at=time.time(), outcome='refused', detail=str(error))
        raise
    try:
        tasks.checkpoint(state['id'], f"Dispatched to {runner.label} on this device: worktree {worktree['path']} "
                         f"on {worktree['branch']}, made from {worktree['base']} at {worktree['base_commit']}.",
                         branch=worktree['branch'], commit=worktree['base_commit'],
                         next_step='The Dispatched session takes the claim over and works the Task.',
                         actor=actor, workspace=workspace)
        text = prompt(claimed['task'], workspace=workspace, worktree=worktree, token=claimed['token'], role=role)
        log = store.log_path(run_id)
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, 'wb') as output:
            proc = subprocess.Popen(argv, cwd=worktree['cwd'], stdin=subprocess.PIPE, stdout=output,
                                    stderr=subprocess.STDOUT, env=_env(workspace, run_id), **_group())
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


def watch(run: dict, proc: subprocess.Popen, *, minutes: float | None = None) -> dict:
    """Wait for the session to end, renewing the Dispatcher's lease until
    the session takes the claim over and stopping it past ``minutes``
    (``dispatch.session_minutes``); then record its outcome."""
    from ..config_schema import resolve
    limit = 60 * float(minutes if minutes is not None else resolve('dispatch.session_minutes')[0])
    started, renewed, reason = time.monotonic(), time.monotonic(), None
    try:
        while proc.poll() is None:
            now = time.monotonic()
            if now - started > limit:
                reason = f'ran past its {limit / 60:g}-minute limit and was stopped'
                stop(proc)
                break
            if now - renewed >= RENEW_EVERY:
                tasks.renew(run['session'], pid=proc.pid)
                renewed = now
            time.sleep(POLL)
    except KeyboardInterrupt:
        reason = 'was stopped by the person'
        stop(proc)
    return finish(run['id'], proc.wait(), reason=reason)


def finish(run_id: str, exit_code: int | None, *, reason: str | None = None) -> dict:
    """Record how the run ended. A worker whose Task is in Review succeeded;
    any other end releases the claim this run holds, with a comment."""
    run = store.get(run_id)
    workspace, task = run['workspace'], run['task']
    state = tasks.get(task, workspace=workspace)
    if not reason and run['role'] == 'work' and state['status'] == 'review':
        outcome, detail = 'review', 'moved the Task to Review'
    else:
        outcome = 'failed'
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
    return store.get(run_id)


def run(task: str, *, workspace: str | None = None, client: str = 'claude-code', minutes: float | None = None,
        on_start=None) -> dict:
    """Dispatch ``task`` now and wait for its session to end: the run's record."""
    started, proc = start(task, workspace=workspace, client=client)
    if on_start:
        on_start(started)
    return watch(started, proc, minutes=minutes)
