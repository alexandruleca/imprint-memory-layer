"""What bounds the Dispatcher's spending (ADR 0220, "Limits", "Failures", "Its own Tasks").

- ``dispatch.daily_limit`` sessions start a day on this device;
- after ``dispatch.failure_limit`` failures in a row the Dispatcher switches
  itself off and tells the person;
- a Task whose Dispatched sessions fail twice goes to the person;
- a Task a Dispatched session created, or moved to Ready from Backlog, is
  never dispatched, so dispatched work cannot feed itself.

A session the person stopped, or the server's shutdown, is *stopped*: it
is no failure.
"""

from __future__ import annotations

import datetime
import time

from .. import tasks
from ..tasks.actors import SYSTEM
from . import store

TASK_FAILURES = 2


def _setting(key: str) -> int:
    from ..config_schema import resolve
    return int(resolve(key)[0])


def started_today(now: float | None = None) -> int:
    """Sessions started on this device since local midnight."""
    midnight = datetime.datetime.combine(datetime.date.fromtimestamp(now or time.time()), datetime.time()).timestamp()
    return sum(1 for run in store.recent(500) if run['started_at'] >= midnight and run['outcome'] != 'refused')


def held() -> str | None:
    """Why the Dispatcher starts nothing now despite being on, or None."""
    limit = _setting('dispatch.daily_limit')
    if started_today() >= limit:
        return (f'{limit} Dispatched sessions started today, the daily limit (dispatch.daily_limit): '
                'more start tomorrow')
    return None


def failures_in_a_row() -> int:
    """Failed runs since the last success, counting only runs started after
    the Dispatcher was last switched on; stopped and refused runs do not count."""
    since = store.last_switched_on()
    count = 0
    for run in store.recent(100):
        if run['ended_at'] is None or run['outcome'] in ('stopped', 'refused'):
            continue
        if run['started_at'] < since or run['outcome'] != 'failed':
            break
        count += 1
    return count


def after_failure(run: dict) -> None:
    """A Task whose Dispatched sessions failed twice goes to the person."""
    failed = sum(1 for r in store.runs_of(run['task']) if r['outcome'] == 'failed')
    if failed < TASK_FAILURES:
        return
    state = tasks.get(run['task'], workspace=run['workspace'])
    if 'user' in state['assignees']:
        return
    tasks.assign(run['task'], add=['user'], actor=SYSTEM, workspace=run['workspace'])
    tasks.comment(run['task'], f'Dispatched sessions failed on this Task {failed} times, so the Dispatcher leaves it '
                  'to you: their comments above say where each stopped. Unassign yourself to let agents take it again.',
                  to=['user'], actor=SYSTEM, workspace=run['workspace'])


def switch_off_after_failures(run: dict) -> bool:
    """Switch every workspace's Dispatcher on this device off after
    ``dispatch.failure_limit`` failures in a row, and tell the person once,
    on the last failed Task. True when it switched off."""
    limit = _setting('dispatch.failure_limit')
    if limit <= 0 or failures_in_a_row() < limit:
        return False
    from . import status
    for workspace in store.switched_on_workspaces():
        status.switch(workspace, False)
    tasks.comment(run['task'], f'The Dispatcher on this device switched itself off after {limit} Dispatched sessions '
                  'failed in a row; the comments on their Tasks say why. Switch it on again from the Board when '
                  'that is fixed.', to=['user'], actor=SYSTEM, workspace=run['workspace'])
    return True


def _readying(events: list[dict]) -> dict | None:
    """The latest event that made the Task Ready from Backlog, or created it
    Ready; moves back to Ready from work or review do not count."""
    status, found = None, None
    for event in events:
        kind, data = event['kind'], event['data']
        if kind == 'created':
            status = data.get('status', 'backlog')
            found = event if status == 'ready' else found
        elif kind == 'moved':
            if data.get('status') == 'ready' and status == 'backlog':
                found = event
            status = data.get('status', status)
        elif kind == 'claimed' and status in ('backlog', 'ready'):
            status = 'in_progress'
        elif kind == 'released':
            status = data.get('status') or ('ready' if status == 'in_progress' else status)
    return found


