"""How far each project and Feature is, and how long is left at the recent pace.

Progress is the Done Tasks out of every Task but the Cancelled ones. A
project counts its Tasks, not the Features that group them; a Feature counts
its own Tasks. How long is left is the Tasks still open divided by how many
were finished a day over the last ``PACE_DAYS`` days, estimated only once
``ESTIMATE_AFTER`` were finished in that time: with fewer, the pace says too
little to promise anything.
"""

from __future__ import annotations

import time

from .validation import project_filter
from .view import _archived, _order, listing

PACE_DAYS = 14
ESTIMATE_AFTER = 3
COUNTED = ('backlog', 'ready', 'in_progress', 'review', 'done')


def _summary(states: list[dict], now: float) -> dict:
    live = [s for s in states if s['status'] != 'cancelled']
    counts = {status: sum(1 for s in live if s['status'] == status) for status in COUNTED}
    total, done = len(live), counts['done']
    finished = sum(1 for s in live if s['status'] == 'done' and s.get('ended_at') and now - s['ended_at'] <= PACE_DAYS * 86400)
    pace = finished / PACE_DAYS
    remaining = total - done
    if total and not remaining:
        days_left = 0
    elif finished >= ESTIMATE_AFTER:
        days_left = remaining / pace
    else:
        days_left = None
    return {
        'total': total, 'done': done, 'cancelled': len(states) - len(live), 'counts': counts,
        'blocked': sum(1 for s in live if s['status'] != 'done' and (s.get('blocked') or s.get('open_blockers'))),
        'stale': sum(1 for s in live if s.get('stale')),
        'finished_recently': finished, 'pace': pace, 'days_left': days_left,
    }


def progress(*, project: str | None = None, feature: str | None = None, workspace: str | None = None) -> dict:
    """``{'projects': [...], 'features': [...]}``: each project's and each
    Feature's progress and estimate, Done and Cancelled Tasks of any age
    included. ``feature`` gives that Feature alone; ``project`` that project
    and its Features."""
    states = listing(archived=True, workspace=workspace)
    project = project_filter(project)
    now = time.time()
    features = [s for s in states if s['kind'] == 'feature']
    leaves = [s for s in states if s['kind'] != 'feature']
    projects = [] if feature else [
        {'name': name, **_summary([s for s in leaves if s['project'] == name], now)}
        for name in sorted({s['project'] for s in leaves}) if not project or name == project]
    wanted = [f for f in features if (not feature or f['id'] == feature.strip()) and (not project or f['project'] == project)]
    return {'projects': projects, 'features': [
        {'id': f['id'], 'title': f['title'], 'project': f['project'],
         **_summary([s for s in states if s['feature'] == f['id']], now)}
        for f in sorted(wanted, key=lambda f: f['title'].lower())]}


def _working_order(members: list[dict]) -> list[dict]:
    """A Feature's Tasks with each after the Tasks it waits on, and otherwise
    in the Board's order: the order they can be worked in."""
    ids = {m['id'] for m in members}
    waits = {m['id']: {b for b in m['blockers'] if b in ids} for m in members}
    left, placed, ordered = sorted(members, key=_order), set(), []
    while left:
        ready = next((m for m in left if waits[m['id']] <= placed), left[0])  # A cycle cannot be stored; just in case.
        ordered.append(ready)
        placed.add(ready['id'])
        left.remove(ready)
    return ordered


def features(*, project: str | None = None, archived: bool = False, workspace: str | None = None) -> list[dict]:
    """Every Feature with how far it is and its Tasks in working order
    (ADR 0242): ``[{'feature', 'summary', 'tasks'}]``, in the Board's order.
    A Feature that ended over 14 days ago is left out unless ``archived``;
    its Tasks are counted whatever their age."""
    states = listing(archived=True, workspace=workspace)
    project = project_filter(project)
    now = time.time()
    members: dict[str, list[dict]] = {}
    for state in states:
        if state['feature']:
            members.setdefault(state['feature'], []).append(state)
    found = [f for f in states if f['kind'] == 'feature' and (not project or f['project'] == project)
             and (archived or not _archived(f, now))]
    return [{'feature': f, 'summary': _summary(members.get(f['id'], []), now),
             'tasks': _working_order(members.get(f['id'], []))} for f in found]
