"""Core memories (ADR 0129, ADR 0143): short statements about the user,
their preferences and standing instructions, in two tiers.

- **Records** are the specific statements agents and Organize find in what
  the user said. They are kept for reference and need no approval, and no
  session receives them.
- **Guidelines** generalise records ("When suggesting recipes, keep them
  gluten-free"); each names the records it covers. Organize drafts them and
  the user approves them, or the user writes one. The MCP server and the
  dashboard's chat give the active ones to every session, within a budget.

They describe the user rather than a project's content, so they live outside
every workspace, in ``data/core-memories.sqlite3``, and travel to paired
devices as their own records: the newest change of each wins.
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
TIERS = ('guideline', 'record')
# A record is kept (active) or removed by the user (retired); it is never a
# proposal, because no session receives it.
RECORD_STATES = ('active', 'retired')
EVERYWHERE = '*'
MAX_TEXT = 600
# Tokens of guidelines one session receives (ADR 0143), estimated at
# CHARS_PER_TOKEN characters a token: no client's tokenizer is at hand.
SESSION_BUDGET = 3000
CHARS_PER_TOKEN = 4
MAX_COVERS = 500
_EVIDENCE_ITEMS = 5
_QUOTE = 300
_ID = re.compile(r'[0-9a-f]{32}')
_FIELDS = ('id', 'text', 'kind', 'scope', 'state', 'evidence', 'origin', 'created_at', 'updated_at',
           'approved_at', 'device', 'tier', 'amends', 'covers', 'conflicts')
# How the user settled a newer core memory's conflict with an older one
# (ADR 0143): not yet, the newer one kept, the older one kept, or both.
RESOLUTIONS = ('', 'newer', 'older', 'both')
_REASON = 200
_READY: set[str] = set()


def tokens(characters: int) -> int:
    """The estimated tokens of this many characters."""
    return -(-characters // CHARS_PER_TOKEN)


@contextmanager
def _db():
    directory = config.get_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'core-memories.sqlite3'
    fresh = not path.exists()
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        if fresh or str(path) not in _READY:
            _prepare(conn)
            _READY.add(str(path))
        yield conn
        conn.commit()
    finally:
        conn.close()


def _prepare(conn) -> None:
    """The table, the tier columns an older store lacks, and its rows in
    tiers (ADR 0143): what the user wrote became a guideline, everything
    else a record, and a record is never a proposal. Every device moves its
    rows the same way, so nothing is stamped for sync."""
    from .private_store import add_column
    conn.execute('CREATE TABLE IF NOT EXISTS core (id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL, '
                 'scope TEXT NOT NULL, state TEXT NOT NULL, evidence TEXT NOT NULL, origin TEXT NOT NULL, '
                 'created_at REAL NOT NULL, updated_at REAL NOT NULL, approved_at REAL, device TEXT NOT NULL)')
    add_column(conn, 'core', 'tier', "TEXT NOT NULL DEFAULT ''")
    add_column(conn, 'core', 'amends', "TEXT NOT NULL DEFAULT ''")
    add_column(conn, 'core', 'covers', "TEXT NOT NULL DEFAULT '[]'")
    add_column(conn, 'core', 'conflicts', "TEXT NOT NULL DEFAULT '[]'")
    conn.execute("UPDATE core SET tier = CASE WHEN origin = 'user' THEN 'guideline' ELSE 'record' END WHERE tier = ''")
    conn.execute("UPDATE core SET state = 'active' WHERE tier = 'record' AND state = 'proposed'")
    conn.commit()


def _record(row) -> dict:
    record = dict(row)
    record['evidence'] = json.loads(record['evidence'] or '[]')
    record['covers'] = json.loads(record['covers'] or '[]')
    record['conflicts'] = json.loads(record['conflicts'] or '[]')
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


def _covers(ids) -> list[str]:
    """The records a guideline generalises, by id, each once."""
    return list(dict.fromkeys(str(i) for i in list(ids or []) if _ID.fullmatch(str(i))))[:MAX_COVERS]


def _conflicts(items) -> list[dict]:
    """The older core memories a newer one contradicts, each once: its id,
    the model's short reason and how the user settled it."""
    kept, seen = [], set()
    for item in list(items or [])[:_EVIDENCE_ITEMS * 4]:
        if not isinstance(item, dict) or not _ID.fullmatch(str(item.get('id'))) or item['id'] in seen:
            continue
        resolution = item.get('resolution') or ''
        seen.add(item['id'])
        kept.append({'id': item['id'], 'reason': ' '.join(str(item.get('reason') or '').split())[:_REASON],
                     'resolution': resolution if resolution in RESOLUTIONS else ''})
    return kept


