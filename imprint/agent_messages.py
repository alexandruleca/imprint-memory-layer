"""The person's messages to an agent session on this device (ADR 0245).

The dashboard's Live view lets the person write to any running agent
session, with or without a Task. A message is redacted and kept with the
session in ``agent_activity.sqlite3`` on this device, never synced. It
reaches the agent through the notice registry (ADR 0240): on its next
Imprint tool reply in every client, and in Claude Code after its next tool
call or on its next prompt, each message once. Sending pokes the session's
server, which prepares the hook's notice within seconds.

When the session holds a Task, a copy of the message lands on the Task as
the person's comment, already marked told to that session, so the Task's
history and paired devices see it and the agent hears it once.

A message's state is ``waiting`` until it is told, then ``delivered`` with
how (``via``: ``reply``, ``tool``, ``prompt`` or ``inbox``) and when. A
message Claude Code's inbox held or refused, or that failed to send there,
says so and still waits for the notice path.
"""

from __future__ import annotations

import sys
import time
import uuid

from . import agent_activity, notices, tasks
from .redaction import redact

PREFIX = 'msg'
TEXT_CHARS = 4000
HEADING = "From the user, sent from Imprint's dashboard:"
STATES = ('waiting', 'delivered', 'held', 'refused', 'failed')


def _running(board_session: str) -> dict | None:
    """The running session on this device that ``board_session`` names, if any."""
    for session in agent_activity.connected_sessions():
        if session.get('board_session') == board_session or f"process:{session['id']}" == board_session:
            return session
    return None


def board_session_of(session_id: str, *, running: bool = True) -> str | None:
    """The Board's name for the session with this record id (as Live lists
    it): only a session running on this device, unless ``running`` is False."""
    if running:
        for session in agent_activity.connected_sessions():
            if session['id'] == session_id:
                return session.get('board_session') or f"process:{session['id']}"
        return None
    if not agent_activity._db_path().exists():
        return None
    row = agent_activity._connect().execute('SELECT id, board_session FROM agent_sessions WHERE id = ?',
                                            (session_id,)).fetchone()
    return (row['board_session'] or f"process:{row['id']}") if row else None


def _held_task(board_session: str, workspace: str | None) -> dict | None:
    """The Task this session holds on its workspace's Board, if any."""
    try:
        for state in tasks.listing(workspace=workspace):
            if (state.get('holder') or {}).get('session') == board_session and not state.get('stale'):
                return state
    except (LookupError, ValueError, OSError):
        return None
    return None


def send(board_session: str, text: str) -> dict:
    """Store the person's message to a running session and have its server
    tell it within seconds; a copy goes on the Task it holds. Refused, with
    the reason, when the text is empty or the session no longer runs here."""
    text = redact((text or '').strip())
    if not text:
        raise ValueError('Write a message first')
    if len(text) > TEXT_CHARS:
        raise ValueError(f'A message is at most {TEXT_CHARS} characters')
    session = _running(board_session)
    if session is None:
        raise LookupError("That agent's session has ended on this device, so it can no longer be told anything")
    workspace = session.get('workspace') or None
    message = {'id': uuid.uuid4().hex, 'board_session': board_session, 'text': text, 'sent_at': time.time(),
               'state': 'waiting', 'via': None, 'delivered_at': None, 'seen_at': None, 'note': None,
               'task': None, 'workspace': workspace}
    held = _held_task(board_session, workspace)
    if held is not None:
        try:
            state = tasks.comment(held['id'], text, to=['holder'], workspace=workspace)
            told = [c for c in state.get('comments', []) if c.get('text') == text and c['by'].get('kind') == 'user']
            if told:
                # The agent hears the message once, as a message, not again as a comment (Q3).
                tasks.take_unseen(board_session, [f"notified:{told[-1]['id']}"], workspace=workspace)
            message['task'] = held['id']
        except (LookupError, ValueError, PermissionError) as error:
            print(f'[imprint] the message was not copied to {held["id"]}: {error}', file=sys.stderr, flush=True)
    conn = agent_activity._connect()
    conn.execute('INSERT INTO agent_messages (id, board_session, text, sent_at, state, task, workspace) '
                 'VALUES (?, ?, ?, ?, ?, ?, ?)',
                 (message['id'], board_session, text, message['sent_at'], 'waiting', message['task'], workspace))
    conn.commit()
    notices.poke([board_session])
    return message


