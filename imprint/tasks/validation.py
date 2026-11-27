"""What a Task's fields may hold, checked when a change is made."""

from __future__ import annotations

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