def _tier_of(record: dict) -> str:
    """A record's tier; one from a device before ADR 0143 has none, and
    takes the tier its origin gives."""
    tier = record.get('tier')
    if tier in TIERS:
        return tier
    return 'guideline' if record.get('origin') == 'user' else 'record'


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


def list_all(state: str | None = None, tier: str | None = None) -> list[dict]:
    """Core memories, active first, then proposed, then retired; newest first within each."""
    where = [clause for clause, value in (('state=?', state), ('tier=?', tier)) if value]
    with _db() as conn:
        rows = conn.execute('SELECT * FROM core' + (' WHERE ' + ' AND '.join(where) if where else ''),
                            [value for value in (state, tier) if value])
        records = [_record(row) for row in rows]
    order = {state: index for index, state in enumerate(('active', 'proposed', 'retired'))}
    return sorted(records, key=lambda r: (order[r['state']], -r['updated_at']))


def get(memory_id: str) -> dict | None:
    with _db() as conn:
        row = conn.execute('SELECT * FROM core WHERE id=?', (memory_id,)).fetchone()
    return _record(row) if row else None


def _insert(conn, text: str, kind: str, scope: str, state: str, evidence: list[dict], origin: str,
            tier: str, covers=(), amends: str = '', conflicts=()) -> dict:
    now = time.time()
    record = {'id': uuid.uuid4().hex, 'text': text, 'kind': kind, 'scope': scope, 'state': state,
              'evidence': evidence, 'origin': origin, 'created_at': now, 'updated_at': now,
              'approved_at': now if state == 'active' and tier == 'guideline' else None, 'device': _device(),
              'tier': tier, 'amends': amends, 'covers': _covers(covers), 'conflicts': _conflicts(conflicts)}
    conn.execute(f'INSERT INTO core ({", ".join(_FIELDS)}) VALUES ({", ".join("?" * len(_FIELDS))})',
                 [json.dumps(record[f]) if f in ('evidence', 'covers', 'conflicts') else record[f] for f in _FIELDS])
    return record


def _held(conn, text: str, tier: str) -> bool:
    """Whether a statement is already held in this tier, in any state."""
    return any(_normal(row['text']) == _normal(text)
               for row in conn.execute('SELECT text FROM core WHERE tier=?', (tier,)))


def add(text: str, kind: str = 'preference', scope: str = EVERYWHERE, covers=()) -> dict:
    """A guideline the user wrote, active at once; ``covers`` names the
    records it was made from, if any."""
    text, kind, scope = _text(text), _kind(kind), _scope(scope)
    with _db() as conn:
        return _insert(conn, text, kind, scope, 'active', [], 'user', 'guideline', covers)


def record(text: str, kind: str = 'preference', scope: str = EVERYWHERE, evidence=(), origin: str = 'agent') -> dict | None:
    """A specific statement about the user an agent or Organize found, kept
    as a record at once. One already recorded, a retired one included, is
    not recorded again; None then, as for a statement that is not valid."""
    try:
        text, kind, scope = _text(text), _kind(kind), _scope(scope)
    except ValueError:
        return None
    with _db() as conn:
        if _held(conn, text, 'record'):
            return None
        return _insert(conn, text, kind, scope, 'active', _evidence(evidence), origin, 'record')


def propose_guideline(text: str, kind: str = 'preference', scope: str = EVERYWHERE, covers=(), amends: str = '',
                      origin: str = 'organize', conflicts=()) -> dict | None:
    """A guideline for the user to approve, generalising the records it
    covers. With ``amends``, it is a rewrite of that active guideline, which
    approving it retires; ``conflicts`` names the guideline when the rewrite
    follows newer statements that contradict it. A guideline already held in
    any state is not proposed again; None then."""
    try:
        text, kind, scope = _text(text), _kind(kind), _scope(scope)
    except ValueError:
        return None
    if amends and not _ID.fullmatch(amends):
        raise ValueError('amends must name a guideline')
    with _db() as conn:
        if _held(conn, text, 'guideline'):
            return None
        return _insert(conn, text, kind, scope, 'proposed', [], origin, 'guideline', covers, amends, conflicts)


