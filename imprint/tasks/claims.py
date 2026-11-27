"""Claims are leases held by the agent session (ADR 0210)."""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time

from .. import config
from . import actors
from .actors import _actor
from .names import _name
from .store import _append, _project, _reading, _state, _states, _uid, _writing
from .validation import TEXT_CHARS, TITLE_CHARS, _redacted, status_ref, _text
from .view import _view


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _lease(conn: sqlite3.Connection, claim: str, now: float, pid: int | None, device: str,
           active_at: float | None) -> None:
    conn.execute('INSERT INTO leases (claim, renewed_at, active_at, pid, device) VALUES (?, ?, ?, ?, ?) '
                 'ON CONFLICT (claim) DO UPDATE SET renewed_at = MAX(renewed_at, excluded.renewed_at), '
                 'active_at = MAX(COALESCE(active_at, 0), COALESCE(excluded.active_at, 0)), '
                 'pid = excluded.pid, device = excluded.device', (claim, now, active_at, pid, device))


def claim(task: str, *, actor: dict, token: str | None = None, pid: int | None = None,
          workspace: str | None = None) -> dict:
    """Claim a Task for an agent session: ``{'task': state, 'token': token}``.

    The token proves the claim is this agent's: presented by a resumed
    conversation or a restarted server, it takes the claim back. A Task held
    by a live session is refused; a Stale one is taken over. Claiming moves a
    Backlog or Ready Task to In progress. Claiming a Task this session holds
    returns it with no token."""
    actor = _actor(actor)
    if actor['kind'] != 'agent':
        raise PermissionError('Agents claim tasks; assign it to "user" to take it on yourself')
    with _writing(workspace) as conn:
        uid = _uid(conn, task)
        view = _view(conn)
        state = view.by_uid[uid]
        if state['status'] in ('done', 'cancelled'):
            raise ValueError(f"{state['id']} is {state['status'].title()}: move it back before claiming it")
        if 'user' in state['assignees']:
            raise PermissionError(f"{state['id']} is assigned to the user: agents comment on it but do not claim it")
        holder, replaces = state['holder'], None
        if holder:
            if holder['session'] == actor['session']:
                return {'task': view.public(state), 'token': None}
            _, stale = view.claim(state)
            if token and _hash(token) == holder.get('token_hash'):
                replaces = holder['claim']
            elif stale:
                replaces, token = holder['claim'], None
            else:
                since = time.strftime('%Y-%m-%d %H:%M', time.localtime(holder['since']))
                raise PermissionError(f"{state['id']} is held by {actors.label(holder)} on {holder['device']} since "
                                      f"{since}, and it is not stale")
        else:
            token = None
        token = token or secrets.token_urlsafe(24)
        data = {'token_hash': _hash(token)}
        if replaces:
            data['replaces'] = replaces
        if replaces and data['token_hash'] == holder.get('token_hash') and holder.get('name') and not actor.get('name'):
            # The same agent after a restart: it keeps the name it had.
            _name(conn, actor['session'], holder['name'], actor.get('client'), pid, chosen=True)
            actor = {**actor, 'name': holder['name']}
        event = _append(conn, uid, 'claimed', data, actor)
        _lease(conn, event['id'], event['recorded_at'], pid or os.getpid(), actor['device'], event['recorded_at'])
        _project(conn, uid)
        view = _view(conn)
    return {'task': view.public(view.by_uid[uid]), 'token': token}


def _held(conn: sqlite3.Connection, uid: str, actor: dict) -> dict:
    view = _view(conn)
    state = view.by_uid[uid]
    _, stale = view.claim(state)
    holder = state['holder']
    if not holder or stale or holder['session'] != actor.get('session'):
        raise PermissionError(f"You do not hold {state['id']}: claim it first")
    return state


