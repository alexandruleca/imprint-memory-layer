"""The Board: each workspace's Tasks, kept as a log of events (ADRs 0208, 0209).

Every change to a Task is an event that is never changed afterwards: what
changed, the device and agent session that changed it, and a hybrid logical
clock timestamp. A Task's current state is computed from its events in clock
order, then event id, so every device that holds the same events computes the
same state. The computed state is kept beside the events as a projection that
any write to the Task rebuilds from them.

The store is per workspace (``data/tasks.sqlite3`` for the default one,
``data/tasks_<name>.sqlite3`` for others) and shared by every process of the
installation: the MCP servers of all connected clients, the CLI and the
dashboard. Writes take SQLite's write lock for the clock tick, the event and
the projection together.

Text is redacted before it is stored (ADR 0060).
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import config

STATUSES = ('backlog', 'ready', 'in_progress', 'review', 'done', 'cancelled')
PRIORITIES = ('P0', 'P1', 'P2', 'P3')
# Crockford's base32 in lower case: no i, l, o or u to misread.
ID_ALPHABET = '0123456789abcdefghjkmnpqrstvwxyz'
ID_LENGTH = 5

TITLE_CHARS = 200
TEXT_CHARS = 20_000
CRITERION_CHARS = 500
CRITERIA = 50
TAG_CHARS = 40
TAGS = 30

USER = {'kind': 'user'}
ARCHIVE_DAYS = 14
ASSIGNEES = 20

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    task        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    data        TEXT NOT NULL,
    hlc         TEXT NOT NULL,
    device      TEXT NOT NULL,
    actor       TEXT NOT NULL,
    session     TEXT,
    agent       TEXT,
    client      TEXT,
    recorded_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_task ON events (task, hlc, id);
CREATE TABLE IF NOT EXISTS tasks (
    uid   TEXT PRIMARY KEY,
    id    TEXT NOT NULL,
    state TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_by_id ON tasks (id);
CREATE TABLE IF NOT EXISTS clock (
    one     INTEGER PRIMARY KEY CHECK (one = 1),
    wall    INTEGER NOT NULL,
    counter INTEGER NOT NULL
);
"""


# ── Who acts ─────────────────────────────────────────────────────

def _device() -> str:
    try:
        from .device import current
        return current()['id']
    except (OSError, ValueError):
        return ''


def agent(session: str, name: str | None = None, client: str | None = None, device: str | None = None) -> dict:
    """The actor for a change an agent session makes."""
    actor = {'kind': 'agent', 'session': session, 'name': name, 'client': client}
    if device:
        actor['device'] = device
    return actor


def _actor(actor: dict | None) -> dict:
    actor = dict(actor or USER)
    if actor.get('kind') not in ('user', 'agent'):
        raise ValueError('actor must be the user or an agent')
    if actor['kind'] == 'agent' and not actor.get('session'):
        raise ValueError('an agent actor needs its session')
    actor.setdefault('device', _device())
    return actor


def _who(event: dict) -> dict:
    if event['actor'] == 'user':
        return {'kind': 'user', 'device': event['device']}
    return {'kind': 'agent', 'session': event['session'], 'name': event['agent'],
            'client': event['client'], 'device': event['device']}


# ── Store ────────────────────────────────────────────────────────

def _workspace(workspace: str | None) -> str:
    name = workspace or config.get_active_workspace()
    error = config.validate_workspace_name(name)
    if error:
        raise ValueError(f'Invalid workspace {name!r}: {error}')
    if name not in config.get_known_workspaces():
        raise ValueError(f'Unknown workspace {name!r}')
    return name


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=10000')
    conn.executescript(_SCHEMA)
    return conn


