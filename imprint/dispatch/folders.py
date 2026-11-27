"""Where a project lives on this device (ADR 0220, "Projects").

A Task names its project, never a folder. The Dispatcher needs the folder:

1. the project's mapping in this device's project roots (written by ingest
   and ``imprint workspace map``), else
2. the folder of the most recent agent session in that project, named the
   way the Board names a session's project ([`server.py`](../server.py),
   ``_board_project``).

The folder must be in a git repository: Dispatched sessions work in a
worktree of it, on a branch made from the branch checked out there
(ADR 0223). A project with no such folder is reported with the reason,
never guessed.
"""

from __future__ import annotations

import os

from .. import agent_activity, workspace_policy
from ..cli_conversations import derive_project
from ..wsl_paths import windows_to_linux
from . import git

# How many recent session folders are looked at for a project with no mapping.
RECENT_FOLDERS = 200
OPEN_STATUSES = ('ready', 'in_progress', 'review')


def _session_project(directory: str) -> str:
    """The project the Board gives a session working in ``directory``: named
    from the folder as the session's client sees it, so a WSL session's
    folder is taken as its Linux path."""
    located = windows_to_linux(directory)
    if located:
        return derive_project(located[1]) or derive_project(directory)
    return derive_project(directory)


def _from_sessions(project: str) -> str | None:
    for directory in agent_activity.recent_directories(RECENT_FOLDERS):
        try:
            if _session_project(directory) == project and os.path.isdir(directory):
                return directory
        except (OSError, ValueError):
            continue
    return None


def _unavailable(project: str, reason: str, path: str | None = None, source: str | None = None) -> dict:
    return {'project': project, 'ok': False, 'path': path, 'source': source, 'reason': reason}


def resolve(project: str, workspace: str | None = None) -> dict:
    """Where ``project`` lives on this device.

    Available: ``{'project', 'ok': True, 'path', 'source', 'distro',
    'repository', 'subfolder', 'branch', 'commit'}``. ``path`` is the folder
    as this runtime opens it; ``source`` is ``mapping`` or ``session``;
    ``distro`` names the WSL distribution it lives in, if any, and
    ``repository`` is the repository's top folder as git there reports it;
    ``subfolder`` is the folder's place inside the repository (``''`` at
    its top); ``branch`` is the checked-out branch (``None`` on a detached
    HEAD) and ``commit`` the checked-out commit. Otherwise ``{'project',
    'ok': False, 'reason', …}``."""
    path, source = workspace_policy.project_root(project, workspace), 'mapping'
    if path is None:
        path, source = _from_sessions(project), 'session'
    if path is None:
        return _unavailable(project, f'No folder is known for {project} on this device: '
                                     f'map one with `imprint workspace map {project} <dir>`')
    if not os.path.isdir(path):
        return _unavailable(project, f'{path} is not a folder on this device', path, source)
    try:
        # One git call: a WSL folder costs a wsl.exe start for each.
        # --abbrev-ref applies to the revisions after it, so the full commit comes first.
        top, inside, commit, branch = git.run(
            path, 'rev-parse', '--show-toplevel', '--show-prefix', 'HEAD', '--abbrev-ref', 'HEAD').splitlines()
    except git.GitError as error:
        return _unavailable(project, f'{path} is not in a git repository that git can read here: {error}', path, source)
    except ValueError:
        return _unavailable(project, f'git did not report the repository of {path}', path, source)
    distro, _ = git.location(path)
    return {'project': project, 'ok': True, 'path': path, 'source': source, 'distro': distro,
            'repository': top, 'subfolder': inside.rstrip('/'),
            'branch': None if branch == 'HEAD' else branch, 'commit': commit}


def open_projects(workspace: str | None = None) -> list[dict]:
    """Each project with Tasks the Dispatcher could take (Ready, In progress
    or Review), and where it lives on this device."""
    from .. import tasks
    projects = sorted({state['project'] for state in tasks.listing(workspace=workspace)
                       if state['status'] in OPEN_STATUSES and state['project']})
    return [resolve(project, workspace) for project in projects]


def distributions(workspace: str | None = None) -> list[str]:
    """The WSL distributions this device's projects live in, as their
    mappings and recent sessions name them; only running ones, since
    looking into a stopped one starts it (ADR 0201)."""
    from ..wsl_paths import running_distributions
    places = set()
    for path in [*workspace_policy.mapped_roots(workspace), *agent_activity.recent_directories(RECENT_FOLDERS)]:
        located = windows_to_linux(path)
        if located:
            places.add(located[0])
    running = running_distributions() or []
    return sorted(place for place in places if place in running)
