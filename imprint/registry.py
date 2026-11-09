"""Skills and personas (ADR 0168): the user's own instructions for coding
agents, kept once and synced to paired devices.

- A **skill** is a folder with a ``SKILL.md`` (YAML front matter with
  ``name`` and ``description``, then instructions) and optional supporting
  files, in the Agent Skills layout the clients read.
- A **persona** is one Markdown file with the same front matter followed by
  the role's prompt; Claude Code reads it as a subagent.

Both belong to the user rather than to a workspace, so they live outside
every workspace, in ``data/registry.sqlite3``. Each item is stored as its
files, verbatim, with the content hash of those files; ``registry_files``
writes them into the folders the connected clients read and takes edits
made there back (ADR 0169). Paired devices exchange items as their own
dataset; the newest change of each wins, and the version it replaced stays
in this device's history, which is never synced.

Items hold text files only. A file that holds what looks like a credential
is refused rather than stored redacted (ADR 0060): a redacted script would
no longer run, and the folder in the client would be rewritten with it.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import PurePosixPath

from . import config

KINDS = ('skill', 'persona')
# The file that carries an item's front matter. A persona is that one file,
# written into the client as ``<name>.md``.
MAIN = {'skill': 'SKILL.md', 'persona': 'PERSONA.md'}
STATES = ('active', 'disabled', 'retired')
ORIGINS = ('user', 'adopted', 'client')
# Agent Skills names: lower-case letters, digits and single hyphens, at most
# 64 characters, the same as the folder's name (Cursor and Codex check it).
NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_FILE = 256 * 1024
MAX_TOTAL = 1024 * 1024
MAX_FILES = 64
# Versions of one item this device keeps after they were replaced.
HISTORY = 10
# Files and folders a client or an operating system leaves in a skill's
# folder; they are not part of the skill.
IGNORED = frozenset({'.DS_Store', 'Thumbs.db', 'desktop.ini', '__pycache__', '.git', 'node_modules'})
_ID = re.compile(r'[0-9a-f]{32}')
_FRONT = re.compile(r'\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)', re.S)
_FIELDS = ('id', 'kind', 'name', 'description', 'files', 'hash', 'state', 'origin', 'device',
           'created_at', 'updated_at')
_READY: set[str] = set()


@contextmanager
def _db():
    directory = config.get_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'registry.sqlite3'
    fresh = not path.exists()
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        if fresh or str(path) not in _READY:
            conn.execute('CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, kind TEXT NOT NULL, '
                         'name TEXT NOT NULL, description TEXT NOT NULL, files TEXT NOT NULL, hash TEXT NOT NULL, '
                         'state TEXT NOT NULL, origin TEXT NOT NULL, device TEXT NOT NULL, '
                         'created_at REAL NOT NULL, updated_at REAL NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS versions (item_id TEXT NOT NULL, hash TEXT NOT NULL, '
                         'files TEXT NOT NULL, state TEXT NOT NULL, device TEXT NOT NULL, updated_at REAL NOT NULL, '
                         'replaced_at REAL NOT NULL, PRIMARY KEY (item_id, hash))')
            conn.commit()
            _READY.add(str(path))
        yield conn
        conn.commit()
    finally:
        conn.close()


def _device() -> str:
    try:
        from .device import current
        return current()['id']
    except (OSError, ValueError):
        return ''


# ── Validation ────────────────────────────────────────────────────

def front_matter(text: str) -> tuple[dict, str]:
    """The YAML front matter of a ``SKILL.md`` or persona file and the text
    after it. ValueError when the front matter is missing or not a mapping."""
    match = _FRONT.match(text)
    if not match:
        raise ValueError('starts without front matter (a "---" line, name and description, then "---")')
    import yaml
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        mark = getattr(error, 'problem_mark', None)
        where = f' (line {mark.line + 2})' if mark is not None else ''
        raise ValueError(f'has front matter that is not valid YAML{where}') from None
    if not isinstance(meta, dict):
        raise ValueError('has front matter that is not a mapping of fields')
    return meta, text[match.end():]


def _path(value) -> str:
    """A file's place in a skill: relative, with forward slashes, inside it."""
    path = str(value or '').replace('\\', '/')
    parts = PurePosixPath(path).parts
    # A colon would name a drive, or on Windows a file's alternate data stream.
    if (not parts or path.startswith('/') or ':' in path or any(ord(c) < 32 for c in path)
            or any(part in ('.', '..', '') for part in path.split('/'))):
        raise ValueError(f'{value!r} is not a path inside the item')
    if any(part in IGNORED for part in parts):
        raise ValueError(f'{path} is a file Imprint does not keep')
    return path


