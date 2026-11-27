"""Each Dispatched session's worktree (ADR 0223).

A Task's work happens in a git worktree that Imprint makes outside the
project, at ``~/.imprint/worktrees/<project>/<task id>``, on the Task's
branch ``task/<id>-<slug>``. The first dispatch makes the branch from the
branch checked out in the project's folder; later dispatches of the same
Task continue on it. Dispatched sessions commit there and never push,
merge, or change the project's own checkout.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import git

SLUG_WORDS = 6
SLUG_CHARS = 40


def slug(title: str) -> str:
    words = re.findall(r'[a-z0-9]+', (title or '').lower())
    return '-'.join(words[:SLUG_WORDS])[:SLUG_CHARS].strip('-') or 'work'


def branch_prefix(task_id: str) -> str:
    """``task/<id>-``: the id without its ``T-``, in lower case."""
    short = task_id[2:] if task_id.upper().startswith('T-') else task_id
    return f'task/{short.lower()}-'


def branch_name(task_id: str, title: str) -> str:
    return branch_prefix(task_id) + slug(title)


def _folder_name(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]+', '-', name).strip('-.') or 'project'


def location(folder: dict, task_id: str) -> str:
    """Where the Task's worktree goes, as git on the project's system sees it."""
    if folder.get('distro'):
        raise NotImplementedError('Worktrees of projects in WSL come with Dispatcher 6')
    return os.path.join(str(Path.home()), '.imprint', 'worktrees', _folder_name(folder['project']), task_id)


def _worktrees(folder: dict) -> dict[str, str]:
    """The repository's worktrees: path -> checked-out branch ('' when detached)."""
    found, path = {}, None
    for line in git.run(folder['path'], 'worktree', 'list', '--porcelain').splitlines():
        if line.startswith('worktree '):
            path = line[len('worktree '):]
            found[path] = ''
        elif line.startswith('branch ') and path is not None:
            found[path] = line[len('branch refs/heads/'):]
    return found


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _task_branch(folder: dict, task_id: str) -> str | None:
    """A branch an earlier dispatch made for this Task, whatever its title was then."""
    prefix = branch_prefix(task_id)
    listed = git.run(folder['path'], 'for-each-ref', '--format=%(refname:short)', f'refs/heads/{prefix}*')
    return next((line for line in listed.splitlines() if line.startswith(prefix)), None)


def prepare(folder: dict, task_id: str, title: str) -> dict:
    """The Task's worktree, made or reused: ``{'path', 'cwd', 'branch',
    'base', 'base_commit', 'created'}``. ``cwd`` is the project's folder
    inside the worktree, where the session starts."""
    path = location(folder, task_id)
    base = folder.get('branch') or folder['commit']
    existing = next((branch for listed, branch in _worktrees(folder).items() if _same(listed, path)), None)
    if existing is not None:
        branch, created = existing or _task_branch(folder, task_id) or branch_name(task_id, title), False
    else:
        if os.path.exists(path):
            raise FileExistsError(f'{path} exists and is not a worktree of {folder["repository"]}: move it away')
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        branch = _task_branch(folder, task_id)
        if branch:
            git.run(folder['path'], 'worktree', 'add', path, branch)
        else:
            branch = branch_name(task_id, title)
            git.run(folder['path'], 'worktree', 'add', '-b', branch, path, base)
        created = True
    cwd = os.path.join(path, *folder['subfolder'].split('/')) if folder.get('subfolder') else path
    return {'path': path, 'cwd': cwd, 'branch': branch, 'base': base, 'base_commit': folder['commit'],
            'created': created}


def remove(folder: dict, task_id: str) -> bool:
    """Remove the Task's worktree, keeping its branch and commits; False when it had none."""
    path = location(folder, task_id)
    if not any(_same(listed, path) for listed in _worktrees(folder)):
        return False
    git.run(folder['path'], 'worktree', 'remove', '--force', path)
    return True
