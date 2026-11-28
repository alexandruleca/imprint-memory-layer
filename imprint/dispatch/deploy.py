"""Deploying the Tasks a Dispatched review passed (ADR 0239).

On a device whose Deploy role is on, Imprint's own git code merges the
branch of each such Task into the branch its work started from and pushes
it to that branch's remote. No client session is started, so a deploy
counts toward neither ``dispatch.concurrency`` nor ``dispatch.daily_limit``.

The merge happens in a throwaway detached worktree on ``<remote>/<base>``:
a fast-forward when the base has not moved, otherwise a merge commit made
with the repository's own git identity. The push is never forced, and the
person's checkout and local branches are never touched. A conflict sends
the Task back to Ready for a worker to resolve; a second one hands it to
the person. A push the remote rejects because the base moved meanwhile is
tried again on the next pass.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
import uuid

from .. import tasks
from ..tasks.actors import SYSTEM
from . import folders, git, guards, store, worktrees

CONFLICTS_TO_PERSON = 2
NO_REMOTE = 'cannot deploy it: its base branch tracks no remote here'
DEFAULT_REFUSED = "will not deploy it: its base is the remote's default branch"
NOT_HERE = 'cannot deploy it: its last commit is not in this copy of the repository'
_REJECTED = ('[rejected]', 'non-fast-forward', 'fetch first', 'failed to update ref', 'cannot lock ref')


def _tag(event: dict) -> dict | None:
    tag = event['data'].get('dispatch') if event['kind'] == 'comment' and event['actor'] == 'system' else None
    return tag if isinstance(tag, dict) else None


def waiting(history: list[dict]) -> bool:
    """Whether a Done Task waits to be deployed: a Dispatched review passed
    it after its last move to Done, and no deploy came since. A Task the
    person moved to Done has no passing review after that move."""
    done = [i for i, e in enumerate(history) if e['kind'] == 'moved' and e['data'].get('status') == 'done']
    if not done:
        return False
    since = [_tag(e) for e in history[done[-1]:]]
    passed = any(t and t.get('role') == 'review' and t.get('outcome') == 'passed' for t in since)
    deployed = any(t and t.get('role') == 'deploy' and t.get('outcome') == 'deployed' for t in since)
    return passed and not deployed


def candidates(workspace: str) -> list[dict]:
    """The workspace's Done Tasks waiting to be deployed, oldest first."""
    found = []
    for state in tasks.listing(status='done', workspace=workspace):
        try:
            if waiting(tasks.history(state['id'], workspace=workspace)):
                found.append(state)
        except LookupError:
            continue  # deleted meanwhile
    return sorted(found, key=lambda s: s['status_at'])


def _allowed(base: str, default: str | None) -> bool:
    """Deploy pushes to the remote's default branch only when the person lists it (ADR 0239)."""
    from ..config_schema import resolve
    listed = {b.strip() for b in str(resolve('dispatch.deploy_branches')[0] or '').split(',') if b.strip()}
    if base in listed:
        return True
    if default is not None:
        return base != default
    return base not in ('main', 'master')  # the default is unknown: the usual defaults wait for the list


def _say_once(task: str, workspace: str, marker: str, text: str) -> None:
    """Comment once to the person why a Task waits, until the reason changes."""
    said = any(e['kind'] == 'comment' and e['actor'] == 'system' and marker in e['data'].get('text', '')
               for e in tasks.history(task, workspace=workspace))
    if not said:
        tasks.comment(task, text, to=['user'], actor=SYSTEM, workspace=workspace)


def _note(task: str, workspace: str, run_id: str, outcome: str, text: str, *, to=()) -> None:
    tasks.comment(task, text, to=list(to), actor=SYSTEM, workspace=workspace,
                  dispatch={'run': run_id, 'role': 'deploy', 'outcome': outcome, 'started': False})


def _record(run_id: str, state: dict, workspace: str, started: float, outcome: str, detail: str,
            branch: str | None, base: str | None) -> dict:
    """A deploy in the device's run record, ended as it is written: it holds
    no session and counts toward neither concurrency nor the daily limit;
    a failed one counts toward ``dispatch.failure_limit``."""
    return store.add({'id': run_id, 'workspace': workspace, 'task': state['id'], 'title': state['title'],
                      'project': state['project'], 'client': 'imprint', 'role': 'deploy', 'session': None,
                      'started_at': started, 'ended_at': time.time(), 'outcome': outcome, 'detail': detail,
                      'pid': None, 'branch': branch, 'base': base})


def _worktree(folder: dict, task_id: str) -> str:
    return worktrees.location(folder, task_id) + '.deploy'


def _remove(folder: dict, path: str) -> None:
    try:
        git.run(folder['path'], 'worktree', 'remove', '--force', path)
    except git.GitError:
        native = worktrees.wsl_native(folder, path)
        if os.path.isdir(native):
            shutil.rmtree(native, ignore_errors=True)
        try:
            git.run(folder['path'], 'worktree', 'prune')
        except git.GitError:
            pass