def _files(kind: str, files) -> dict[str, str]:
    if not isinstance(files, dict) or not files:
        raise ValueError('An item is a mapping of its files to their text')
    kept: dict[str, str] = {}
    total = 0
    for raw, text in files.items():
        path = _path(raw)
        if path in kept:
            raise ValueError(f'{path} is listed twice')
        if not isinstance(text, str):
            raise ValueError(f'{path} is not text: an item holds text files only')
        size = len(text.encode('utf-8'))
        if size > MAX_FILE:
            raise ValueError(f'{path} is {size:,} bytes; a file may have at most {MAX_FILE:,}')
        total += size
        kept[path] = text
    if total > MAX_TOTAL:
        raise ValueError(f'The files hold {total:,} bytes; an item may have at most {MAX_TOTAL:,}')
    if len(kept) > MAX_FILES:
        raise ValueError(f'{len(kept)} files; an item may have at most {MAX_FILES}')
    if kind == 'persona' and list(kept) != [MAIN['persona']]:
        raise ValueError(f'A persona is one file, {MAIN["persona"]}')
    if MAIN[kind] not in kept:
        raise ValueError(f'A {kind} needs a {MAIN[kind]}')
    return dict(sorted(kept.items()))


def _credentials(files: dict[str, str]) -> None:
    """Refuse files that hold what looks like a credential (ADR 0060)."""
    from . import redaction
    paths = list(files)
    for path, redacted in zip(paths, redaction.redact_all([files[p] for p in paths])):
        if redacted != files[path]:
            found = ', '.join(sorted(redaction.kinds(redacted) - redaction.kinds(files[path])))
            raise ValueError(f'{path} holds what looks like a credential ({found or "secret"}). '
                             'Remove it first: skills and personas never carry secrets.')


def validate(kind: str, files) -> dict:
    """``{name, description, files}`` of a valid item, or ValueError naming
    what is wrong."""
    if kind not in KINDS:
        raise ValueError(f'kind must be one of {", ".join(KINDS)}')
    files = _files(kind, files)
    try:
        meta, body = front_matter(files[MAIN[kind]])
    except ValueError as error:
        raise ValueError(f'{MAIN[kind]} {error}') from None
    name, description = meta.get('name'), meta.get('description')
    if not isinstance(name, str) or not NAME.fullmatch(name) or len(name) > MAX_NAME:
        raise ValueError(f'{MAIN[kind]} needs a name of lower-case letters, digits and hyphens, '
                         f'at most {MAX_NAME} characters (it names the folder clients read)')
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f'{MAIN[kind]} needs a description: clients read it to decide when to use the {kind}')
    description = ' '.join(description.split())
    if len(description) > MAX_DESCRIPTION:
        raise ValueError(f'The description has {len(description)} characters; at most {MAX_DESCRIPTION}')
    if not body.strip():
        raise ValueError(f'{MAIN[kind]} has no instructions after its front matter')
    _credentials(files)
    return {'name': name, 'description': description, 'files': files}