def checkpoint(task: str, text: str, *, branch: str | None = None, commit: str | None = None, files=(),
               next_step: str | None = None, actor: dict, workspace: str | None = None) -> dict:
    """Record the holder's progress: what is done, what is next, and where
    the work is (its task branch and last commit, the files touched)."""
    actor = _actor(actor)
    texts = _redacted([_text(text, 'checkpoint', TEXT_CHARS, required=True), _text(next_step, 'next step', TEXT_CHARS)])
    data = {'text': texts[0]}
    if texts[1]:
        data['next'] = texts[1]
    if branch:
        data['branch'] = _text(branch, 'branch', TITLE_CHARS)
    if commit:
        data['commit'] = _text(commit, 'commit', 64)
    if files:
        data['files'] = [_text(f, 'file', 500) for f in list(files)[:200]]
    with _writing(workspace) as conn:
        uid = _uid(conn, task)
        state = _held(conn, uid, actor)
        event = _append(conn, uid, 'checkpoint', data, actor)
        _lease(conn, state['holder']['claim'], event['recorded_at'], os.getpid(), actor['device'], event['recorded_at'])
        _project(conn, uid)
        view = _view(conn)
    return view.public(view.by_uid[uid])


def release(task: str, *, status: str | None = None, actor: dict | None = None,
            workspace: str | None = None) -> dict:
    """End a claim without finishing: its holder, or the user, lets it go.
    Work in progress goes back to Ready (Backlog without criteria) unless a
    status is given."""
    actor = _actor(actor)
    with _writing(workspace) as conn:
        uid = _uid(conn, task)
        state = _state(conn, uid) if actor['kind'] == 'user' else _held(conn, uid, actor)
        if not state['holder']:
            raise ValueError(f"{state['id']} has no claim to release")
        if status is None and state['status'] == 'in_progress':
            status = 'ready' if state['acceptance'] else 'backlog'
        data = {'claim': state['holder']['claim']}
        if status:
            data['status'] = status_ref(status)
            if data['status'] not in ('backlog', 'ready'):
                raise ValueError('Releasing puts a Task back in Backlog or Ready; move it to Review when it is finished')
            if data['status'] == 'ready' and not state['acceptance']:
                raise ValueError('A task moves to ready only with at least one acceptance criterion')
        _append(conn, uid, 'released', data, actor)
        _project(conn, uid)
        view = _view(conn)
    return view.public(view.by_uid[uid])


def renew(session: str, *, pid: int | None = None, active_at: float | None = None) -> int:
    """Renew the lease of every claim ``session`` holds, in every workspace
    of this installation; ``active_at`` is the holder's latest Imprint
    activity. The holder's Imprint server calls it in the background."""
    renewed, now, pid = 0, time.time(), pid or os.getpid()
    for workspace in config.get_known_workspaces():
        with _reading(workspace) as conn:
            if conn is None:
                continue
            held = [s['holder']['claim'] for s in _states(conn) if (s.get('holder') or {}).get('session') == session]
            named = conn.execute('SELECT pid, device FROM names WHERE session = ?', (session,)).fetchone()
        stale_name = named is not None and (named['pid'], named['device']) != (pid, actors._device())
        if not held and not stale_name:
            continue
        with _writing(workspace) as conn:
            for claim_id in held:
                _lease(conn, claim_id, now, pid, actors._device(), active_at)
                renewed += 1
            if stale_name:  # A resumed session: its name is live again, under this process.
                conn.execute('UPDATE names SET pid = ?, device = ? WHERE session = ?', (pid, actors._device(), session))
    return renewed


def lost_claims(session: str, workspace: str | None = None) -> list[dict]:
    """The claims ``session`` made that another, earlier claim beat."""
    with _reading(workspace) as conn:
        states = _states(conn)
    return [{'task': s['id'], 'title': s['title'], 'to': lost['to'], 'at': lost['at'], 'claim': lost['claim']}
            for s in states for lost in s.get('lost_claims', []) if lost['session'] == session]


def take_unseen(session: str, keys: list[str], workspace: str | None = None) -> list[str]:
    """The ``keys`` this session has not been told yet, now marked told."""
    if not keys:
        return []
    with _writing(workspace) as conn:
        new = [key for key in keys if conn.execute('INSERT OR IGNORE INTO seen (session, key) VALUES (?, ?)',
                                                   (session, key)).rowcount]
    return new
