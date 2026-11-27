"""Reading the Board: every Task as read at one moment, its claim live, Stalling or Stale by its lease."""

from __future__ import annotations

import sqlite3
import time

from . import actors
from .state import _short
from .store import PRIORITIES, STATUSES, _events, _reading, _states, _uid, _workspace
from .validation import _priority, status_ref


ARCHIVE_DAYS = 14


def _order(state: dict) -> tuple:
    position = state.get('position')
    return (PRIORITIES.index(state['priority']), position is None, position or 0, state['created_hlc'])


def _archived(state: dict, now: float) -> bool:
    return state['ended_at'] is not None and now - state['ended_at'] > ARCHIVE_DAYS * 86400


def _minutes(key: str) -> float:
    from ..config_schema import resolve
    return float(resolve(key)[0]) * 60


def _leases(conn: sqlite3.Connection | None) -> dict:
    if conn is None:
        return {}
    return {row['claim']: dict(row) for row in conn.execute('SELECT * FROM leases')}


def _view(conn: sqlite3.Connection | None) -> '_View':
    return _View(_states(conn), _leases(conn))


class _View:
    """Every Task of a store as read once, at one moment: uids resolve to
    ids, Tasks look up their blockers and Features, and each claim is live,
    Stalling or Stale by its lease (ADR 0210)."""

    def __init__(self, states: list[dict], leases: dict | None = None):
        self.by_uid = {state['uid']: state for state in states}
        self.leases = leases or {}
        self.now = time.time()
        self.device = actors._device()
        self.limits = None

    def claim(self, state: dict) -> tuple[bool, bool]:
        """(stalling, stale) for the Task's claim."""
        holder = state.get('holder')
        if not holder:
            return False, False
        if self.limits is None:
            self.limits = (_minutes('tasks.lease_minutes'), _minutes('tasks.stalling_minutes'),
                           _minutes('tasks.stale_minutes'))
        lease_limit, stalling_limit, stale_limit = self.limits
        lease = self.leases.get(holder['claim']) or {}
        renewed = max(holder['since'], lease.get('renewed_at') or 0)
        active = max(holder['progress_at'], lease.get('active_at') or 0)
        if lease.get('device') == self.device and lease.get('pid'):
            from ..processes import is_alive
            if not is_alive(lease['pid']):
                return False, True  # Its process ended on this device: released at once.
        if self.now - renewed > lease_limit or self.now - active > stale_limit:
            return False, True
        return self.now - active > stalling_limit, False

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
        stalling, stale = self.claim(state)
        holder = state.get('holder')
        shown['stalling'], shown['stale'] = stalling, stale
        shown['holder'] = None if stale or not holder else {**_short(holder), 'progress_at': holder['progress_at']}
        if stale:
            shown['previous_holder'] = _short(holder)
        shown.pop('lost_claims', None)
        return shown


def get(task: str, workspace: str | None = None) -> dict:
    with _reading(workspace) as conn:
        uid = _uid(conn, task)
        view = _view(conn)
    return view.public(view.by_uid[uid])


def _contains(state: dict, text: str) -> bool:
    haystack = ' '.join([state['id'], *state.get('former_ids', []), state['title'], state['description'],
                         *state['appended'], *state['acceptance'], *state['tags']]).lower()
    return text.lower() in haystack


def listing(*, project: str | None = None, feature: str | None = None, tag: str | None = None,
            status: str | None = None, priority: str | None = None, assignee: str | None = None,
            holder: str | None = None, text: str | None = None, archived: bool = False,
            workspace: str | None = None) -> list[dict]:
    """The workspace's Tasks that match every filter given, in Board order.
    Done and Cancelled Tasks that ended more than 14 days ago are left out
    unless ``archived``."""
    with _reading(workspace) as conn:
        view = _view(conn)
        feature_uid = _uid(conn, feature) if feature else None
    states, now = list(view.by_uid.values()), view.now
    status = status_ref(status) if status else None
    priority = _priority(priority) if priority else None
    tag = tag.strip().lower() if tag else None
    assignee = assignee.strip().lower() if assignee else None
    holder = holder.strip().lower() if holder else None
    held_by = {}
    if holder:
        for s in states:
            live = s.get('holder') if not view.claim(s)[1] else None
            held_by[s['uid']] = {(live.get('name') or '').lower(), (live.get('session') or '').lower()} if live else set()
    found = [s for s in states
             if (archived or not _archived(s, now))
             and (not holder or holder in held_by[s['uid']])
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