@contextmanager
def _reading(workspace: str | None):
    """A connection to the workspace's store, or None when it has none yet:
    reading an empty Board makes no file."""
    path = config.tasks_db_path(_workspace(workspace))
    if not path.exists():
        yield None
        return
    conn = _open(path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _writing(workspace: str | None):
    conn = _open(config.tasks_db_path(_workspace(workspace)))
    try:
        conn.execute('BEGIN IMMEDIATE')
        try:
            yield conn
        except BaseException:
            conn.execute('ROLLBACK')
            raise
        conn.execute('COMMIT')
    finally:
        conn.close()


def _tick(conn: sqlite3.Connection) -> str:
    """The next hybrid logical clock timestamp: the wall clock in
    milliseconds, or the last timestamp's plus a counter when the wall clock
    stood still or went back."""
    now = int(time.time() * 1000)
    row = conn.execute('SELECT wall, counter FROM clock').fetchone()
    wall, counter = (row['wall'], row['counter']) if row else (0, -1)
    if now > wall:
        wall, counter = now, 0
    else:
        counter += 1
        if counter > 9999:
            wall, counter = wall + 1, 0
    conn.execute('INSERT INTO clock (one, wall, counter) VALUES (1, ?, ?) '
                 'ON CONFLICT (one) DO UPDATE SET wall = excluded.wall, counter = excluded.counter', (wall, counter))
    return f'{wall:013d}.{counter:04d}'


def _append(conn: sqlite3.Connection, task: str, kind: str, data: dict, actor: dict) -> dict:
    event = {'id': uuid.uuid4().hex, 'task': task, 'kind': kind, 'data': data, 'hlc': _tick(conn),
             'device': actor['device'], 'actor': actor['kind'], 'session': actor.get('session'),
             'agent': actor.get('name'), 'client': actor.get('client'), 'recorded_at': time.time()}
    conn.execute('INSERT INTO events (id, task, kind, data, hlc, device, actor, session, agent, client, recorded_at) '
                 'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                 (event['id'], task, kind, json.dumps(data, ensure_ascii=False), event['hlc'], event['device'],
                  event['actor'], event['session'], event['agent'], event['client'], event['recorded_at']))
    return event


def _event(row: sqlite3.Row) -> dict:
    event = dict(row)
    event['data'] = json.loads(event['data'])
    return event


def _events(conn: sqlite3.Connection, task: str) -> list[dict]:
    rows = conn.execute('SELECT * FROM events WHERE task = ? ORDER BY hlc, id', (task,))
    return [_event(row) for row in rows]


def _project(conn: sqlite3.Connection, task: str) -> dict:
    """Recompute one Task's state from its events and keep it; a deleted
    Task leaves the projection."""
    state = _fold(_events(conn, task))
    if state.get('deleted'):
        conn.execute('DELETE FROM tasks WHERE uid = ?', (task,))
        return state
    conn.execute('INSERT INTO tasks (uid, id, state) VALUES (?, ?, ?) '
                 'ON CONFLICT (uid) DO UPDATE SET id = excluded.id, state = excluded.state',
                 (task, state['id'], json.dumps(state, ensure_ascii=False)))
    return state


def _states(conn: sqlite3.Connection | None) -> list[dict]:
    if conn is None:
        return []
    return [json.loads(row['state']) for row in conn.execute('SELECT state FROM tasks')]


# ── Computing a Task's state from its events ─────────────────────
# Each kind of event changes the state it is applied to. Events are applied
# in clock order, so for a field the last edit wins, while appended text
# accumulates. Rules (a Ready Task has criteria, Features do not nest) are
# checked when a change is made, not here: an event from a paired device is
# applied as it was written.

def _created(state: dict | None, event: dict) -> dict:
    data = event['data']
    return {
        'uid': event['task'], 'id': data['id'], 'title': data['title'],
        'description': data.get('description', ''), 'appended': [], 'project': data['project'],
        'status': data.get('status', 'backlog'), 'priority': data.get('priority', 'P2'), 'position': None,
        'acceptance': list(data.get('acceptance', [])), 'tags': list(data.get('tags', [])),
        'assignees': list(data.get('assignees', [])), 'feature': data.get('feature'),
        'blockers': list(data.get('blockers', [])), 'blocked': None,
        'created_at': event['recorded_at'], 'created_hlc': event['hlc'], 'created_by': _who(event),
        'updated_at': event['recorded_at'], 'ended_at': None,
    }


_EDITABLE = ('title', 'description', 'project', 'priority', 'acceptance', 'position')


def _edited(state: dict, event: dict) -> dict:
    for field, value in event['data'].items():
        if field in _EDITABLE:
            state[field] = value
            if field == 'description':
                state['appended'] = []  # A replacement was written seeing what was appended before it.
    return state


def _appended(state: dict, event: dict) -> dict:
    state['appended'].append(event['data']['text'])
    return state


def _moved(state: dict, event: dict) -> dict:
    status = event['data']['status']
    if status in ('done', 'cancelled') and state['status'] not in ('done', 'cancelled'):
        state['ended_at'] = event['recorded_at']
    elif status not in ('done', 'cancelled'):
        state['ended_at'] = None
    state['status'] = status
    if 'position' in event['data']:
        state['position'] = event['data']['position']
    return state


def _set_changes(values: list, data: dict) -> list:
    values = [v for v in values if v not in data.get('remove', [])]
    return values + [v for v in data.get('add', []) if v not in values]


def _tagged(state: dict, event: dict) -> dict:
    state['tags'] = _set_changes(state['tags'], event['data'])
    return state


