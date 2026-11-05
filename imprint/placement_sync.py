"""Placement rules travel between paired devices (ADR 0134).

A rule says which project the memories of one origin project, or of one
source key of it, are placed in (ADR 0036). Each rule carries the time it
last changed; a removed rule stays as an empty target, so its removal
travels too. A device takes a peer's rule when it is newer than its own,
then moves its own copies of the memories it governs, as a move made there.

Applying needs the ingestion lock, like any move. When a job holds it, the
dashboard's queue applies the rules after that job (``apply-placements``).
"""
from __future__ import annotations

import json
import math
import sys

COMMAND = 'apply-placements'


def _key(origin: str, source: str) -> str:
    return json.dumps([origin, source], ensure_ascii=False)


def manifest(workspace: str) -> dict[str, float]:
    """Every rule of the workspace, removed ones included, by its change time."""
    from . import workspace_policy as policy
    with policy._db() as conn:
        return {_key(project, source): float(updated or 0) for project, source, updated in conn.execute(
            'SELECT project, source, updated_at FROM placements WHERE workspace=?', (workspace,))}


def export(workspace: str, keys) -> list[dict]:
    from . import workspace_policy as policy
    wanted = set(keys)
    with policy._db() as conn:
        rows = conn.execute('SELECT project, source, target, updated_at FROM placements WHERE workspace=?',
                            (workspace,)).fetchall()
    return [{'origin': project, 'source': source, 'target': target, 'updated_at': float(updated or 0)}
            for project, source, target, updated in rows if _key(project, source) in wanted]


def _name(value, *, empty: bool) -> str:
    from .projects import project_name
    if value == '' and empty:
        return ''
    return project_name(value)


def _record(record) -> tuple[tuple[str, str], str, float]:
    """A rule from a peer as (origin, source), target, time; ValueError
    when it is not one."""
    if not isinstance(record, dict):
        raise ValueError('not a rule')
    source = record.get('source')
    if not isinstance(source, str) or len(source) > 4096 or not source.isprintable():
        raise ValueError('source')
    # Memories without a project are placed source by source only (ADR 0130).
    origin = _name(record.get('origin'), empty=bool(source))
    target = _name(record.get('target'), empty=True)
    updated = record.get('updated_at')
    if type(updated) not in (int, float) or not math.isfinite(updated) or updated <= 0:
        raise ValueError('time')
    return (origin, source), target, float(updated)


def import_records(records: list, workspace: str) -> dict[str, int]:
    """Store the peer's rules that are newer than this device's, then place
    this device's memories by them. Returns counts, and ``moved``."""
    from . import workspace_policy as policy
    from .projects import _placement_rules, placement
    stats = {'inserted': 0, 'updated': 0, 'skipped': 0, 'moved': 0}
    changed: set[tuple[str, str]] = set()
    with policy._db() as conn:
        before = _placement_rules(conn, workspace)
        for record in records:
            try:
                key, target, updated = _record(record)
            except (TypeError, ValueError):
                stats['skipped'] += 1
                continue
            held = conn.execute('SELECT target, updated_at FROM placements WHERE workspace=? AND project=? AND source=?',
                                (workspace, *key)).fetchone()
            if held is not None and float(held[1] or 0) >= updated:
                stats['skipped'] += 1
                continue
            conn.execute('INSERT OR REPLACE INTO placements (workspace, project, source, target, updated_at) '
                         'VALUES (?, ?, ?, ?, ?)', (workspace, *key, target, updated))
            stats['updated' if held is not None else 'inserted'] += 1
            changed.add(key)
        after = _placement_rules(conn, workspace)
    # The memories a changed rule governs sit where the old rules put them.
    projects = {placement(origin, source, before) for origin, source in changed
                if placement(origin, source, before) != placement(origin, source, after)}
    if projects:
        stats['moved'] = apply(workspace, projects)
    return stats


def apply(workspace: str, projects: set[str] | None = None) -> int:
    """Place the memories in ``projects`` (all when None) by the current
    rules now, holding the ingestion lock; when a job holds it, queue that
    for after it. Returns how many memories moved now."""
    from . import queue_lock, workspace_policy as policy
    from .projects import _apply_rules, _placement_rules
    fd = queue_lock.acquire(COMMAND, '', block=False)
    if fd is None:
        from . import queue
        queue.submit(COMMAND, {'workspace': workspace})
        return 0
    try:
        with policy._db() as conn:
            rules = _placement_rules(conn, workspace)
        return _apply_rules(workspace, rules, projects)
    finally:
        queue_lock.release(fd)


def main(argv: list[str] | None = None) -> int:
    """The queued job: place every memory of the session's workspace by the
    rules, waiting for the lock."""
    from . import queue_lock, workspace_policy as policy
    from .projects import _apply_rules, _placement_rules
    workspace = policy.workspace_name(None)
    fd = queue_lock.acquire(COMMAND, '', block=True)
    try:
        with policy._db() as conn:
            rules = _placement_rules(conn, workspace)
        moved = _apply_rules(workspace, rules)
        print(f'Placed {moved} memories by the project rules paired devices sent.', flush=True)
        return 0
    finally:
        if fd is not None:
            queue_lock.release(fd)


if __name__ == '__main__':
    sys.exit(main())