def content_hash(kind: str, files: dict[str, str]) -> str:
    """The identity of an item's content: equal for equal files, whatever
    order they were read in."""
    return hashlib.sha256(json.dumps([kind, sorted(files.items())], ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def compose(kind: str, name: str, description: str, instructions: str) -> dict[str, str]:
    """The files of a new item from a form: front matter and instructions."""
    import yaml
    meta = yaml.safe_dump({'name': name, 'description': ' '.join(str(description or '').split())},
                          sort_keys=False, allow_unicode=True, width=10_000)
    return {MAIN[kind]: f'---\n{meta}---\n\n{str(instructions or "").strip()}\n'}


# ── Reads ─────────────────────────────────────────────────────────

def _item(row, *, with_files: bool = True) -> dict:
    item = dict(row)
    files = json.loads(item['files'])
    item['paths'] = list(files)
    item['size'] = sum(len(text.encode('utf-8')) for text in files.values())
    if with_files:
        item['files'] = files
    else:
        del item['files']
    return item


def list_all(kind: str | None = None, *, with_files: bool = False) -> list[dict]:
    """Items by kind and name, the active name holder first (``shadowed``
    marks one that another item of its kind and name outranks)."""
    with _db() as conn:
        rows = conn.execute('SELECT * FROM items' + (' WHERE kind=?' if kind else ''), (kind,) if kind else ())
        items = [_item(row, with_files=with_files) for row in rows]
    holders = {(i['kind'], i['name']): i['id'] for i in _holders(items)}
    for item in items:
        item['shadowed'] = item['state'] != 'retired' and holders.get((item['kind'], item['name'])) != item['id']
    order = {state: index for index, state in enumerate(STATES)}
    return sorted(items, key=lambda i: (i['kind'], order[i['state']], i['name'], i['created_at'], i['id']))


def _holders(items) -> list[dict]:
    """The item that holds each name among those not retired: the oldest,
    then the smallest id, so every device picks the same one when two
    devices created the same name before they synced."""
    best: dict[tuple, dict] = {}
    for item in items:
        if item['state'] == 'retired':
            continue
        key = (item['kind'], item['name'])
        held = best.get(key)
        if held is None or (item['created_at'], item['id']) < (held['created_at'], held['id']):
            best[key] = item
    return list(best.values())


def active(kind: str) -> list[dict]:
    """The items clients receive: active, and holding their name."""
    return [item for item in _holders(list_all(kind, with_files=True)) if item['state'] == 'active']


def get(item_id: str) -> dict | None:
    with _db() as conn:
        row = conn.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
    return _item(row) if row else None


def find(name: str, kind: str | None = None) -> dict:
    """The item of that name that is not retired; KeyError when there is
    none, ValueError when it is ambiguous between the kinds."""
    matches = [i for i in list_all(kind, with_files=True) if i['name'] == name and i['state'] != 'retired']
    if not matches:
        raise KeyError(name)
    kinds = {i['kind'] for i in matches}
    if len(kinds) > 1:
        raise ValueError(f'{name} is both a skill and a persona: say which')
    return next(i for i in matches if not i['shadowed'])


def history(item_id: str) -> list[dict]:
    """The versions this device replaced, newest first."""
    with _db() as conn:
        rows = conn.execute('SELECT * FROM versions WHERE item_id=? ORDER BY replaced_at DESC', (item_id,)).fetchall()
    versions = []
    for row in rows:
        version = dict(row)
        version['files'] = json.loads(version['files'])
        versions.append(version)
    return versions


# ── Writes ────────────────────────────────────────────────────────

def _name_free(conn, kind: str, name: str, item_id: str = '') -> None:
    taken = conn.execute("SELECT id FROM items WHERE kind=? AND name=? AND state!='retired' AND id!=?",
                         (kind, name, item_id)).fetchone()
    if taken:
        raise ValueError(f'A {kind} named {name} already exists')


def _keep(conn, row, replaced_at: float) -> None:
    """Put a replaced version in this device's history, at most HISTORY."""
    conn.execute('INSERT OR REPLACE INTO versions (item_id, hash, files, state, device, updated_at, replaced_at) '
                 'VALUES (?, ?, ?, ?, ?, ?, ?)', (row['id'], row['hash'], row['files'], row['state'], row['device'],
                                                 row['updated_at'], replaced_at))
    conn.execute('DELETE FROM versions WHERE item_id=? AND hash NOT IN (SELECT hash FROM versions WHERE item_id=? '
                 'ORDER BY replaced_at DESC LIMIT ?)', (row['id'], row['id'], HISTORY))


def _stamp(previous: float) -> float:
    """Now, or just after ``previous`` when the clock has not moved past it,
    so paired devices take the change."""
    return max(time.time(), previous + 0.001)


def create(kind: str, files, origin: str = 'user') -> dict:
    """A new item, active at once."""
    if origin not in ORIGINS:
        raise ValueError(f'origin must be one of {", ".join(ORIGINS)}')
    valid = validate(kind, files)
    now = time.time()
    record = {'id': uuid.uuid4().hex, 'kind': kind, 'name': valid['name'], 'description': valid['description'],
              'files': json.dumps(valid['files'], ensure_ascii=False), 'hash': content_hash(kind, valid['files']),
              'state': 'active', 'origin': origin, 'device': _device(), 'created_at': now, 'updated_at': now}
    with _db() as conn:
        _name_free(conn, kind, valid['name'])
        conn.execute(f'INSERT INTO items ({", ".join(_FIELDS)}) VALUES ({", ".join("?" * len(_FIELDS))})',
                     [record[f] for f in _FIELDS])
    return get(record['id'])


def update(item_id: str, *, files=None, state: str | None = None, at: float | None = None) -> dict:
    """Change an item's files or state (``active``, ``disabled``, or
    ``retired``, which removes it from the clients and from every paired
    device). The version it replaces goes to this device's history. ``at``
    dates the change (an edit made in a client's folder); it is never dated
    at or before the item's last change. KeyError for an unknown id."""
    if state is not None and state not in STATES:
        raise ValueError(f'state must be one of {", ".join(STATES)}')
    with _db() as conn:
        row = conn.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
        if row is None:
            raise KeyError(item_id)
        changes: dict = {}
        if files is not None:
            valid = validate(row['kind'], files)
            digest = content_hash(row['kind'], valid['files'])
            if digest != row['hash']:
                changes.update(name=valid['name'], description=valid['description'], hash=digest,
                               files=json.dumps(valid['files'], ensure_ascii=False))
        if state is not None and state != row['state']:
            changes['state'] = state
        if not changes:
            return _item(row)
        name = changes.get('name', row['name'])
        if changes.get('state', row['state']) != 'retired':
            _name_free(conn, row['kind'], name, item_id)
        now = time.time()
        _keep(conn, row, now)
        changes.update(device=_device(), updated_at=max(at if at is not None else now, row['updated_at'] + 0.001))
        conn.execute(f'UPDATE items SET {", ".join(f"{k}=?" for k in changes)} WHERE id=?', [*changes.values(), item_id])
    return get(item_id)


def keep_version(item_id: str, files, at: float) -> None:
    """Keep an edit that lost to a newer change in this device's history,
    so the user can restore it."""
    row = get(item_id)
    if row is None:
        return
    valid = validate(row['kind'], files)
    digest = content_hash(row['kind'], valid['files'])
    if digest == row['hash']:
        return
    with _db() as conn:
        _keep(conn, {'id': item_id, 'hash': digest, 'files': json.dumps(valid['files'], ensure_ascii=False),
                     'state': row['state'], 'device': _device(), 'updated_at': at}, time.time())


def restore(item_id: str, digest: str) -> dict:
    """Make a version from this device's history the item's current one."""
    version = next((v for v in history(item_id) if v['hash'] == digest), None)
    if version is None:
        raise KeyError(digest)
    return update(item_id, files=version['files'], state='active')


# ── Sync (ADR 0168): the newest change of each item wins ─────────

def exists() -> bool:
    """Whether this device ever stored an item: the store is made on the first."""
    return (config.get_data_dir() / 'registry.sqlite3').exists()


def manifest() -> dict[str, float]:
    with _db() as conn:
        return {row['id']: float(row['updated_at']) for row in conn.execute('SELECT id, updated_at FROM items')}


def export(ids) -> list[dict]:
    wanted = set(ids)
    with _db() as conn:
        rows = [row for row in conn.execute('SELECT * FROM items') if row['id'] in wanted]
    return [{**{f: row[f] for f in _FIELDS}, 'files': json.loads(row['files'])} for row in rows]


def import_records(records: list[dict]) -> dict[str, int]:
    """Store items a paired device sent: new ones, and newer versions of
    held ones, whose replaced version goes to this device's history. An
    item that does not validate here, a credential in it included, is
    skipped."""
    stats = {'inserted': 0, 'updated': 0, 'skipped': 0}
    with _db() as conn:
        for record in records:
            try:
                item_id, kind, state = str(record['id']), record['kind'], record['state']
                if not _ID.fullmatch(item_id) or kind not in KINDS or state not in STATES:
                    raise ValueError(item_id)
                valid = validate(kind, record['files'])
                values = {'id': item_id, 'kind': kind, 'name': valid['name'], 'description': valid['description'],
                          'files': json.dumps(valid['files'], ensure_ascii=False),
                          'hash': content_hash(kind, valid['files']), 'state': state,
                          'origin': record.get('origin') if record.get('origin') in ORIGINS else 'user',
                          'device': str(record.get('device') or '')[:128],
                          'created_at': float(record['created_at']), 'updated_at': float(record['updated_at'])}
            except (KeyError, TypeError, ValueError):
                stats['skipped'] += 1
                continue
            held = conn.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
            if held is not None and held['updated_at'] >= values['updated_at']:
                stats['skipped'] += 1
                continue
            if held is not None and held['hash'] != values['hash']:
                _keep(conn, held, time.time())
            conn.execute(f'INSERT OR REPLACE INTO items ({", ".join(_FIELDS)}) VALUES ({", ".join("?" * len(_FIELDS))})',
                         [values[f] for f in _FIELDS])
            stats['updated' if held else 'inserted'] += 1
    return stats