def _assigned(state: dict, event: dict) -> dict:
    state['assignees'] = _set_changes(state['assignees'], event['data'])
    return state


def _linked(state: dict, event: dict) -> dict:
    data = event['data']
    if 'feature' in data:
        state['feature'] = data['feature']
    state['blockers'] = _set_changes(state['blockers'], {'add': data.get('add_blockers', []),
                                                         'remove': data.get('remove_blockers', [])})
    return state


def _blocked(state: dict, event: dict) -> dict:
    state['blocked'] = {'reason': event['data']['reason'], 'by': _who(event), 'at': event['recorded_at']}
    return state


def _unblocked(state: dict, event: dict) -> dict:
    state['blocked'] = None
    return state


_APPLY = {'created': _created, 'edited': _edited, 'appended': _appended, 'moved': _moved, 'tagged': _tagged,
          'assigned': _assigned, 'linked': _linked, 'blocked': _blocked, 'unblocked': _unblocked}


def _fold(events: list[dict]) -> dict:
    tombstone = next((event for event in events if event['kind'] == 'deleted'), None)
    if tombstone is not None:  # Nothing else applies, and the other events lost their content.
        return {'uid': tombstone['task'], 'id': tombstone['data'].get('id'), 'deleted': True,
                'deleted_at': tombstone['recorded_at'], 'updated_at': tombstone['recorded_at']}
    state = None
    for event in events:
        apply = _APPLY.get(event['kind'])
        if apply is None or (state is None and event['kind'] != 'created'):
            continue  # A kind from a newer release, or an event before its Task's creation arrived.
        state = apply(state, event)
        state['updated_at'] = max(state['updated_at'], event['recorded_at'])
    if state is None:
        raise LookupError('task has no created event')
    return state


# ── Validation ───────────────────────────────────────────────────

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


def _status(value) -> str:
    status = str(value or '').strip().lower().replace('-', '_').replace(' ', '_')
    if status not in STATUSES:
        raise ValueError(f'status must be one of {", ".join(STATUSES)}')
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
    from .redaction import redact_all
    return redact_all(texts)


def task_ref(value: str) -> str:
    """A Task id in its stored form, from ``T-7f3kq``, ``t-7F3KQ`` or ``7f3kq``."""
    text = (value or '').strip()
    if text[:2].lower() == 't-':
        text = text[2:]
    return 'T-' + text.lower()


def _draw_id(conn: sqlite3.Connection) -> str:
    while True:
        candidate = 'T-' + ''.join(secrets.choice(ID_ALPHABET) for _ in range(ID_LENGTH))
        if conn.execute('SELECT 1 FROM tasks WHERE id = ?', (candidate,)).fetchone() is None:
            return candidate


def _uid(conn: sqlite3.Connection | None, ref: str) -> str:
    task_id = task_ref(ref)
    row = conn.execute('SELECT uid FROM tasks WHERE id = ?', (task_id,)).fetchone() if conn else None
    if row is None:
        deleted = conn.execute("SELECT 1 FROM events WHERE kind = 'deleted' AND json_extract(data, '$.id') = ?",
                               (task_id,)).fetchone() if conn else None
        raise LookupError(f'Task {task_id} was deleted' if deleted else f'No task {task_id}')
    return row['uid']


def _state(conn: sqlite3.Connection, uid: str) -> dict:
    return json.loads(conn.execute('SELECT state FROM tasks WHERE uid = ?', (uid,)).fetchone()['state'])


# ── Reading ──────────────────────────────────────────────────────

def _order(state: dict) -> tuple:
    position = state.get('position')
    return (PRIORITIES.index(state['priority']), position is None, position or 0, state['created_hlc'])


def _archived(state: dict, now: float) -> bool:
    return state['ended_at'] is not None and now - state['ended_at'] > ARCHIVE_DAYS * 86400


class _View:
    """Every Task of a store as read once: uids resolve to ids, and Tasks
    look up their blockers and Features."""

    def __init__(self, states: list[dict]):
        self.by_uid = {state['uid']: state for state in states}

    def id(self, uid: str | None) -> str | None:
        state = self.by_uid.get(uid) if uid else None
        return state['id'] if state else None

    def public(self, state: dict) -> dict:
        shown = {key: value for key, value in state.items() if key not in ('uid', 'appended')}
        shown['description'] = '\n\n'.join(part for part in [state['description'], *state['appended']] if part)
        shown['feature'] = self.id(state['feature'])
        shown['feature_title'] = self.by_uid[state['feature']]['title'] if shown['feature'] else None
        shown['blockers'] = [i for i in map(self.id, state['blockers']) if i]
        shown['open_blockers'] = [self.id(uid) for uid in state['blockers']
                                  if uid in self.by_uid and self.by_uid[uid]['status'] != 'done']
        shown['tasks'] = sorted(s['id'] for s in self.by_uid.values() if s['feature'] == state['uid'])
        return shown


