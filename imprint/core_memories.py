"""Core memories (ADR 0129): short statements about the user, their
preferences and standing instructions, that the user approved.

They describe the user rather than a project's content, so they live outside
every workspace, in ``data/core-memories.sqlite3``, and travel to paired
devices as their own records: the newest change of each wins. Agents and the
organize job only propose them; only the user makes one active. The MCP
server hands the active ones to every session in its instructions.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import config

KINDS = ('preference', 'instruction', 'fact')
STATES = ('proposed', 'active', 'retired')
EVERYWHERE = '*'
MAX_TEXT = 600
# Characters of core memories one session receives.
SESSION_BUDGET = 2000
_EVIDENCE_ITEMS = 5
_QUOTE = 300
_ID = re.compile(r'[0-9a-f]{32}')
_FIELDS = ('id', 'text', 'kind', 'scope', 'state', 'evidence', 'origin', 'created_at', 'updated_at',
           'approved_at', 'device')


@contextmanager
def _db():
    directory = config.get_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(directory / 'core-memories.sqlite3', timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS core (id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL, '
                     'scope TEXT NOT NULL, state TEXT NOT NULL, evidence TEXT NOT NULL, origin TEXT NOT NULL, '
                     'created_at REAL NOT NULL, updated_at REAL NOT NULL, approved_at REAL, device TEXT NOT NULL)')
        yield conn
        conn.commit()
    finally:
        conn.close()


def _record(row) -> dict:
    record = dict(row)
    record['evidence'] = json.loads(record['evidence'] or '[]')
    return record


def _normal(text: str) -> str:
    return ' '.join(text.split()).casefold()


def _text(value) -> str:
    """The redacted statement: one to ``MAX_TEXT`` characters (ADR 0060)."""
    text = ' '.join(str(value or '').split())
    if not text or len(text) > MAX_TEXT:
        raise ValueError(f'A core memory is 1-{MAX_TEXT} characters')
    from . import redaction
    return redaction.redact(text)


def _scope(value) -> str:
    if value in (None, '', EVERYWHERE):
        return EVERYWHERE
    from .projects import project_name
    return project_name(value)


def _kind(value) -> str:
    if value not in KINDS:
        raise ValueError(f'kind must be one of {", ".join(KINDS)}')
    return value


def _evidence(items) -> list[dict]:
    """At most a few short pointers to where a proposal came from."""
    kept = []
    for item in list(items or [])[:_EVIDENCE_ITEMS]:
        if isinstance(item, dict):
            entry = {key: str(item[key])[:_QUOTE] for key in ('memory_id', 'source', 'project', 'quote') if item.get(key)}
            if entry:
                kept.append(entry)
    return kept


def _device() -> str:
    try:
        from .device import current
        return current()['id']
    except (OSError, ValueError):
        return ''


def list_all(state: str | None = None) -> list[dict]:
    """Core memories, active first, then proposed, then retired; newest first within each."""
    with _db() as conn:
        rows = conn.execute('SELECT * FROM core' + (' WHERE state=?' if state else ''), (state,) if state else ())
        records = [_record(row) for row in rows]
    order = {state: index for index, state in enumerate(('active', 'proposed', 'retired'))}
    return sorted(records, key=lambda r: (order[r['state']], -r['updated_at']))


def get(memory_id: str) -> dict | None:
    with _db() as conn:
        row = conn.execute('SELECT * FROM core WHERE id=?', (memory_id,)).fetchone()
    return _record(row) if row else None


def _insert(conn, text: str, kind: str, scope: str, state: str, evidence: list[dict], origin: str) -> dict:
    now = time.time()
    record = {'id': uuid.uuid4().hex, 'text': text, 'kind': kind, 'scope': scope, 'state': state,
              'evidence': evidence, 'origin': origin, 'created_at': now, 'updated_at': now,
              'approved_at': now if state == 'active' else None, 'device': _device()}
    conn.execute(f'INSERT INTO core ({", ".join(_FIELDS)}) VALUES ({", ".join("?" * len(_FIELDS))})',
                 [json.dumps(record[f]) if f == 'evidence' else record[f] for f in _FIELDS])
    return record


def add(text: str, kind: str = 'preference', scope: str = EVERYWHERE) -> dict:
    """A core memory the user wrote: active at once."""
    text, kind, scope = _text(text), _kind(kind), _scope(scope)
    with _db() as conn:
        return _insert(conn, text, kind, scope, 'active', [], 'user')


def propose(text: str, kind: str = 'preference', scope: str = EVERYWHERE, evidence=(), origin: str = 'agent') -> dict | None:
    """A candidate for the user to approve. A statement already held in any
    state, a retired one included, is not proposed again; None then."""
    try:
        text, kind, scope = _text(text), _kind(kind), _scope(scope)
    except ValueError:
        return None
    with _db() as conn:
        if any(_normal(row['text']) == _normal(text) for row in conn.execute('SELECT text FROM core')):
            return None
        return _insert(conn, text, kind, scope, 'proposed', _evidence(evidence), origin)


def update(memory_id: str, *, text=None, kind=None, scope=None, state=None) -> dict:
    """Edit, approve (state ``active``), retire or restore a core memory."""
    changes = {}
    if text is not None:
        changes['text'] = _text(text)
    if kind is not None:
        changes['kind'] = _kind(kind)
    if scope is not None:
        changes['scope'] = _scope(scope)
    if state is not None:
        if state not in STATES:
            raise ValueError(f'state must be one of {", ".join(STATES)}')
        changes['state'] = state
    with _db() as conn:
        row = conn.execute('SELECT * FROM core WHERE id=?', (memory_id,)).fetchone()
        if row is None:
            raise KeyError(memory_id)
        record = _record(row)
        if changes.get('state') == 'proposed' and record['state'] != 'proposed':
            raise ValueError('An approved or retired core memory cannot become a proposal again')
        now = time.time()
        if changes.get('state') == 'active' and record['state'] != 'active':
            changes['approved_at'] = now
        changes['updated_at'] = now
        changes['device'] = _device()
        conn.execute(f'UPDATE core SET {", ".join(f"{key}=?" for key in changes)} WHERE id=?',
                     [*changes.values(), memory_id])
        record.update(changes)
    return record


# ── What a session receives ─────────────────────────────────────

def session_projects(cwd: str | None) -> set[str]:
    """The names a session's folder may go by: the nearest project
    manifest's name and the folder's own name."""
    if not cwd:
        return set()
    from .projects import MANIFESTS, _extract_name
    try:
        path = Path(cwd).resolve()
    except OSError:
        return set()
    names = {path.name} if path.name else set()
    for directory in (path, *path.parents):
        for manifest in MANIFESTS:
            if (directory / manifest).is_file():
                names |= {_extract_name(directory / manifest, manifest, directory), directory.name}
                return {name for name in names if name}
    return names


def session_text(cwd: str | None = None, budget: int = SESSION_BUDGET) -> str:
    """The active core memories for a session in ``cwd``, those for every
    project first, within ``budget`` characters; empty without any."""
    projects = session_projects(cwd)
    try:
        active = [r for r in list_all('active') if r['scope'] == EVERYWHERE or r['scope'] in projects]
    except sqlite3.Error:
        return ''
    if not active:
        return ''
    active.sort(key=lambda r: (r['scope'] != EVERYWHERE, r['created_at']))
    lines, used = [], 0
    for record in active:
        line = f"- {record['text']}" + (f" (in {record['scope']})" if record['scope'] != EVERYWHERE else '')
        if used + len(line) + 1 > budget:
            lines.append(f'- … {len(active) - len(lines)} more on the dashboard\'s Core memories page.')
            break
        lines.append(line)
        used += len(line) + 1
    return '\n'.join(lines)


def used_budget() -> int:
    """Characters every-project core memories take of a session's budget."""
    return len(session_text(None, budget=10 ** 9))