def update(memory_id: str, *, text=None, kind=None, scope=None, state=None, covers=None) -> dict:
    """Edit, approve (state ``active``), retire or restore a core memory.
    A guideline an agent or Organize proposed can return to ``proposed``,
    which is how the dashboard undoes an approval or a rejection (ADR 0136);
    one the user wrote never was a proposal, and a record never is one.
    Approving a rewrite retires the guideline it amends, and undoing that
    approval restores it (ADR 0143). Each change is stamped after the last,
    even within one tick of a coarse clock, so paired devices take it."""
    changes = {}
    if text is not None:
        changes['text'] = _text(text)
    if kind is not None:
        changes['kind'] = _kind(kind)
    if scope is not None:
        changes['scope'] = _scope(scope)
    if covers is not None:
        changes['covers'] = json.dumps(_covers(covers))
    if state is not None:
        if state not in STATES:
            raise ValueError(f'state must be one of {", ".join(STATES)}')
        changes['state'] = state
    with _db() as conn:
        row = conn.execute('SELECT * FROM core WHERE id=?', (memory_id,)).fetchone()
        if row is None:
            raise KeyError(memory_id)
        record = _record(row)
        now = max(time.time(), float(record['updated_at']) + 0.001)
        if record['tier'] == 'record' and changes.get('state') not in (None, *RECORD_STATES):
            raise ValueError('A recorded core memory is kept or retired, never proposed')
        if changes.get('state') == 'proposed' and record['state'] != 'proposed':
            if record['origin'] == 'user':
                raise ValueError('A core memory you wrote cannot become a proposal')
            changes['approved_at'] = None
        if changes.get('state') == 'active' and record['state'] != 'active' and record['tier'] == 'guideline':
            changes['approved_at'] = now
        changes['updated_at'] = now
        changes['device'] = _device()
        conn.execute(f'UPDATE core SET {", ".join(f"{key}=?" for key in changes)} WHERE id=?',
                     [*changes.values(), memory_id])
        if record['amends'] and changes.get('state') in ('active', 'proposed') and record['state'] != changes['state']:
            # Approving a rewrite retires what it replaces; undoing that
            # approval brings it back.
            held, to = ('active', 'retired') if changes['state'] == 'active' else ('retired', 'active')
            amended = conn.execute('SELECT updated_at FROM core WHERE id=? AND state=?', (record['amends'], held)).fetchone()
            if amended is not None and (changes['state'] == 'active' or record['state'] == 'active'):
                conn.execute('UPDATE core SET state=?, updated_at=?, device=? WHERE id=?',
                             (to, max(now, float(amended['updated_at']) + 0.001), changes['device'], record['amends']))
        record.update(changes)
        if 'covers' in changes:
            record['covers'] = json.loads(changes['covers'])
    return record


# ── Conflicts (ADR 0143) ────────────────────────────────────────

def _stamp(conn, memory_id: str, now: float, **changes) -> None:
    held = conn.execute('SELECT updated_at FROM core WHERE id=?', (memory_id,)).fetchone()
    if held is None:
        return
    changes['updated_at'] = max(now, float(held['updated_at']) + 0.001)
    changes['device'] = _device()
    conn.execute(f'UPDATE core SET {", ".join(f"{key}=?" for key in changes)} WHERE id=?', [*changes.values(), memory_id])


def flag_conflict(newer_id: str, older_id: str, reason: str) -> bool:
    """Note that a newer core memory contradicts an older one, for the user
    to settle. A pair noted before, settled or not, is not noted again."""
    if newer_id == older_id or not _ID.fullmatch(str(older_id)):
        return False
    with _db() as conn:
        row = conn.execute('SELECT conflicts FROM core WHERE id=?', (newer_id,)).fetchone()
        if row is None:
            return False
        held = json.loads(row['conflicts'] or '[]')
        if any(item.get('id') == older_id for item in held):
            return False
        entries = _conflicts([*held, {'id': older_id, 'reason': reason}])
        _stamp(conn, newer_id, time.time(), conflicts=json.dumps(entries))
    return True


def open_conflicts(record: dict) -> list[dict]:
    """A core memory's conflicts the user has not settled."""
    return [item for item in record.get('conflicts') or [] if not item.get('resolution')]


