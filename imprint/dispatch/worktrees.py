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
import posixpath
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
    """Where the Task's worktree goes, as git on the project's system sees
    it: under the home of the WSL distribution a project lives in, else
    under this system's home."""
    if folder.get('distro'):
        from . import wsl
        return posixpath.join(wsl.worktree_root(folder['distro']), _folder_name(folder['project']), task_id)
    return os.path.join(str(Path.home()), '.imprint', 'worktrees', _folder_name(folder['project']), task_id)


def _exists(folder: dict, path: str) -> bool:
    if folder.get('distro'):
        from . import wsl
        return os.path.exists(wsl.native(path, folder['distro']))
    return os.path.exists(path)


def _make_parent(folder: dict, path: str) -> None:
    if folder.get('distro'):
        from . import wsl
        wsl.run(folder['distro'], ['mkdir', '-p', posixpath.dirname(path)])
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)


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


def _same(folder: dict, a: str, b: str) -> bool:
    """Whether two paths name one worktree. git lists a worktree by its real
    path, so a local folder reached through a symlink (macOS's /var is
    /private/var) compares by real path; a WSL distribution's paths are
    compared as written, since this runtime cannot resolve them."""
    if folder.get('distro'):
        return posixpath.normpath(a) == posixpath.normpath(b)
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _task_branch(folder: dict, task_id: str) -> str | None:
    """A branch an earlier dispatch made for this Task, whatever its title was then."""
    prefix = branch_prefix(task_id)
    listed = git.run(folder['path'], 'for-each-ref', '--format=%(refname:short)', f'refs/heads/{prefix}*')
    return next((line for line in listed.splitlines() if line.startswith(prefix)), None)


def prepare(folder: dict, task_id: str, title: str, *, start: str | None = None) -> dict:
    """The Task's worktree, made or reused: ``{'path', 'cwd', 'branch',
    'base', 'base_commit', 'tip', 'fresh', 'git_dir', 'created'}``.
    ``cwd`` is the project's folder inside the worktree, where the session
    starts. A new branch starts at ``start`` (a takeover's last commit on
    its task branch, made on another device) when given, else at the branch
    checked out in the project's folder: then it is ``fresh``, and
    ``base_commit`` is where it was made from. ``tip`` is the branch's last
    commit now."""
    path = location(folder, task_id)
    base = start or folder.get('branch') or folder['commit']
    existing = next((branch for listed, branch in _worktrees(folder).items() if _same(folder, listed, path)), None)
    fresh = False
    if existing is not None:
        branch, created = existing or _task_branch(folder, task_id) or branch_name(task_id, title), False
    else:
        if _exists(folder, path):
            raise FileExistsError(f'{path} exists and is not a worktree of {folder["repository"]}: move it away')
        _make_parent(folder, path)
        branch = _task_branch(folder, task_id)
        if branch:
            git.run(folder['path'], 'worktree', 'add', path, branch)
        else:
            branch = branch_name(task_id, title)
            git.run(folder['path'], 'worktree', 'add', '-b', branch, path, base)
            fresh = start is None
        created = True
    join = posixpath.join if folder.get('distro') else os.path.join
    cwd = join(path, *folder['subfolder'].split('/')) if folder.get('subfolder') else path
    # The folder a worktree's commits are written to: the repository's own git folder.
    where = wsl_native(folder, path)
    git_dir = git.run(where, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    if not folder.get('distro'):
        git_dir = os.path.normpath(git_dir)  # Windows git answers with forward slashes; clients get the native form
    tip = git.run(where, 'rev-parse', 'HEAD')
    return {'path': path, 'cwd': cwd, 'branch': branch, 'base': base, 'base_commit': start or folder['commit'],
            'tip': tip, 'fresh': fresh, 'git_dir': git_dir, 'created': created}


def scratch_location(folder: dict, task_id: str) -> str:
    """Where a refinement's throwaway copy goes: beside the Task's own worktree's place."""
    return location(folder, task_id) + '.refine'


def scratch(folder: dict, task_id: str) -> dict:
    """A throwaway copy of the project at its checked-out commit, for a
    refinement to read (ADR 0230): a detached worktree with no branch,
    made afresh each time; ``remove_scratch`` takes it away. Returns the
    keys ``prepare`` returns, ``branch`` None and ``scratch`` True."""
    path = scratch_location(folder, task_id)
    if any(_same(folder, listed, path) for listed in _worktrees(folder)):
        git.run(folder['path'], 'worktree', 'remove', '--force', path)
    elif _exists(folder, path):
        raise FileExistsError(f'{path} exists and is not a worktree of {folder["repository"]}: move it away')
    _make_parent(folder, path)
    git.run(folder['path'], 'worktree', 'add', '--detach', path, folder['commit'])
    join = posixpath.join if folder.get('distro') else os.path.join
    cwd = join(path, *folder['subfolder'].split('/')) if folder.get('subfolder') else path
    git_dir = git.run(wsl_native(folder, path), 'rev-parse', '--path-format=absolute', '--git-common-dir')
    if not folder.get('distro'):
        git_dir = os.path.normpath(git_dir)
    return {'path': path, 'cwd': cwd, 'branch': None, 'base': folder.get('branch') or folder['commit'],
            'base_commit': folder['commit'], 'tip': folder['commit'], 'fresh': False, 'git_dir': git_dir,
            'created': True, 'scratch': True}


def remove_scratch(folder: dict, task_id: str) -> bool:
    """Remove a refinement's throwaway copy; False when there was none."""
    path = scratch_location(folder, task_id)
    if not any(_same(folder, listed, path) for listed in _worktrees(folder)):
        return False
    git.run(folder['path'], 'worktree', 'remove', '--force', path)
    return True


def wsl_native(folder: dict, path: str) -> str:
    """A worktree path as this runtime reaches it, for running git there."""
    if folder.get('distro'):
        from . import wsl
        return wsl.native(path, folder['distro'])
    return path


def remove(folder: dict, task_id: str) -> bool:
    """Remove the Task's worktree, keeping its branch and commits; False when it had none."""
    path = location(folder, task_id)
    if not any(_same(folder, listed, path) for listed in _worktrees(folder)):
        return False
    git.run(folder['path'], 'worktree', 'remove', '--force', path)
    return True
