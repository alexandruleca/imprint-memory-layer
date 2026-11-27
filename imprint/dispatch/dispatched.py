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


class ClaimLost(LookupError):
    """Another device's earlier claim on the Task won (ADR 0224)."""


LOST_CHECK = 10.0  # How often a running session's claim is checked, in seconds.


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


def handoff_lines(handoff: dict | None, project: str) -> list[str]:
    """Earlier work on the Task, from its Handoff: where the session continues."""
    if not handoff or not handoff.get('checkpoints'):
        return []
    lines = ['', 'Earlier work on this Task (its Handoff): continue from it.']
    previous = handoff.get('previous_holder')
    if previous:
        lines.append(f"- Previous holder: {tasks.label(previous)} on {previous.get('device') or 'a device'}")
    last = handoff.get('last_checkpoint') or {}
    if last.get('text'):
        lines.append(f"- Last checkpoint: {last['text']}")
    if last.get('next'):
        lines.append(f"- Its next step: {last['next']}")
    if handoff.get('branch') or handoff.get('commit'):
        lines.append('- Work: ' + ' @ '.join(p for p in (handoff.get('branch'), handoff.get('commit')) if p))
    if handoff.get('files'):
        lines.append('- Files: ' + ', '.join(handoff['files'][:30]))
    if handoff.get('conversation'):
        lines.append(f"- Its conversation: read_source(source=\"{handoff['conversation']}\", project=\"{project}\"), "
                     'if Imprint has learned it')
    return lines


def prompt(state: dict, *, workspace: str, worktree: dict, token: str, role: str = 'work',
           note: str = '', handoff: dict | None = None, base: str | None = None) -> str:
    """The Dispatched session's prompt: the Task, where to work, the claim
    token, its Handoff when it was worked on before, its client's ``note``
    about its own sandbox, and the instructions of ``imprint-dispatch``."""
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
        *([f'- Review against: {base} (`git diff {base}...HEAD` when it is a commit)',
           '- Your role: review. Follow the "Reviewing" part of the instructions below.'] if role == 'review' else []),
        *handoff_lines(handoff, state['project']),
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
          role: str = 'work', on_wait=None) -> tuple[dict, subprocess.Popen]:
    """Claim ``task`` and start a Dispatched session of ``client`` on it
    (by default, the client the Task is assigned to, else the default
    client, ADR 0222): (the run's record, the client's process)."""
    workspace = _workspace(workspace)
    state = tasks.get(task, workspace=workspace)
    if role == 'work' and state['status'] not in WORKABLE:
        raise ValueError(f"{state['id']} is {tasks.STATUS_LABELS.get(state['status'], state['status'])}: "
                         'a Dispatched session works on Backlog, Ready or Stale Tasks')
    if role == 'review' and state['status'] != 'review':
        raise ValueError(f"{state['id']} is not in Review: a Dispatched review checks finished work")
    folder = folders.resolve(state['project'], workspace)
    if not folder['ok']:
        raise LookupError(folder['reason'])
    from . import clients
    distro = folder.get('distro')
    where = f' in {distro}' if distro else ''
    if client is None:
        from . import guards
        choices = clients.allowed(where=distro)
        client = (guards.reviewer(state, choices, workspace) if role == 'review'
                  else clients.for_task(state, choices, tasks.device_name()))
        if client is None:
            raise LookupError(f"No client this device may start for {state['id']}{where}: "
                              '`imprint tasks dispatch status` says which are available')
    runner = runners.get(client)
    binary = clients.binary(runner, distro)  # before claiming: a missing client claims nothing
    if not binary:
        raise LookupError(f'{runner.label} was not found{where}: put {runner.binary} on PATH or set {runner.setting}')
    # A Task worked on before continues from its Handoff: on its own task
    # branch, at its last commit when that branch is not here yet (ADR 0223).
    handoff = tasks.handoff(state['id'], workspace=workspace) if state.get('checkpoints') else None
    start_at = None
    on_branch = (handoff or {}).get('branch') or ''
    if handoff and handoff.get('commit') and (role == 'review' or on_branch.startswith(worktrees.branch_prefix(state['id']))):
        # A review checks the work where it ends, on whatever branch it was done.
        from . import git
        start_at = handoff['commit'] if git.has_commit(folder['path'], handoff['commit']) else None
    worktree = worktrees.prepare(folder, state['id'], state['title'], start=start_at)
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
    claimed_at = time.time()
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
        # The claim must reach the paired devices before the client starts (ADR 0224).
        from . import claimcheck
        held, winner = claimcheck.confirm(state['id'], workspace, session_of(run_id), claimed_at, on_wait=on_wait)
        if not held:
            finish(run_id, None, lost=winner, started=False)
            raise ClaimLost(f"{state['id']} was claimed first by {winner}: no session started")
        base = None
        if role == 'review':
            from . import guards
            base = guards.review_base(state, workspace) or f"the merge base of HEAD and {folder.get('branch') or 'the main branch'}"
        text = prompt(claimed['task'], workspace=workspace, worktree=worktree, token=claimed['token'], role=role,
                      note=runner.note, handoff=handoff, base=base)
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
    except ClaimLost:
        raise
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
        self.renewed, self.reason, self.stopped, self.lost = time.monotonic(), None, False, None
        self.checked = time.monotonic()

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
            elif time.monotonic() - self.checked >= LOST_CHECK and self._claim_lost():
                self.stop(f'lost its claim to {self.lost}, whose claim was earlier, and was stopped', stopped=False)
            elif time.monotonic() - self.renewed >= RENEW_EVERY:
                tasks.renew(self.run['session'], pid=self.pid)
                self.renewed = time.monotonic()
            if self.running():
                return None
        code = self.proc.wait() if self.proc else None  # An adopted session's exit code is not known.
        return finish(self.run['id'], code, reason=self.reason, stopped=self.stopped, lost=self.lost)

    def _claim_lost(self) -> bool:
        """Whether another device's earlier claim took the Task from this session (ADR 0224)."""
        from . import claimcheck
        self.checked = time.monotonic()
        held, winner = claimcheck.ours(self.run['task'], self.run['workspace'],
                                       _our_sessions(self.run['task'], self.run['id'], self.run['workspace']))
        if not held:
            self.lost = winner
        return not held


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