def messages(board_session: str, *, limit: int = 200) -> list[dict]:
    """A session's messages, oldest first."""
    if not agent_activity._db_path().exists():
        return []
    rows = agent_activity._connect().execute(
        'SELECT * FROM agent_messages WHERE board_session = ? ORDER BY sent_at DESC LIMIT ?',
        (board_session, limit)).fetchall()
    return [dict(row) for row in reversed(rows)]


def recent(board_sessions: list[str], per_session: int = 3) -> dict[str, list[dict]]:
    """The last few messages of each session, oldest first, for Live's cards."""
    if not board_sessions or not agent_activity._db_path().exists():
        return {}
    marks = ','.join('?' * len(board_sessions))
    rows = agent_activity._connect().execute(
        f'SELECT * FROM agent_messages WHERE board_session IN ({marks}) ORDER BY sent_at DESC', board_sessions).fetchall()
    found: dict[str, list[dict]] = {}
    for row in rows:
        shown = found.setdefault(row['board_session'], [])
        if len(shown) < per_session:
            shown.insert(0, dict(row))
    return found


def _waiting(board_session: str) -> list[dict]:
    if not agent_activity._db_path().exists():
        return []
    rows = agent_activity._connect().execute(
        "SELECT * FROM agent_messages WHERE board_session = ? AND state != 'delivered' ORDER BY sent_at",
        (board_session,)).fetchall()
    return [dict(row) for row in rows]


def _section(me: dict, workspace: str | None):
    """The messages a session has not been told, for the notice registry."""
    pending = _waiting(me['session'])
    if not pending:
        return None
    lines = []
    for message in pending:
        lines.append(f"- {message['text']}" if '\n' not in message['text']
                     else '- ' + message['text'].replace('\n', '\n  '))
    return notices.Section(HEADING, [f"{PREFIX}:{m['id']}" for m in pending], lines,
                           more="More messages from the user wait: they are on Imprint's dashboard.")


def _settle(keys: list[str], session: str, via: str, at: float, *, workspace: str | None = None) -> list[str]:
    """Mark the messages ``keys`` name delivered to ``session`` by ``via``;
    returns the keys newly marked, so no path tells a message twice."""
    conn = agent_activity._connect()
    told = []
    for key in keys:
        message_id = key.split(':', 1)[1]
        cursor = conn.execute("UPDATE agent_messages SET state = 'delivered', via = ?, delivered_at = ?, note = NULL "
                              "WHERE id = ? AND board_session = ? AND state != 'delivered'",
                              (via, at, message_id, session))
        if cursor.rowcount:
            told.append(key)
    conn.commit()
    return told


def mark(message_id: str, state: str, note: str | None = None) -> None:
    """Record what became of a message on a path that did not tell it: held
    or refused by the client, or a send that failed (the inbox, ADR 0245).
    A delivered message stays delivered."""
    if state not in STATES or state == 'delivered':
        raise ValueError(f'state is one of {", ".join(s for s in STATES if s != "delivered")}')
    conn = agent_activity._connect()
    conn.execute("UPDATE agent_messages SET state = ?, note = ? WHERE id = ? AND state != 'delivered'",
                 (state, note, message_id))
    conn.commit()


def mark_seen(message_ids: list[str], at: float | None = None) -> None:
    """The agent's transcript shows these delivered messages (ADR 0244)."""
    if not message_ids:
        return
    conn = agent_activity._connect()
    conn.executemany("UPDATE agent_messages SET seen_at = ? WHERE id = ? AND state = 'delivered' AND seen_at IS NULL",
                     [(at or time.time(), message_id) for message_id in message_ids])
    conn.commit()


def _register() -> None:
    notices.register_provider('messages', _section)
    notices.register_settler(PREFIX, _settle)


_register()
