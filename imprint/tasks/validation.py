"""What a Task's fields may hold, checked when a change is made."""

from __future__ import annotations

import os
import re

from .store import PRIORITIES, STATUSES


TITLE_CHARS = 200


TEXT_CHARS = 20_000


CRITERION_CHARS = 500


CRITERIA = 50


TAG_CHARS = 40


TAGS = 30


ASSIGNEES = 20


_ASSIGNEE = re.compile(r'^(?:[a-z0-9][a-z0-9._-]{0,63}(?:@[a-z0-9][a-z0-9._-]{0,63})?|@[a-z0-9][a-z0-9._-]{0,63})$')


def _text(value, field: str, limit: int, required: bool = False) -> str:
    text = (value or '').strip() if isinstance(value, str) or value is None else None
    if text is None:
        raise ValueError(f'{field} must be text')
    if required and not text:
        raise ValueError(f'A task needs a {field}')
    if len(text) > limit:
        raise ValueError(f'{field} is longer than {limit} characters')
    return text


# A folder rather than a name: an absolute POSIX path, a home-relative one, a
# UNC share (\\wsl.localhost\...) or a Windows drive path. A name such as
# github.com/owner/repo is not one.
_FOLDER = re.compile(r'^(?:[/~]|\\\\|[A-Za-z]:[\\/])')


def is_folder(value: str) -> bool:
    return bool(_FOLDER.match(value or ''))


def project_filter(value: str | None) -> str | None:
    """A project to filter by: a folder means the project it holds."""
    if value and is_folder(value.strip()):
        return folder_project(value.strip()) or value.strip()
    return value


def folder_project(folder: str) -> str:
    """The project a folder holds, named as the Board names a session's
    project (ADR 0211): ``/home/alex/code/imprint`` gives ``imprint``. Empty
    when this device cannot name it. A folder in a WSL distribution is named
    by its Linux path, so naming it never reads the share or starts the
    distribution."""
    from ..cli_conversations import derive_project
    from ..wsl_paths import client_path, windows_to_linux
    in_wsl = windows_to_linux(folder)
    try:
        return derive_project(in_wsl[1] if in_wsl else client_path(os.path.expanduser(folder)))
    except OSError:  # a share or drive this device cannot read
        return ''


def _project_name(value) -> str:
    """A Task's project, by name. A folder given instead, as an agent passing
    its working directory does, becomes the name of the project it holds
    (ADR 0226); one that names no project is refused."""
    project = _text(value, 'project', TITLE_CHARS, required=True)
    if not is_folder(project):
        return project
    named = folder_project(project)
    if not named:
        raise ValueError(f'{project} is a folder, not a project: name the project instead')
    return named


def _priority(value) -> str:
    priority = str(value or 'P2').strip().upper()
    if priority not in PRIORITIES:
        raise ValueError(f'priority must be one of {", ".join(PRIORITIES)}')
    return priority


STATUS_LABELS = {'backlog': 'Backlog', 'ready': 'Ready', 'in_progress': 'In progress', 'review': 'Review',
                 'done': 'Done', 'cancelled': 'Cancelled'}


def status_ref(value) -> str:
    """A status in its stored form, from ``In progress``, ``in-progress`` or ``in_progress``."""
    status = str(value or '').strip().lower().replace('-', '_').replace(' ', '_')
    if status not in STATUSES:
        raise ValueError(f'status must be one of {", ".join(STATUS_LABELS[s] for s in STATUSES)}')
    return status


def _criteria(values) -> list[str]:
    criteria = [_text(v, 'acceptance criterion', CRITERION_CHARS) for v in (values or [])]
    criteria = [c for c in criteria if c]
    if len(criteria) > CRITERIA:
        raise ValueError(f'a task has at most {CRITERIA} acceptance criteria')
    return criteria


def _tags(values) -> list[str]:
    tags = []
    for value in values or []:
        tag = _text(value, 'tag', TAG_CHARS).lower()
        if tag and tag not in tags:
            tags.append(tag)
    if len(tags) > TAGS:
        raise ValueError(f'a task has at most {TAGS} tags')
    return tags


def _assignees(values) -> list[str]:
    """Each one ``user``, a client (``codex``), a device (``@bedrock``) or a
    client on a device (``codex@bedrock``)."""
    assignees = []
    for value in values or []:
        assignee = str(value or '').strip().lower()
        if not _ASSIGNEE.match(assignee):
            raise ValueError(f'{value!r} is not an assignee: use "user", a client ("codex"), '
                             'a device ("@bedrock") or a client on a device ("codex@bedrock")')
        if assignee not in assignees:
            assignees.append(assignee)
    if len(assignees) > ASSIGNEES:
        raise ValueError(f'a task has at most {ASSIGNEES} assignees')
    return assignees


def _redacted(texts: list[str]) -> list[str]:
    from ..redaction import redact_all
    return redact_all(texts)


def _limit(count: int, limit: int, what: str) -> None:
    if count > limit:
        raise ValueError(f'a task has at most {limit} {what}')
