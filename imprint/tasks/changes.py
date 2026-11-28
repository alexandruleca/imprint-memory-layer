"""Changing a Task as the user or an agent: each change one event, refused when it breaks a rule."""

from __future__ import annotations

import json
import sqlite3
import uuid

from .actors import _actor
from .state import _set_changes
from .store import _append, _draw_id, _project, _state, _uid, _writing
from .validation import ASSIGNEES, CRITERION_CHARS, TAGS, TEXT_CHARS, TITLE_CHARS, _assignees, _criteria, _limit, _priority, _project_name, _redacted, status_ref, _tags, _text
from .view import _view


def _change(task: str, kind: str, data, actor: dict | None, workspace: str | None, check=None) -> dict:
    """Append one event to an existing Task and return its new state.
    ``check(conn, uid, state)`` refuses a change that breaks a rule; ``data``
    may be ``data(conn, uid)``, built inside the write."""
    actor = _actor(actor)
    with _writing(workspace) as conn:
        uid = _uid(conn, task)
        if check:
            check(conn, uid, _state(conn, uid))
        if callable(data):
            data = data(conn, uid)
        _append(conn, uid, kind, data, actor)
        _project(conn, uid)
        view = _view(conn)
    return view.public(view.by_uid[uid])


def _check_feature(conn: sqlite3.Connection, uid: str, feature_uid: str | None) -> None:
    if not feature_uid:
        return
    if feature_uid == uid:
        raise ValueError('A Task cannot be a Feature of itself')
    if _state(conn, feature_uid)['feature']:
        raise ValueError("That Task belongs to a Feature, so it cannot be one: Features do not nest")
    has_tasks = any(json.loads(row['state'])['feature'] == uid for row in conn.execute('SELECT state FROM tasks'))
    if has_tasks:
        raise ValueError('This Task is a Feature with Tasks of its own, so it cannot belong to a Feature')


def _check_blockers(conn: sqlite3.Connection, uid: str, blockers: list[str]) -> None:
    if uid in blockers:
        raise ValueError('A Task cannot block itself')
    edges = {json.loads(r['state'])['uid']: json.loads(r['state'])['blockers'] for r in conn.execute('SELECT state FROM tasks')}
    seen, stack = set(), list(blockers)
    while stack:  # Does any new blocker wait, directly or not, on this Task?
        current = stack.pop()
        if current == uid:
            raise ValueError('That would make a cycle of blockers')
        if current not in seen:
            seen.add(current)
            stack.extend(edges.get(current, []))


def create(title: str, *, project: str, description: str = '', status: str = 'backlog', priority: str = 'P2',
           acceptance=(), tags=(), assignees=(), feature: str | None = None, blockers=(),
           actor: dict | None = None, workspace: str | None = None) -> dict:
    """Put a new Task on the workspace's Board."""
    actor = _actor(actor)
    title = _text(title, 'title', TITLE_CHARS, required=True)
    project = _project_name(project)
    description = _text(description, 'description', TEXT_CHARS)
    criteria = _criteria(acceptance)
    status = status_ref(status)
    if status not in ('backlog', 'ready'):
        raise ValueError('a new task starts in backlog or ready')
    if status == 'ready' and not criteria:
        raise ValueError('A task moves to ready only with at least one acceptance criterion')
    texts = _redacted([title, description, *criteria])
    data = {'title': texts[0], 'description': texts[1], 'project': project, 'status': status,
            'priority': _priority(priority), 'acceptance': texts[2:], 'tags': _tags(tags),
            'assignees': _assignees(assignees)}
    with _writing(workspace) as conn:
        uid = uuid.uuid4().hex
        if feature:
            data['feature'] = _uid(conn, feature)
            _check_feature(conn, uid, data['feature'])
        if blockers:
            data['blockers'] = [_uid(conn, b) for b in blockers]
            _check_blockers(conn, uid, data['blockers'])
        data['id'] = _draw_id(conn)
        _append(conn, uid, 'created', data, actor)
        _project(conn, uid)
        view = _view(conn)
    return view.public(view.by_uid[uid])


_UNSET = object()


def edit(task: str, *, title=_UNSET, description=_UNSET, project=_UNSET, priority=_UNSET, acceptance=_UNSET,
         position=_UNSET, actor: dict | None = None, workspace: str | None = None) -> dict:
    """Replace some of a Task's fields; each field keeps its last edit."""
    fields = {}
    if title is not _UNSET:
        fields['title'] = _redacted([_text(title, 'title', TITLE_CHARS, required=True)])[0]
    if description is not _UNSET:
        fields['description'] = _redacted([_text(description, 'description', TEXT_CHARS)])[0]
    if project is not _UNSET:
        fields['project'] = _project_name(project)
    if priority is not _UNSET:
        fields['priority'] = _priority(priority)
    if acceptance is not _UNSET:
        fields['acceptance'] = _redacted(_criteria(acceptance))
    if position is not _UNSET:
        fields['position'] = None if position is None else float(position)
    if not fields:
        raise ValueError('Nothing to change: pass a field to edit')

    def check(conn, uid, state):
        if 'acceptance' in fields and not fields['acceptance'] and state['status'] == 'ready':
            raise ValueError('A Ready task keeps at least one acceptance criterion')
    return _change(task, 'edited', fields, actor, workspace, check)


