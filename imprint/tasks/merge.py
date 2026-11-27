"""The Board between paired devices (ADR 0209): its events merge by union,
each claim's lease keeps its latest renewal, each setting its newest change,
and the user's read marks add up. They travel as the per-workspace dataset
``tasks`` of the delta sync (``imprint.sync_delta``), keyed so one manifest
holds them all: ``e:<event>``, ``l:<claim>``, ``s:<setting>``, ``r:<comment>``.
Agents' names and read state stay on their device."""

from __future__ import annotations

import json
import re

from .store import _project, _reading, _writing
from .validation import _redacted


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
            event = {**event, 'data': _redacted_data(event['data'])}
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



# Fields of an event's data that hold someone's text; they are redacted again
# on arrival, as every write here is (ADR 0060).
_TEXT_FIELDS = ('title', 'description', 'text', 'reason', 'next')


def _redacted_data(data: dict) -> dict:
    data = dict(data)
    fields = [f for f in _TEXT_FIELDS if isinstance(data.get(f), str) and data[f]]
    criteria = data.get('acceptance') if isinstance(data.get('acceptance'), list) else None
    texts = [data[f] for f in fields] + [c for c in (criteria or []) if isinstance(c, str)]
    if not texts:
        return data
    redacted = _redacted(texts)
    for field, text in zip(fields, redacted):
        data[field] = text
    if criteria is not None:
        data['acceptance'] = redacted[len(fields):]
    return data


def sync_manifest(workspace: str | None = None) -> dict[str, float]:
    """Every record of the Board sync can change, by key, with the version
    the importer compares; empty, without making a store, when there is none."""
    with _reading(workspace) as conn:
        if conn is None:
            return {}
        found = {f"e:{row['id']}": 0.0 for row in conn.execute('SELECT id FROM events')}
        found.update({f"l:{row['claim']}": float(row['renewed_at']) for row in conn.execute('SELECT claim, renewed_at FROM leases')})
        found.update({f"s:{row['key']}": float(row['updated_at']) for row in conn.execute('SELECT key, updated_at FROM settings')})
        found.update({f"r:{row['key'][5:]}": 0.0 for row in conn.execute(
            "SELECT key FROM seen WHERE session = 'user' AND key LIKE 'read:%'")})
    return found


def sync_export(keys: list[str], workspace: str | None = None) -> list[dict]:
    """The records ``keys`` name, as the other device's importer takes them."""
    wanted = {kind: {key[2:] for key in keys if key.startswith(f'{kind}:')} for kind in 'elsr'}
    with _reading(workspace) as conn:
        if conn is None:
            return []
        rows = conn.execute('SELECT * FROM events ORDER BY hlc, id')
        records = [{'type': 'event', **event} for event in map(_event_record, rows) if event['id'] in wanted['e']]
        records += [{'type': 'lease', 'claim': row['claim'], 'renewed_at': row['renewed_at'], 'active_at': row['active_at'],
                     'device': row['device']} for row in conn.execute('SELECT * FROM leases') if row['claim'] in wanted['l']]
        records += [{'type': 'setting', 'key': row['key'], 'value': row['value'], 'updated_at': row['updated_at']}
                    for row in conn.execute('SELECT * FROM settings') if row['key'] in wanted['s']]
        records += [{'type': 'read', 'id': key} for key in sorted(wanted['r'])]
    return records


def _event_record(row) -> dict:
    event = dict(row)
    event['data'] = json.loads(event['data'])
    return event


def _number(value) -> float:
    if type(value) not in (int, float):
        raise ValueError('not a number')
    return float(value)


def sync_import(records: list[dict], workspace: str | None = None) -> dict[str, int]:
    """Store what a paired device sent: new events, newer lease renewals and
    settings, and read marks. Malformed records are skipped."""
    stats = {'inserted': 0, 'updated': 0, 'skipped': 0}
    events = [r for r in records if isinstance(r, dict) and r.get('type') == 'event']
    good = [{k: v for k, v in e.items() if k != 'type'} for e in events
            if all(field in e for field in _EVENT_FIELDS) and isinstance(e.get('data'), dict)
            and isinstance(e.get('id'), str) and re.fullmatch(r'[0-9a-f]{32}', e['id'])]
    stored = import_events(good, workspace=workspace) if good else 0
    stats['inserted'] += stored
    stats['skipped'] += len(events) - stored
    rest = [r for r in records if not (isinstance(r, dict) and r.get('type') == 'event')]
    if not rest:
        return stats
    with _writing(workspace) as conn:
        for record in rest:
            try:
                kind = record.get('type') if isinstance(record, dict) else None
                if kind == 'lease':
                    claim, renewed = str(record['claim']), _number(record['renewed_at'])
                    active = _number(record['active_at']) if record.get('active_at') is not None else None
                    held = conn.execute('SELECT renewed_at FROM leases WHERE claim = ?', (claim,)).fetchone()
                    if held is not None and held['renewed_at'] >= renewed:
                        stats['skipped'] += 1
                        continue
                    conn.execute('INSERT INTO leases (claim, renewed_at, active_at, pid, device) VALUES (?, ?, ?, NULL, ?) '
                                 'ON CONFLICT (claim) DO UPDATE SET renewed_at = excluded.renewed_at, '
                                 'active_at = MAX(COALESCE(active_at, 0), COALESCE(excluded.active_at, 0)), '
                                 'pid = NULL, device = excluded.device', (claim, renewed, active, str(record.get('device') or '')))
                    stats['updated' if held is not None else 'inserted'] += 1
                elif kind == 'setting':
                    key, value, at = str(record['key']), record['value'], _number(record['updated_at'])
                    if key != 'autonomy' or value not in ('off', 'stale', 'ready'):
                        raise ValueError(key)
                    held = conn.execute('SELECT updated_at FROM settings WHERE key = ?', (key,)).fetchone()
                    if held is not None and held['updated_at'] >= at:
                        stats['skipped'] += 1
                        continue
                    conn.execute('INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) '
                                 'DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at', (key, value, at))
                    stats['updated' if held is not None else 'inserted'] += 1
                elif kind == 'read':
                    comment = str(record['id'])
                    if not re.fullmatch(r'[0-9a-f]{32}', comment):
                        raise ValueError(comment)
                    added = conn.execute("INSERT OR IGNORE INTO seen (session, key) VALUES ('user', ?)",
                                         (f'read:{comment}',)).rowcount
                    stats['inserted' if added else 'skipped'] += 1
                else:
                    raise ValueError(kind)
            except (KeyError, TypeError, ValueError):
                stats['skipped'] += 1
    return stats