def deploy(state: dict, workspace: str) -> dict | None:
    """Deploy one Task: its run record, or None when it was left for now
    (no folder here, nowhere to push, a push to retry on the next pass)."""
    folder = folders.resolve(state['project'], workspace)
    if not folder.get('ok'):
        return None
    task, path = state['id'], folder['path']
    handoff = tasks.handoff(task, workspace=workspace)
    branch, commit = handoff.get('branch'), handoff.get('commit')
    base = guards.recorded_base(task, workspace) or folder.get('branch')
    if not commit or not base:
        return None
    remote = guards.remote_of(folder, base)
    if not remote:
        _say_once(task, workspace, NO_REMOTE, f'The Dispatcher on this device {NO_REMOTE} ({base}). Merge it yourself, '
                  'or give the branch an upstream.')
        return None
    started, run_id = time.time(), uuid.uuid4().hex[:12]
    try:
        if not _allowed(base, git.default_branch(path, remote)):
            _say_once(task, workspace, DEFAULT_REFUSED, f'The Dispatcher on this device {DEFAULT_REFUSED} ({remote}/{base}). '
                      'List it in dispatch.deploy_branches to let deploy push there, or merge it yourself.')
            return None
        git.fetch_branch(path, remote, base)
        if branch and branch != base:
            guards.fetch_work(task, folder, workspace, handoff)
        if not git.has_commit(path, commit):
            _say_once(task, workspace, NOT_HERE, f'The Dispatcher on this device {NOT_HERE} ({commit[:12]} on '
                      f'{branch or "its branch"}). Push it, and deploy runs on the next pass.')
            return None
        target = f'refs/remotes/{remote}/{base}'
        if git.is_ancestor(path, commit, target):
            how, pushed = f'it was already in {remote}/{base}', commit
        else:
            outcome = _merge_and_push(folder, task, branch or commit[:12], commit, remote, base, target)
            if outcome is None:
                return None  # the base moved meanwhile: the next pass tries again
            if outcome[0] == 'conflict':
                return _conflict(state, workspace, run_id, started, branch, base, remote, outcome[1])
            how, pushed = outcome[1], outcome[2]
    except git.GitError as error:
        detail = f'could not deploy to {remote}/{base}: {error}'
        run = _record(run_id, state, workspace, started, 'failed', detail, branch, base)
        print(f'[imprint] dispatcher {detail} ({task})', file=sys.stderr, flush=True)
        guards.switch_off_after_failures(run)
        return run
    if branch and branch.startswith('task/') and branch != base:
        git.result(path, 'push', '--quiet', remote, '--delete', branch, timeout=git.NETWORK_TIMEOUT, network=True)
    _note(task, workspace, run_id, 'deployed',
          f'Deployed {commit[:12]} to {remote}/{base}: {how}' + (f' ({pushed[:12]}).' if pushed != commit else '.'))
    return _record(run_id, state, workspace, started, 'deployed', f'{how}; {remote}/{base} at {pushed[:12]}',
                   branch, base)


def _merge_and_push(folder: dict, task: str, branch: str, commit: str, remote: str, base: str,
                    target: str) -> tuple | None:
    """Merge ``commit`` onto ``<remote>/<base>`` in a throwaway worktree and
    push it: ('pushed', how, commit pushed), ('conflict', files), or None
    when the remote refused the push because the base moved meanwhile."""
    path = folder['path']
    worktree = _worktree(folder, task)
    if worktrees._exists(folder, worktree):
        _remove(folder, worktree)
    worktrees._make_parent(folder, worktree)
    git.run(path, 'worktree', 'add', '--detach', worktree, target)
    where = worktrees.wsl_native(folder, worktree)
    try:
        if git.is_ancestor(path, target, commit):
            how, pushed = f'a fast-forward of {remote}/{base}', commit
        else:
            code, _, err = git.result(where, 'merge', '--no-ff', '--no-edit', '-m', f'Merge {branch} ({task})', commit)
            if code:
                _, conflicted, _ = git.result(where, 'diff', '--name-only', '--diff-filter=U')
                git.result(where, 'merge', '--abort')
                if conflicted:
                    return ('conflict', conflicted.split())
                raise git.GitError((err or f'git merge exited with {code}').splitlines()[-1])
            how, pushed = f'merged into {remote}/{base}', git.run(where, 'rev-parse', 'HEAD')
        code, _, err = git.result(path, 'push', '--quiet', remote, f'{pushed}:refs/heads/{base}',
                                  timeout=git.NETWORK_TIMEOUT, network=True)
        if code:
            if any(word in err for word in _REJECTED):
                return None
            raise git.GitError((err or f'git push exited with {code}').splitlines()[-1])
        return ('pushed', how, pushed)
    finally:
        _remove(folder, worktree)


def _conflict(state: dict, workspace: str, run_id: str, started: float, branch: str | None, base: str,
              remote: str, files: list[str]) -> dict:
    """A conflict sends the Task back to Ready for a worker to resolve; the
    second, counted on every paired device, hands it to the person."""
    task = state['id']
    shown = ', '.join(files[:8]) + (f' and {len(files) - 8} more' if len(files) > 8 else '')
    tasks.move(task, 'ready', actor=SYSTEM, workspace=workspace)
    _note(task, workspace, run_id, 'conflict',
          f'Imprint could not deploy this Task: {branch or "its branch"} conflicts with {remote}/{base} in {shown}. '
          f'Merge {remote}/{base} into {branch or "the branch"}, resolve the conflicts, and move it to Review again.')
    run = _record(run_id, state, workspace, started, 'conflict', f'conflicts with {remote}/{base} in {shown}',
                  branch, base)
    conflicts = sum(1 for end in guards.run_ends(task, workspace).values()
                    if end['role'] == 'deploy' and end['outcome'] == 'conflict')
    if conflicts >= CONFLICTS_TO_PERSON and 'user' not in tasks.get(task, workspace=workspace)['assignees']:
        tasks.assign(task, add=['user'], actor=SYSTEM, workspace=workspace)
        tasks.comment(task, f'Deploying this Task met conflicts {conflicts} times, so the Dispatcher leaves it to you. '
                      'Unassign yourself to let agents take it again.', to=['user'], actor=SYSTEM, workspace=workspace)
    return run
