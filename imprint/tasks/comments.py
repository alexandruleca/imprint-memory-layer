"""Comments (ADR 0212): addressed messages on Tasks, and each session's unread ones."""

from __future__ import annotations

import re
import sqlite3

from .actors import _who
from .changes import _change
from .claims import take_unseen
from .store import _event, _reading
from .validation import ASSIGNEES, TEXT_CHARS, _redacted, _text
from .view import _View, _view


_MENTION = re.compile(r'(?<![\w@])@([A-Za-z0-9][A-Za-z0-9._-]*(?:@[A-Za-z0-9][A-Za-z0-9._-]*)?)')


_ADDRESS = re.compile(r'^(?:@?[a-z0-9][a-z0-9._-]{0,63}|[a-z0-9][a-z0-9._-]{0,63}@[a-z0-9][a-z0-9._-]{0,63})$')


INBOX = 20


def _addresses(values, text: str) -> list[str]:
    found = []
    for value in [*(values or []), *(m.rstrip('.') for m in _MENTION.findall(text))]:
        address = str(value or '').strip().lower()
        if not _ADDRESS.match(address):
            raise ValueError(f'{value!r} is not an address: use "user", "holder", a name, a client, '
                             '"@device" or "client@device"')
        if address not in found:
            found.append(address)
    return found[:ASSIGNEES]


def comment(task: str, text: str, *, to=(), actor: dict | None = None, workspace: str | None = None) -> dict:
    """Comment on a Task, optionally addressed; ``@name`` in the text
    addresses too."""
    text = _redacted([_text(text, 'comment', TEXT_CHARS, required=True)])[0]
    return _change(task, 'comment', {'text': text, 'to': _addresses(to, text)}, actor, workspace)


def _addressed(to: list[str], holder: dict | None, session: str, name: str | None, client: str | None,
               device: str | None) -> bool:
    name, client, device = (name or '').lower(), (client or '').lower(), (device or '').lower()
    is_holder = bool(holder) and holder['session'] == session
    if not to:
        return is_holder
    for address in to:
        if address == 'holder' and is_holder:
            return True
        if '@' in address[1:]:
            if client and device and address == f'{client}@{device}':
                return True
            continue
        bare = address.lstrip('@')  # "@grimforge", "@codex" and "@bedrock" read as the bare word.
        if bare in {a for a in (name, client, device) if a} and bare not in ('user', 'holder'):
            return True
    return False


def _comment_rows(conn: sqlite3.Connection, view: _View) -> list[tuple[dict, dict]]:
    """(comment event, its Task's state) for every comment, oldest first."""
    rows = conn.execute("SELECT * FROM events WHERE kind = 'comment' ORDER BY hlc, id")
    return [(event, view.by_uid[event['task']]) for event in map(_event, rows) if event['task'] in view.by_uid]


def _inbox_item(event: dict, state: dict) -> dict:
    return {'id': event['id'], 'task': state['id'], 'title': state['title'], 'text': event['data'].get('text', ''),
            'to': event['data'].get('to', []), 'by': _who(event), 'at': event['recorded_at']}


def _unread(reader: str, wanted, mark: bool, workspace: str | None) -> list[dict]:
    """The newest ``INBOX`` comments ``wanted(event, state, view)`` picks that
    ``reader`` has not read and did not write, oldest first; ``mark`` marks
    them read."""
    with _reading(workspace) as conn:
        if conn is None:
            return []
        view = _view(conn)
        read = {row['key'] for row in conn.execute('SELECT key FROM seen WHERE session = ?', (reader,))}
        found = [_inbox_item(event, state) for event, state in _comment_rows(conn, view)
                 if event.get('session') != reader and f"read:{event['id']}" not in read and wanted(event, state, view)]
    found = found[-INBOX:]
    if mark and found:
        take_unseen(reader, [f"read:{item['id']}" for item in found], workspace=workspace)
    return found


def inbox(session: str, *, name: str | None = None, client: str | None = None, device: str | None = None,
          mark: bool = True, workspace: str | None = None) -> list[dict]:
    """Unread comments addressed to this session, oldest first (at most the
    20 newest); ``mark`` marks them read."""
    def addressed(event, state, view):
        holder = None if view.claim(state)[1] else state['holder']
        return _addressed(event['data'].get('to', []), holder, session, name, client, device)
    return _unread(session, addressed, mark, workspace)


def user_inbox(*, mark: bool = True, workspace: str | None = None) -> list[dict]:
    """Unread comments agents, or Imprint itself (the Dispatcher, ADR 0220),
    addressed to the user, oldest first."""
    return _unread('user', lambda event, state, view: event['actor'] in ('agent', 'system')
                   and 'user' in event['data'].get('to', []), mark, workspace)


def mark_user_read(ids: list[str], workspace: str | None = None) -> int:
    """Mark these comments to the user read; returns how many were unread."""
    return len(take_unseen('user', [f'read:{i}' for i in ids if isinstance(i, str) and i], workspace=workspace))
