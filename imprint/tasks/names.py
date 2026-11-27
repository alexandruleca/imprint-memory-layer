"""Agent names (ADR 0216)."""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import time

from . import actors
from .store import ID_ALPHABET, _reading, _writing
from .view import _view


_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9-]{1,23}$')


# Words that address someone else in a comment (ADR 0212) cannot be names.
RESERVED_NAMES = frozenset({'user', 'holder', 'everyone', *actors.CLIENTS})


def _name(conn: sqlite3.Connection, session: str, name: str, client: str | None, pid: int | None,
          chosen: bool) -> None:
    conn.execute('INSERT INTO names (session, name, lower, client, device, pid, chosen, updated_at) '
                 'VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (session) DO UPDATE SET name = excluded.name, '
                 'lower = excluded.lower, client = COALESCE(excluded.client, client), device = excluded.device, '
                 'pid = excluded.pid, chosen = excluded.chosen, updated_at = excluded.updated_at',
                 (session, name, name.lower(), client, actors._device(), pid or os.getpid(), int(chosen), time.time()))


def _taken(conn: sqlite3.Connection, session: str) -> set[str]:
    """Names, in lower case, that another session has: one live on this
    device, or one holding a live claim, seen from any device."""
    from ..processes import is_alive
    device = actors._device()
    taken = {row['lower'] for row in conn.execute('SELECT lower, device, pid FROM names WHERE session != ?', (session,))
             if row['device'] != device or is_alive(row['pid'])}
    view = _view(conn)
    for state in view.by_uid.values():
        holder = state.get('holder')
        if holder and holder['session'] != session and holder.get('name') and not view.claim(state)[1]:
            taken.add(holder['name'].lower())
    return taken


def choose_name(session: str, name: str, *, client: str | None = None, pid: int | None = None,
                workspace: str | None = None) -> str:
    """Give an agent session the name it chose: 2 to 24 letters, digits and
    hyphens, unique without case among the workspace's live sessions."""
    name = (name or '').strip()
    if not _NAME.match(name):
        raise ValueError('A name is 2 to 24 letters, digits and hyphens, starting with a letter or digit')
    if name.lower() in RESERVED_NAMES:
        raise ValueError(f'{name} is reserved: it addresses a client or role in comments; choose another')
    with _writing(workspace) as conn:
        taken = _taken(conn, session)
        if name.lower() in taken:
            suggestion = next(f'{name}-{n}' for n in range(2, 1000) if f'{name}-{n}'.lower() not in taken)
            raise ValueError(f'{name} is taken by another session: choose another, such as {suggestion}')
        _name(conn, session, name, client, pid, chosen=True)
    return name


def name_of(session: str, workspace: str | None = None) -> str | None:
    with _reading(workspace) as conn:
        row = conn.execute('SELECT name FROM names WHERE session = ?', (session,)).fetchone() if conn else None
    return row['name'] if row else None


def ensure_name(session: str, *, client: str | None = None, pid: int | None = None,
                workspace: str | None = None) -> str:
    """The session's name, or a fallback (its client and four characters)
    given now to a session that never chose one."""
    with _writing(workspace) as conn:
        row = conn.execute('SELECT name FROM names WHERE session = ?', (session,)).fetchone()
        if row:
            return row['name']
        taken = _taken(conn, session)
        prefix = re.sub(r'[^a-z0-9-]', '', (client or 'agent').lower())[:19].strip('-') or 'agent'
        while True:
            name = f"{prefix}-{''.join(secrets.choice(ID_ALPHABET) for _ in range(4))}"
            if name not in taken:
                break
        _name(conn, session, name, client, pid, chosen=False)
    return name
