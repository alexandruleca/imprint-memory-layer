"""Merging events from another device (ADR 0209)."""

from __future__ import annotations

import json
import re

from .store import _project, _writing


_EVENT_FIELDS = ('id', 'task', 'kind', 'data', 'hlc', 'device', 'actor', 'recorded_at')


_HLC = re.compile(r'^(\d{13})\.(\d{4})$')


def import_events(events: list[dict], workspace: str | None = None) -> int:
    """Keep every event not yet held, recompute the Tasks they touch, and
    move this store's clock past theirs. Returns how many were new."""
    stored, touched = 0, set()
    with _writing(workspace) as conn:
        for event in events:
            if not all(field in event for field in _EVENT_FIELDS) or not isinstance(event['data'], dict):
                continue
            clock = _HLC.match(str(event['hlc']))
            if not clock or event['actor'] not in ('user', 'agent'):
                continue
            cursor = conn.execute(
                'INSERT OR IGNORE INTO events (id, task, kind, data, hlc, device, actor, session, agent, client, recorded_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (str(event['id']), str(event['task']), str(event['kind']), json.dumps(event['data'], ensure_ascii=False),
                 event['hlc'], str(event['device']), event['actor'], event.get('session'), event.get('agent'),
                 event.get('client'), float(event['recorded_at'])))
            if cursor.rowcount:
                stored += 1
                touched.add(str(event['task']))
                wall, counter = int(clock.group(1)), int(clock.group(2))
                row = conn.execute('SELECT wall, counter FROM clock').fetchone()
                if row is None or (wall, counter) > (row['wall'], row['counter']):
                    conn.execute('INSERT INTO clock (one, wall, counter) VALUES (1, ?, ?) ON CONFLICT (one) DO UPDATE '
                                 'SET wall = excluded.wall, counter = excluded.counter', (wall, counter))
        for uid in touched:
            if conn.execute("SELECT 1 FROM events WHERE task = ? AND kind = 'deleted'", (uid,)).fetchone():
                conn.execute("UPDATE events SET data = '{}' WHERE task = ? AND kind != 'deleted'", (uid,))
            try:
                _project(conn, uid)
            except LookupError:
                pass  # Its creation has not arrived yet.
    return stored