def append(task: str, text: str, *, actor: dict | None = None, workspace: str | None = None) -> dict:
    """Add text to the end of a Task's description. Appends from several
    agents at once all stay (ADR 0209)."""
    text = _redacted([_text(text, 'text', TEXT_CHARS, required=True)])[0]
    return _change(task, 'appended', {'text': text}, actor, workspace)


def move(task: str, status: str, *, position: float | None = None, actor: dict | None = None,
         workspace: str | None = None) -> dict:
    """Move a Task to another status, and optionally to a position in it."""
    status = status_ref(status)
    actor = _actor(actor)
    data = {'status': status}
    if position is not None:
        data['position'] = float(position)

    def check(conn, uid, state):
        if status == 'ready' and not state['acceptance']:
            raise ValueError('A task moves to ready only with at least one acceptance criterion')
        if status == 'done' and actor['kind'] == 'agent':
            # A Dispatched reviewer holds a Task in Review while it checks it (ADR 0221).
            if (state['holder'] or {}).get('session') == actor['session'] and state['status'] != 'review':
                raise PermissionError('Move it to Review first: another session or the user marks it Done')
            if actor['session'] in (state['worked_by'], state.get('submitted_by')):
                raise PermissionError('The session that did the work does not mark it Done: '
                                      'another session or the user does')
    return _change(task, 'moved', data, actor, workspace, check)


def tag(task: str, *, add=(), remove=(), actor: dict | None = None, workspace: str | None = None) -> dict:
    data = {'add': _tags(add), 'remove': _tags(remove)}
    if not data['add'] and not data['remove']:
        raise ValueError('Name tags to add or remove')
    return _change(task, 'tagged', data, actor, workspace,
                   lambda conn, uid, state: _limit(len(_set_changes(state['tags'], data)), TAGS, 'tags'))


def assign(task: str, *, add=(), remove=(), actor: dict | None = None, workspace: str | None = None) -> dict:
    data = {'add': _assignees(add), 'remove': _assignees(remove)}
    if not data['add'] and not data['remove']:
        raise ValueError('Name assignees to add or remove')
    return _change(task, 'assigned', data, actor, workspace,
                   lambda conn, uid, state: _limit(len(_set_changes(state['assignees'], data)), ASSIGNEES, 'assignees'))


def link(task: str, *, feature=_UNSET, add_blockers=(), remove_blockers=(), actor: dict | None = None,
         workspace: str | None = None) -> dict:
    """Put a Task in a Feature (``''`` takes it out) and add or remove the
    Tasks that block it."""
    if feature is _UNSET and not add_blockers and not remove_blockers:
        raise ValueError('Nothing to link: pass a feature or blockers')

    def links(conn, uid):
        data = {}
        if feature is not _UNSET:
            data['feature'] = _uid(conn, feature) if feature else None
            _check_feature(conn, uid, data['feature'])
        if add_blockers:
            data['add_blockers'] = [_uid(conn, b) for b in add_blockers]
            _check_blockers(conn, uid, data['add_blockers'])
        if remove_blockers:
            data['remove_blockers'] = [_uid(conn, b) for b in remove_blockers]
        return data
    return _change(task, 'linked', links, actor, workspace)


def block(task: str, reason: str, *, actor: dict | None = None, workspace: str | None = None) -> dict:
    """Mark a Task Blocked; it keeps its status."""
    reason = _redacted([_text(reason, 'reason', CRITERION_CHARS)])[0]
    if not reason:
        raise ValueError('Say why it is blocked: a reason is required')
    return _change(task, 'blocked', {'reason': reason}, actor, workspace)


def unblock(task: str, *, actor: dict | None = None, workspace: str | None = None) -> dict:
    return _change(task, 'unblocked', {}, actor, workspace)


def delete(task: str, *, actor: dict | None = None, workspace: str | None = None) -> dict:
    """Delete a Task: only the user may. Its events keep their ids and kinds
    and lose their content; the deletion stays as a tombstone (ADR 0209)."""
    actor = _actor(actor)
    if actor['kind'] != 'user':
        raise PermissionError('Only the user deletes tasks: cancel it instead')
    with _writing(workspace) as conn:
        uid = _uid(conn, task)
        state = _state(conn, uid)
        conn.execute("UPDATE events SET data = '{}' WHERE task = ?", (uid,))
        _append(conn, uid, 'deleted', {'id': state['id']}, actor)
        _project(conn, uid)
    return {'id': state['id'], 'deleted': True}
