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
    """Recompute one Task's state from its events and keep it."""
    state = _fold(_events(conn, task))
    conn.execute('INSERT INTO tasks (uid, id, state) VALUES (?, ?, ?) '
                 'ON CONFLICT (uid) DO UPDATE SET id = excluded.id, state = excluded.state',
                 (task, state['id'], json.dumps(state, ensure_ascii=False)))
    return state


def _states(conn: sqlite3.Connection | None) -> list[dict]:
    if conn is None:
        return []
    return [json.loads(row['state']) for row in conn.execute('SELECT state FROM tasks')]


# ── Computing a Task's state from its events ─────────────────────

def _created(state: dict | None, event: dict) -> dict:
    data = event['data']
    return {
        'uid': event['task'], 'id': data['id'], 'title': data['title'],
        'description': data.get('description', ''), 'project': data['project'],
        'status': data.get('status', 'backlog'), 'priority': data.get('priority', 'P2'), 'position': None,
        'acceptance': list(data.get('acceptance', [])), 'tags': list(data.get('tags', [])),
        'assignees': list(data.get('assignees', [])),
        'created_at': event['recorded_at'], 'created_hlc': event['hlc'], 'created_by': _who(event),
        'updated_at': event['recorded_at'], 'ended_at': None,
    }


_APPLY = {'created': _created}


def _fold(events: list[dict]) -> dict:
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
        raise LookupError(f'No task {task_id}')
    return row['uid']


# ── Reading ──────────────────────────────────────────────────────

def _order(state: dict) -> tuple:
    position = state.get('position')
    return (PRIORITIES.index(state['priority']), position is None, position or 0, state['created_hlc'])


def _public(state: dict) -> dict:
    return {key: value for key, value in state.items() if key != 'uid'}


def get(task: str, workspace: str | None = None) -> dict:
    with _reading(workspace) as conn:
        uid = _uid(conn, task)
        row = conn.execute('SELECT state FROM tasks WHERE uid = ?', (uid,)).fetchone()
        return _public(json.loads(row['state']))


def _matches(state: dict, project: str | None, status: str | None) -> bool:
    return (not project or state['project'] == project) and (not status or state['status'] == status)


def listing(*, project: str | None = None, status: str | None = None, workspace: str | None = None) -> list[dict]:
    """The workspace's Tasks that match every filter given, in Board order."""
    with _reading(workspace) as conn:
        states = [s for s in _states(conn) if _matches(s, project, status)]
    return [_public(s) for s in sorted(states, key=_order)]


def board(*, project: str | None = None, workspace: str | None = None) -> dict:
    """The workspace's Tasks laid out by status."""
    name = _workspace(workspace)
    columns = {status: [] for status in STATUSES}
    for state in listing(project=project, workspace=name):
        columns[state['status']].append(state)
    return {'workspace': name, 'columns': columns}


def history(task: str, workspace: str | None = None) -> list[dict]:
    """A Task's events, oldest first."""
    with _reading(workspace) as conn:
        return _events(conn, _uid(conn, task))


# ── Changing ─────────────────────────────────────────────────────

def create(title: str, *, project: str, description: str = '', status: str = 'backlog', priority: str = 'P2',
           acceptance=(), tags=(), assignees=(), actor: dict | None = None, workspace: str | None = None) -> dict:
    """Put a new Task on the workspace's Board."""
    actor = _actor(actor)
    title = _text(title, 'title', TITLE_CHARS, required=True)
    project = _text(project, 'project', TITLE_CHARS, required=True)
    description = _text(description, 'description', TEXT_CHARS)
    criteria = _criteria(acceptance)
    if status not in ('backlog', 'ready'):
        raise ValueError('a new task starts in backlog or ready')
    if status == 'ready' and not criteria:
        raise ValueError('A task moves to ready only with at least one acceptance criterion')
    texts = _redacted([title, description, *criteria])
    data = {'title': texts[0], 'description': texts[1], 'project': project, 'status': status,
            'priority': _priority(priority), 'acceptance': texts[2:], 'tags': _tags(tags),
            'assignees': list(assignees or [])}
    with _writing(workspace) as conn:
        uid = uuid.uuid4().hex
        data['id'] = _draw_id(conn)
        _append(conn, uid, 'created', data, actor)
        return _public(_project(conn, uid))