def _lost(run: dict, winner: str, started: bool) -> str:
    """Record a run whose claim another device's earlier claim beat
    (ADR 0224): its worktree goes, its commits stay on the branch."""
    detail = (f'lost its claim to {winner}, whose claim was earlier, and was stopped' if started else
              f'was not started: {winner} claimed the Task first')
    if started:
        try:
            folder = folders.resolve(run['project'], run['workspace'])
            if folder['ok']:
                worktrees.remove(folder, run['task'])
        except Exception:  # A worktree still in use is removed when its Task ends (Dispatcher 7).
            pass
    text = f"The Dispatched session on this device {detail}."
    if started and run.get('branch'):
        text += f" Its commits, if any, stay on {run['branch']} here."
    tasks.comment(run['task'], text, actor=SYSTEM, workspace=run['workspace'])
    return detail


def finish(run_id: str, exit_code: int | None, *, reason: str | None = None, stopped: bool = False,
           lost: str | None = None, started: bool = True) -> dict:
    """Record how the run ended. A worker whose Task is in Review succeeded;
    any other end releases the claim this run holds, with a comment. A
    session stopped by the person or the server's shutdown is ``stopped``,
    one whose claim another device won is ``lost`` (ADR 0224), any other
    end ``failed`` (ADR 0220)."""
    run = store.get(run_id)
    if run['ended_at'] is not None:
        return run  # Already recorded, by whoever saw it end first.
    workspace, task = run['workspace'], run['task']
    if lost:
        detail = _lost(run, lost, started)
        store.update(run_id, ended_at=time.time(), exit_code=exit_code, outcome='lost', detail=detail)
        return store.get(run_id)
    if run.get('stop_reason'):
        reason, stopped = run['stop_reason'], True
    state = tasks.get(task, workspace=workspace)
    label = runners.RUNNERS[run['client']].label if run['client'] in runners.RUNNERS else run['client']
    if not reason and run['role'] == 'work' and state['status'] == 'review':
        outcome, detail = 'review', 'moved the Task to Review'
    elif not reason and run['role'] == 'review' and state['status'] in ('done', 'ready'):
        from . import guards
        if state['status'] == 'done':
            outcome, detail = 'passed', 'passed the review and moved the Task to Done'
            tasks.comment(task, f"A Dispatched review ({label}) passed this Task: {run['branch']} is ready to merge.",
                          to=['user'], actor=SYSTEM, workspace=workspace)
        else:
            outcome, detail = 'returned', 'found problems and sent the Task back to Ready'
            holder = state.get('holder') or (state.get('previous_holder') if state.get('stale') else None)
            if holder and holder['session'] in _our_sessions(task, run_id, workspace):
                tasks.release(task, actor=SYSTEM, workspace=workspace)
        store.update(run_id, ended_at=time.time(), exit_code=exit_code, outcome=outcome, detail=detail)
        guards.after_review(store.get(run_id), outcome)
        return store.get(run_id)
    else:
        outcome = 'stopped' if stopped else 'failed'
        detail = reason or (f'ended (exit code {exit_code}) without moving the Task to Review'
                            if run['role'] == 'work' else f'ended (exit code {exit_code}) without deciding the review')
        # A holder whose process ended is Stale at once, and the Board shows it as the previous holder.
        holder = state.get('holder') or (state.get('previous_holder') if state.get('stale') else None)
        released = False
        if holder and holder['session'] in _our_sessions(task, run_id, workspace) and \
                (state['status'] != 'review' or run['role'] == 'review'):
            tasks.release(task, actor=SYSTEM, workspace=workspace)  # a reviewer's release leaves it in Review
            released = True
        tail = log_tail(store.log_path(run_id))
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
        on_start=None, role: str = 'work') -> dict:
    """Dispatch ``task`` now, to work on it or (``role='review'``) to review
    it, and wait for its session to end: the run's record."""
    started, proc = start(task, workspace=workspace, client=client, role=role)
    if on_start:
        on_start(started)
    return watch(started, proc, minutes=minutes)
