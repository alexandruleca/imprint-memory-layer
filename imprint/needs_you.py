"""What waits for the person, one row per reason (ADR 0241).

The dashboard's Needs-you list on Home and the Board: comments agents (or
Imprint) addressed to the person, Tasks no paired device will review,
Tasks assigned to the person, Tasks dispatched work fed (ADR 0227), Stale
Tasks whose work is on another device, a Dispatcher that switched itself
off, and agents waiting for the person's answer on the Agents page.

A row stays until its reason ends or the person dismisses it. Dismissing
a comment marks it read; dismissing any other row records a read mark for
that row's version, so it reaches paired devices (``merge.py`` exchanges
the user's ``read:`` marks) and the row comes back when the Task changes.
"""

from __future__ import annotations

import sys

from . import config, tasks

DISMISSED = 'read:dismissed:'
REASON_CHARS = 240


def _row(kind: str, key: str, task: str | None, title: str, reason: str, since: float | None,
         workspace: str) -> dict:
    return {'kind': kind, 'id': key, 'task': task, 'title': title, 'reason': reason, 'since': since,
            'workspace': workspace}


def _short(text: str) -> str:
    text = ' '.join((text or '').split())
    return text if len(text) <= REASON_CHARS else text[:REASON_CHARS - 1] + '…'


def _comments(workspaces: list[str]) -> list[dict]:
    found = []
    for workspace in workspaces:
        for item in tasks.user_inbox(mark=False, workspace=workspace):
            who = tasks.label(item['by']) if item.get('by', {}).get('kind') != 'system' else 'Imprint'
            found.append(_row('comment', item['id'], item['task'], item['title'],
                              _short(f"{who} wrote to you: {item['text']}"), item['at'], workspace))
    return found


def _reviewed_somewhere(workspace: str) -> bool:
    """Whether a device's Dispatcher, this one or a paired one, reviews this
    workspace's Tasks (ADR 0237); an earlier release's ``on`` counts."""
    from .dispatch import store
    if 'review' in store.roles(workspace):
        return True
    return any(d['on'] and 'review' in (d.get('roles') or []) for d in tasks.dispatching(workspace))


def _versioned(kind: str, state: dict) -> str:
    """A row's key for one version of its Task: a dismissed row returns once the Task changes."""
    return f"{kind}:{state['id']}:{state['updated_at']:.3f}"


def _task_rows(workspace: str) -> list[dict]:
    from .dispatch import guards, store
    states = tasks.listing(workspace=workspace)
    open_states = [s for s in states if s['status'] not in ('done', 'cancelled')]
    found = []
    for state in open_states:
        if 'user' in state['assignees']:
            found.append(_row('assigned', _versioned('assigned', state), state['id'], state['title'],
                              'Assigned to you.', state['updated_at'], workspace))
    if not _reviewed_somewhere(workspace):
        for state in open_states:
            if state['status'] == 'review' and not state.get('holder') and 'user' not in state['assignees']:
                found.append(_row('review', _versioned('review', state), state['id'], state['title'],
                                  "Waiting for review, and no device's Dispatcher reviews this workspace: review it, "
                                  'or tick Review on a device.', state['status_at'], workspace))
    dispatched = tasks.dispatched_sessions(workspace)
    for state in open_states:
        # A Feature's status follows its Tasks, so the person never moves one (ADR 0242).
        if state['status'] in ('ready', 'review', 'backlog') and not state.get('holder') \
                and state.get('kind') != 'feature' and 'user' not in state['assignees'] \
                and guards.fed_by_dispatched(state['id'], workspace, dispatched):
            found.append(_row('fed', _versioned('fed', state), state['id'], state['title'],
                              'A Dispatched session made or moved it, so the Dispatcher leaves it until you move it.',
                              state['updated_at'], workspace))
    for state in open_states:
        if not state.get('stale') or 'user' in state['assignees']:
            continue
        commit = tasks.handoff(state['id'], workspace=workspace).get('commit')
        marker = f'{commit[:12]} {guards.MISSING_WORK}' if commit else None
        if marker and any(e['kind'] == 'comment' and e['actor'] == 'system' and marker in e['data'].get('text', '')
                          for e in tasks.history(state['id'], workspace=workspace)):
            found.append(_row('stale', _versioned('stale', state), state['id'], state['title'],
                              'Stale, and its last commit is on another device: push it, or take it over there.',
                              state['updated_at'], workspace))
    limit = guards.limit('dispatch.failure_limit')
    if limit > 0 and not store.roles(workspace) and guards.failures_in_a_row() >= limit:
        last = store.recent(1)
        at = (last[0].get('ended_at') or last[0]['started_at']) if last else None
        found.append(_row('dispatcher', f'dispatcher:{workspace}:{at or 0:.3f}', None, "This device's Dispatcher",
                          f'It switched itself off after {limit} Dispatched sessions failed in a row: see why on '
                          'their Tasks, then tick its roles again on the Board.', at, workspace))
    return found


def _answers(workspace: str) -> list[dict]:
    """Agents on this device waiting for the person's answer, from the
    Agents page's live state when it reports them (``needs_answer``)."""
    try:
        from . import agents_live
        report = agents_live.live()
    except Exception as error:  # The list must not fail because a transcript could not be read.
        print(f'[imprint] needs-you could not read the live agents: {error}', file=sys.stderr, flush=True)
        return []
    found = []
    for agent in report.get('agents', []):
        if agent.get('state') != 'needs_answer':
            continue
        activity = agent.get('activity') or {}
        question = agent.get('question') or activity.get('question') or 'It is waiting for your answer.'
        since = agent.get('since') or activity.get('since')
        task = agent.get('task') or {}
        found.append(_row('answer', f"answer:{agent['id']}:{since or 0}", task.get('id'),
                          task.get('title') or agent.get('name') or agent.get('client') or 'An agent',
                          _short(f"{agent.get('name') or agent.get('client') or 'An agent'} asks: {question}"),
                          since, agent.get('workspace') or workspace))
    return found


def rows(workspace: str | None = None) -> dict:
    """Everything waiting for the person, newest first: comments from every
    workspace, the rest from the workspace on show."""
    active = workspace or config.get_active_workspace()
    workspaces = [active, *(w for w in config.get_known_workspaces() if w != active)]
    found = [*_comments(workspaces), *_task_rows(active), *_answers(active)]
    dismissed = set(_dismissed([r for r in found if r['kind'] != 'comment'], active))
    found = [r for r in found if r['id'] not in dismissed]
    found.sort(key=lambda r: -(r['since'] or 0))
    return {'workspace': active, 'rows': found}


def _dismissed(rows_: list[dict], workspace: str) -> list[str]:
    keys = [f'{DISMISSED}{r["id"]}' for r in rows_]
    unseen = set(tasks.unseen('user', keys, workspace=workspace)) if keys else set()
    return [r['id'] for r, key in zip(rows_, keys) if key not in unseen]


def dismiss(row_id: str, workspace: str | None = None) -> bool:
    """Dismiss one row: a comment is marked read; any other row is hidden
    until its Task changes. True when it was shown."""
    active = workspace or config.get_active_workspace()
    if not isinstance(row_id, str) or not row_id or len(row_id) > 200:
        raise ValueError('a row id is a short text')
    if ':' not in row_id:  # a comment's id
        return bool(tasks.mark_user_read([row_id], workspace=active))
    return bool(tasks.take_unseen('user', [f'{DISMISSED}{row_id}'], workspace=active))