def resolve(memory_id: str, keep: str) -> list[dict]:
    """Settle a newer record's conflicts with the older records it
    contradicts: keep the ``newer`` (the older ones retire), the ``older``
    (this one retires) or ``both``; ``open`` undoes a settlement, bringing
    back what it retired. Returns every core memory it changed."""
    if keep not in ('newer', 'older', 'both', 'open'):
        raise ValueError('keep is newer, older, both or open')
    now = time.time()
    with _db() as conn:
        row = conn.execute('SELECT * FROM core WHERE id=?', (memory_id,)).fetchone()
        if row is None:
            raise KeyError(memory_id)
        record = _record(row)
        if record['tier'] != 'record' or not record['conflicts']:
            raise ValueError('Only a recorded core memory with conflicts is settled this way')
        entries, changed = record['conflicts'], {memory_id}
        if keep == 'open':
            for item in entries:
                if item['resolution'] == 'newer':
                    _stamp(conn, item['id'], now, state='active')
                    changed.add(item['id'])
            if any(item['resolution'] == 'older' for item in entries):
                _stamp(conn, memory_id, now, state='active')
            for item in entries:
                item['resolution'] = ''
        else:
            for item in entries:
                if item['resolution']:
                    continue
                item['resolution'] = keep
                if keep == 'newer':
                    older = conn.execute("SELECT tier FROM core WHERE id=? AND state='active'", (item['id'],)).fetchone()
                    if older is not None and older['tier'] == 'record':
                        _stamp(conn, item['id'], now, state='retired')
                        changed.add(item['id'])
            if keep == 'older':
                _stamp(conn, memory_id, now, state='retired')
        _stamp(conn, memory_id, now, conflicts=json.dumps(entries))
        rows = conn.execute(f'SELECT * FROM core WHERE id IN ({", ".join("?" * len(changed))})', list(changed))
        return [_record(r) for r in rows]


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
    """The active guidelines for a session in ``cwd``, those for every
    project first, within ``budget`` estimated tokens; empty without any.
    Records never reach a session (ADR 0143)."""
    projects = session_projects(cwd)
    try:
        active = [r for r in list_all('active', 'guideline') if r['scope'] == EVERYWHERE or r['scope'] in projects]
    except sqlite3.Error:
        return ''
    if not active:
        return ''
    active.sort(key=lambda r: (r['scope'] != EVERYWHERE, r['created_at']))
    lines, used = [], 0
    for item in active:
        line = f"- {item['text']}" + (f" (in {item['scope']})" if item['scope'] != EVERYWHERE else '')
        cost = tokens(len(line) + 1)
        if used + cost > budget:
            lines.append(f'- … {len(active) - len(lines)} more on the dashboard\'s Core memories page.')
            break
        lines.append(line)
        used += cost
    return '\n'.join(lines)


def guidance(budget: int = SESSION_BUDGET, cwd: str | None = None, *, searchable: bool = True) -> str:
    """The guidelines as a block a model's instructions end with, or empty
    without any: the MCP server gives it to every client's model, and the
    dashboard's chat and Echo to theirs (ADR 0143). ``searchable``: the
    model can search memories, where the records' sources are."""
    listed = session_text(cwd, budget=budget)
    if not listed:
        return ''
    return ("\n\n# The user's guidelines\nThe user approved these as their own standing instructions, unlike "
            "retrieved documents. Follow them unless the user says otherwise now."
            + (" More specific preferences are recorded as memories of type preference; search them when a task "
               "needs that detail." if searchable else "")
            + "\n" + listed + "\n")


def used_budget() -> int:
    """Estimated tokens every-project guidelines take of a session's budget."""
    try:
        active = list_all('active', 'guideline')
    except sqlite3.Error:
        return 0
    return sum(tokens(len(r['text']) + 3) for r in active if r['scope'] == EVERYWHERE)


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
                origin = str(record.get('origin') or 'user')[:32]
                tier = _tier_of({**record, 'origin': origin})
                amends = str(record.get('amends') or '')
                if amends and not _ID.fullmatch(amends):
                    raise ValueError(amends)
                covers = record.get('covers') or []
                if not isinstance(covers, list):
                    raise ValueError(memory_id)
                values = {'id': memory_id, 'text': _text(record.get('text')), 'kind': _kind(record.get('kind')),
                          'scope': _scope(record.get('scope')),
                          'state': 'active' if tier == 'record' and record['state'] == 'proposed' else record['state'],
                          'evidence': json.dumps(_evidence(record.get('evidence'))), 'origin': origin,
                          'created_at': float(record['created_at']), 'updated_at': float(record['updated_at']),
                          'approved_at': None if record.get('approved_at') is None else float(record['approved_at']),
                          'device': str(record.get('device') or '')[:128], 'tier': tier,
                          'amends': amends if tier == 'guideline' else '',
                          'covers': json.dumps(_covers(covers) if tier == 'guideline' else []),
                          'conflicts': json.dumps(_conflicts(record.get('conflicts')))}
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