def readied_by_dispatched(task: str, workspace: str, dispatched: set[str]) -> bool:
    """Whether a Dispatched session created ``task`` Ready or moved it to Ready from Backlog."""
    event = _readying(tasks.history(task, workspace=workspace))
    return bool(event) and event['actor'] == 'agent' and event['session'] in dispatched


MISSING_WORK = "is not in this device's copy of the repository"


def takeover(state: dict, folder: dict, workspace: str, *, doing: str = 'take this Stale Task over') -> tuple[bool, dict]:
    """Whether this device can take ``state`` over (ADR 0223): it has no
    commit yet, or its Handoff's last commit exists here. When it cannot,
    the Task gets one comment saying so, and is left to the device that
    has the work. Returns (whether to take it, its Handoff)."""
    from . import git
    handoff = tasks.handoff(state['id'], workspace=workspace)
    commit = handoff.get('commit')
    if not commit or git.has_commit(folder['path'], commit):
        return True, handoff
    marker = f'{commit[:12]} {MISSING_WORK}'
    said = any(e['kind'] == 'comment' and e['actor'] == 'system' and marker in e['data'].get('text', '')
               for e in tasks.history(state['id'], workspace=workspace))
    if not said:
        tasks.comment(state['id'], f"The Dispatcher on this device did not {doing}: its last commit "
                      f"{marker} (branch {handoff.get('branch') or 'unknown'}), so it is left to the device that has "
                      "the work, or to you once that commit is pushed and fetched here.",
                      actor=SYSTEM, workspace=workspace)
    return False, handoff


def worker_client(state: dict, workspace: str) -> str | None:
    """The client of the session that did the Task's work."""
    worked_by = state.get('worked_by')
    if not worked_by:
        return None
    claims = [e for e in tasks.history(state['id'], workspace=workspace)
              if e['kind'] in ('claimed', 'moved') and e['session'] == worked_by and e.get('client')]
    return tasks.client_id(claims[-1]['client']) if claims else None


def reviewer(state: dict, choices: list[str], workspace: str) -> str | None:
    """The client that reviews ``state`` (ADR 0221): an allowed one other
    than the client that did the work, else that client in a fresh session."""
    from . import clients
    did = worker_client(state, workspace)
    others = [client for client in choices if client != did]
    if others:
        return clients.default(others)
    return did if did in choices else None


def review_base(state: dict, workspace: str) -> str | None:
    """The commit the Task's branch was made from, from the Dispatcher's first checkpoint on it (ADR 0223)."""
    for event in tasks.history(state['id'], workspace=workspace):
        if event['kind'] == 'checkpoint' and (event['session'] or '').startswith('dispatch:') and event['data'].get('commit'):
            return event['data']['commit']
    return None


REVIEW_ROUNDS = 2


def after_review(run: dict, outcome: str) -> None:
    """A Task sent back by ``REVIEW_ROUNDS`` reviews goes to the person (ADR 0221)."""
    if outcome != 'returned':
        return
    returned = sum(1 for r in store.runs_of(run['task']) if r['role'] == 'review' and r['outcome'] == 'returned')
    if returned < REVIEW_ROUNDS:
        return
    state = tasks.get(run['task'], workspace=run['workspace'])
    if 'user' not in state['assignees']:
        tasks.assign(run['task'], add=['user'], actor=SYSTEM, workspace=run['workspace'])
        tasks.comment(run['task'], f'Dispatched reviews sent this Task back {returned} times, so the Dispatcher leaves '
                      'it to you: the reviews above say what they found. Unassign yourself to let agents take it again.',
                      to=['user'], actor=SYSTEM, workspace=run['workspace'])