# ── Sync (ADR 0129): the newest change of each record wins ──────

def manifest() -> dict[str, float]:
    with _db() as conn:
        return {row['id']: float(row['updated_at']) for row in conn.execute('SELECT id, updated_at FROM core')}


def export(ids) -> list[dict]:
    wanted = set(ids)
    with _db() as conn:
        return [_record(row) for row in conn.execute('SELECT * FROM core') if row['id'] in wanted]


def import_records(records: list[dict]) -> dict[str, int]:
    """Store records a paired device sent: new ones, and newer versions of
    held ones. A record that does not validate is skipped."""
    stats = {'inserted': 0, 'updated': 0, 'skipped': 0}
    with _db() as conn:
        for record in records:
            try:
                memory_id = str(record['id'])
                if not _ID.fullmatch(memory_id) or record.get('state') not in STATES:
                    raise ValueError(memory_id)
                values = {'id': memory_id, 'text': _text(record.get('text')), 'kind': _kind(record.get('kind')),
                          'scope': _scope(record.get('scope')), 'state': record['state'],
                          'evidence': json.dumps(_evidence(record.get('evidence'))),
                          'origin': str(record.get('origin') or 'user')[:32],
                          'created_at': float(record['created_at']), 'updated_at': float(record['updated_at']),
                          'approved_at': None if record.get('approved_at') is None else float(record['approved_at']),
                          'device': str(record.get('device') or '')[:128]}
            except (KeyError, TypeError, ValueError):
                stats['skipped'] += 1
                continue
            held = conn.execute('SELECT updated_at FROM core WHERE id=?', (memory_id,)).fetchone()
            if held is not None and held['updated_at'] >= values['updated_at']:
                stats['skipped'] += 1
                continue
            conn.execute(f'INSERT OR REPLACE INTO core ({", ".join(_FIELDS)}) VALUES ({", ".join("?" * len(_FIELDS))})',
                         [values[f] for f in _FIELDS])
            stats['updated' if held else 'inserted'] += 1
    return stats
