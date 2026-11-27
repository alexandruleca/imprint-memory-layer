"""A workspace's store of events: connections, the hybrid logical clock, appending events and keeping
the projection of each Task's state."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .. import config
from .state import _fold


STATUSES = ('backlog', 'ready', 'in_progress', 'review', 'done', 'cancelled')


PRIORITIES = ('P0', 'P1', 'P2', 'P3')


# Crockford's base32 in lower case: no i, l, o or u to misread.
ID_ALPHABET = '0123456789abcdefghjkmnpqrstvwxyz'


ID_LENGTH = 5


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
CREATE TABLE IF NOT EXISTS leases (
    claim      TEXT PRIMARY KEY,
    renewed_at REAL NOT NULL,
    active_at  REAL,
    pid        INTEGER,
    device     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen (
    session TEXT NOT NULL,
    key     TEXT NOT NULL,
    PRIMARY KEY (session, key)
);
CREATE TABLE IF NOT EXISTS names (
    session    TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    lower      TEXT NOT NULL,
    client     TEXT,
    device     TEXT NOT NULL,
    pid        INTEGER,
    chosen     INTEGER NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS names_by_lower ON names (lower);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clock (
    one     INTEGER PRIMARY KEY CHECK (one = 1),
    wall    INTEGER NOT NULL,
    counter INTEGER NOT NULL
);
"""


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
