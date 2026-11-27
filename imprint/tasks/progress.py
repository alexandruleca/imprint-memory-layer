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

from .view import listing

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
    now = time.time()
    features = [s for s in states if s['tasks']]
    leaves = [s for s in states if not s['tasks']]
    projects = [] if feature else [
        {'name': name, **_summary([s for s in leaves if s['project'] == name], now)}
        for name in sorted({s['project'] for s in leaves}) if not project or name == project]
    wanted = [f for f in features if (not feature or f['id'] == feature.strip()) and (not project or f['project'] == project)]
    return {'projects': projects, 'features': [
        {'id': f['id'], 'title': f['title'], 'project': f['project'],
         **_summary([s for s in states if s['feature'] == f['id']], now)}
        for f in sorted(wanted, key=lambda f: f['title'].lower())]}