def get(task: str, workspace: str | None = None) -> dict:
    with _reading(workspace) as conn:
        uid = _uid(conn, task)
        view = _View(_states(conn))
    return view.public(view.by_uid[uid])


def _contains(state: dict, text: str) -> bool:
    haystack = ' '.join([state['id'], state['title'], state['description'], *state['appended'],
                         *state['acceptance'], *state['tags']]).lower()
    return text.lower() in haystack


def listing(*, project: str | None = None, feature: str | None = None, tag: str | None = None,
            status: str | None = None, priority: str | None = None, assignee: str | None = None,
            text: str | None = None, archived: bool = False, workspace: str | None = None) -> list[dict]:
    """The workspace's Tasks that match every filter given, in Board order.
    Done and Cancelled Tasks that ended more than 14 days ago are left out
    unless ``archived``."""
    with _reading(workspace) as conn:
        states = _states(conn)
        feature_uid = _uid(conn, feature) if feature else None
    view, now = _View(states), time.time()
    status = _status(status) if status else None
    priority = _priority(priority) if priority else None
    tag = tag.strip().lower() if tag else None
    assignee = assignee.strip().lower() if assignee else None
    found = [s for s in states
             if (archived or not _archived(s, now))
             and (not project or s['project'] == project)
             and (not feature_uid or s['feature'] == feature_uid)
             and (not tag or tag in s['tags'])
             and (not status or s['status'] == status)
             and (not priority or s['priority'] == priority)
             and (not assignee or assignee in s['assignees'])
             and (not text or _contains(s, text))]
    return [view.public(s) for s in sorted(found, key=_order)]


def board(*, project: str | None = None, workspace: str | None = None, **filters) -> dict:
    """The workspace's Tasks laid out by status."""
    name = _workspace(workspace)
    columns = {status: [] for status in STATUSES}
    for state in listing(project=project, workspace=name, **filters):
        columns[state['status']].append(state)
    return {'workspace': name, 'columns': columns}


def history(task: str, workspace: str | None = None) -> list[dict]:
    """A Task's events, oldest first."""
    with _reading(workspace) as conn:
        return _events(conn, _uid(conn, task))


# ── Changing ─────────────────────────────────────────────────────

def _change(task: str, kind: str, data: dict, actor: dict | None, workspace: str | None, check=None) -> dict:
    """Append one event to an existing Task and return its new state.
    ``check(conn, uid, state)`` refuses a change that breaks a rule."""
    actor = _actor(actor)
    with _writing(workspace) as conn:
        uid = _uid(conn, task)
        if check:
            check(conn, uid, _state(conn, uid))
        _append(conn, uid, kind, data, actor)
        _project(conn, uid)
        view = _View(_states(conn))
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
    project = _text(project, 'project', TITLE_CHARS, required=True)
    description = _text(description, 'description', TEXT_CHARS)
    criteria = _criteria(acceptance)
    status = _status(status)
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
        view = _View(_states(conn))
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
        fields['project'] = _text(project, 'project', TITLE_CHARS, required=True)
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
    status = _status(status)
    data = {'status': status}
    if position is not None:
        data['position'] = float(position)

    def check(conn, uid, state):
        if status == 'ready' and not state['acceptance']:
            raise ValueError('A task moves to ready only with at least one acceptance criterion')
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


def _limit(count: int, limit: int, what: str) -> None:
    if count > limit:
        raise ValueError(f'a task has at most {limit} {what}')


def link(task: str, *, feature=_UNSET, add_blockers=(), remove_blockers=(), actor: dict | None = None,
         workspace: str | None = None) -> dict:
    """Put a Task in a Feature (``''`` takes it out) and add or remove the
    Tasks that block it."""
    actor = _actor(actor)
    with _writing(workspace) as conn:
        uid = _uid(conn, task)
        data = {}
        if feature is not _UNSET:
            data['feature'] = _uid(conn, feature) if feature else None
            _check_feature(conn, uid, data['feature'])
        if add_blockers:
            data['add_blockers'] = [_uid(conn, b) for b in add_blockers]
            _check_blockers(conn, uid, data['add_blockers'])
        if remove_blockers:
            data['remove_blockers'] = [_uid(conn, b) for b in remove_blockers]
        if not data:
            raise ValueError('Nothing to link: pass a feature or blockers')
        _append(conn, uid, 'linked', data, actor)
        _project(conn, uid)
        view = _View(_states(conn))
    return view.public(view.by_uid[uid])


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
