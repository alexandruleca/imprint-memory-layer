"""OpenCode sessions as transcripts Imprint can learn (ADR 0124).

OpenCode keeps every session in one SQLite database in its data directory
(``$XDG_DATA_HOME/opencode``, default ``~/.local/share/opencode``, on every
platform): ``opencode.db``, or ``opencode-<channel>.db`` for a build of
another release channel, or the file ``OPENCODE_DB`` names. Learning reads
files, so each top-level session is exported to a JSONL transcript in
Imprint's data directory and learned from there like any other client's.

Two schemas are read, chosen by the tables a database has:

- OpenCode 2 (``session_v2``, ``session_message``, ``session_part``): a
  message row's ``type`` says what it is; an assistant's text, reasoning and
  tool calls are its parts, or its data's ``content`` when it has none.
- OpenCode 1.2 to 1.x (``session``, ``message``, ``part``): a message's data
  has a ``role`` and its parts say what was written.

The export's first record states the session: ``{"type": "opencode.session",
"id", "cwd", "title", "database"}``. Each further record is a message:
``{"type": "opencode.message", "role", "content": [blocks], "timestamp"}``.
Roles are ``user`` and ``assistant`` for the conversation, ``context`` for
what OpenCode wrote into it itself (synthetic and system messages, skills,
compaction summaries) and ``tool`` for a shell command the user ran.

An export is rewritten only when its session changed (its ``time_updated``),
so an unchanged session keeps its file's modification time and learning skips
it. Exports of sessions no longer in their database are removed. Subagent
sessions (those with a parent) are not learned, as no other client's are.

Stdlib only: the OpenCode plugin's learner reads a session through it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

SESSION = 'opencode.session'
MESSAGE = 'opencode.message'
# OpenCode session ids: "ses_" and an ascending identifier.
SESSION_ID = re.compile(r'^ses_[A-Za-z0-9]+$')
_MANIFEST = 'manifest.json'


def data_dir() -> Path:
    """OpenCode's data directory, as OpenCode resolves it."""
    base = os.environ.get('XDG_DATA_HOME') or str(Path.home() / '.local' / 'share')
    return Path(base) / 'opencode'


def databases(store: str | os.PathLike | None = None) -> list[Path]:
    """The session databases in an OpenCode data directory: every release
    channel's, and the one ``OPENCODE_DB`` names."""
    root = Path(store) if store else data_dir()
    found = {path for path in root.glob('opencode*.db') if path.is_file()}
    named = os.environ.get('OPENCODE_DB')
    if named and named != ':memory:':
        path = Path(named) if Path(named).is_absolute() else root / named
        if path.is_file():
            found.add(path)
    return sorted(found)


def export_root() -> Path:
    from .config import get_data_dir
    return get_data_dir() / 'opencode_sessions'


def _export_dir(database: Path) -> Path:
    key = hashlib.sha256(str(database.resolve()).encode('utf-8')).hexdigest()[:16]
    return export_root() / key


def _connect(database: Path) -> sqlite3.Connection:
    # Read-only: OpenCode owns the database and may be writing to it.
    conn = sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro', uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _json(value):
    if isinstance(value, (bytes, str)):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _sessions(conn, tables) -> list[dict]:
    table = 'session_v2' if 'session_v2' in tables else 'session' if 'session' in tables else None
    if table is None:
        return []
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
    updated = 'time_updated' if 'time_updated' in columns else '0'
    title = 'title' if 'title' in columns else "''"
    rows = conn.execute(f'SELECT id, parent_id, directory, {title} AS title, {updated} AS updated FROM {table}')
    return [dict(row) for row in rows if not row['parent_id']]


def _block_text(text):
    return {'type': 'text', 'text': text} if isinstance(text, str) and text else None


def _tool_blocks(call_id, name, state):
    """A tool call and, once it has one, its result."""
    state = state if isinstance(state, dict) else {}
    blocks = [{'type': 'tool_use', 'id': str(call_id or ''), 'name': str(name or 'tool'), 'input': state.get('input', '')}]
    result = state.get('content', state.get('output'))
    if result in (None, '', []) and state.get('error') is not None:
        result = state['error']
    if result not in (None, '', []):
        blocks.append({'type': 'tool_result', 'tool_use_id': str(call_id or ''), 'content': result})
    return blocks


def _assistant_v2(content) -> list[dict]:
    blocks = []
    for item in content or []:
        if not isinstance(item, dict):
            continue
        kind = item.get('type')
        if kind == 'text' and _block_text(item.get('text')):
            blocks.append(_block_text(item['text']))
        elif kind == 'reasoning':
            blocks.append({'type': 'reasoning', 'text': item.get('text') or ''})
        elif kind == 'tool':
            blocks.extend(_tool_blocks(item.get('id'), item.get('name'), item.get('state')))
    return blocks


def _messages_v2(conn, session_id) -> list[dict]:
    parts: dict[str, list] = {}
    for row in conn.execute('SELECT message_id, data FROM session_part WHERE session_id=? ORDER BY message_id, ordinal',
                            (session_id,)):
        parts.setdefault(row['message_id'], []).append(_json(row['data']))
    records = []
    for row in conn.execute('SELECT id, type, time_created, data FROM session_message WHERE session_id=? ORDER BY seq',
                            (session_id,)):
        data = _json(row['data'])
        if not isinstance(data, dict):
            continue
        kind, role, blocks = row['type'], None, []
        if kind == 'user':
            role, blocks = 'user', [_block_text(data.get('text'))]
        elif kind == 'assistant':
            role, blocks = 'assistant', _assistant_v2(parts.get(row['id']) or data.get('content'))
        elif kind in ('synthetic', 'system', 'skill'):
            role, blocks = 'context', [_block_text(data.get('text'))]
        elif kind == 'compaction' and data.get('status') == 'completed':
            role, blocks = 'context', [_block_text(data.get('summary'))]
        elif kind == 'shell':
            output = data.get('output')
            if isinstance(output, dict):
                output = output.get('text', output.get('output', json.dumps(output, ensure_ascii=False)))
            text = f"$ {data.get('command', '')}" + (f'\n{output}' if isinstance(output, str) and output else '')
            role, blocks = 'tool', [_block_text(text)]
        blocks = [block for block in blocks if block]
        if role and blocks:
            records.append({'type': MESSAGE, 'role': role, 'content': blocks, 'timestamp': row['time_created']})
    return records


def _messages_v1(conn, session_id) -> list[dict]:
    parts: dict[str, list] = {}
    for row in conn.execute('SELECT message_id, data FROM part WHERE session_id=? ORDER BY message_id, id', (session_id,)):
        parts.setdefault(row['message_id'], []).append(_json(row['data']))
    records = []
    for row in conn.execute('SELECT id, time_created, data FROM message WHERE session_id=? ORDER BY time_created, id',
                            (session_id,)):
        data = _json(row['data'])
        role = data.get('role') if isinstance(data, dict) else None
        if role not in ('user', 'assistant'):
            continue
        typed, injected = [], []
        for part in parts.get(row['id'], []):
            if not isinstance(part, dict) or part.get('ignored'):
                continue
            kind = part.get('type')
            if kind == 'text' and _block_text(part.get('text')):
                (injected if part.get('synthetic') else typed).append(_block_text(part['text']))
            elif kind == 'reasoning' and role == 'assistant':
                typed.append({'type': 'reasoning', 'text': part.get('text') or ''})
            elif kind == 'tool' and role == 'assistant':
                typed.extend(_tool_blocks(part.get('callID'), part.get('tool'), part.get('state')))
        timestamp = row['time_created']
        if injected:
            records.append({'type': MESSAGE, 'role': 'context', 'content': injected, 'timestamp': timestamp})
        if typed:
            records.append({'type': MESSAGE, 'role': role, 'content': typed, 'timestamp': timestamp})
    return records


def _messages(conn, tables, session_id) -> list[dict]:
    if 'session_message' in tables:
        records = _messages_v2(conn, session_id)
        if records or 'message' not in tables:
            return records
    if 'message' in tables and 'part' in tables:
        return _messages_v1(conn, session_id)
    return []


def _write(path: Path, records: list[dict]) -> None:
    content = ''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in records)
    try:
        if path.read_text(encoding='utf-8') == content:
            return
    except OSError:
        pass
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as file:
        file.write(content)
    os.replace(temp, path)


def _load_manifest(directory: Path) -> dict:
    try:
        manifest = json.loads((directory / _MANIFEST).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def _export_database(database: Path, only: str | None = None) -> list[Path]:
    directory = _export_dir(database)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = _load_manifest(directory)
    exported = []
    with closing(_connect(database)) as conn:
        tables = _tables(conn)
        sessions = _sessions(conn, tables)
        for session in sessions:
            session_id = session['id']
            if not isinstance(session_id, str) or not SESSION_ID.match(session_id):
                continue
            if only and session_id != only:
                continue
            path = directory / f'{session_id}.jsonl'
            if path.is_file() and manifest.get(session_id) == session['updated'] and not only:
                exported.append(path)
                continue
            header = {'type': SESSION, 'id': session_id, 'cwd': session['directory'] or '',
                      'title': session['title'] or '', 'database': str(database)}
            _write(path, [header, *_messages(conn, tables, session_id)])
            manifest[session_id] = session['updated']
            exported.append(path)
    if not only:
        live = {path.name for path in exported}
        for stale in directory.glob('*.jsonl'):
            if stale.name not in live:
                stale.unlink(missing_ok=True)
                manifest.pop(stale.stem, None)
    _write_manifest(directory, manifest)
    return exported


def _write_manifest(directory: Path, manifest: dict) -> None:
    temp = directory / f'{_MANIFEST}.tmp'
    temp.write_text(json.dumps(manifest, sort_keys=True), encoding='utf-8')
    os.replace(temp, directory / _MANIFEST)


def export(store: str | os.PathLike | None = None) -> list[Path]:
    """Export every top-level session in an OpenCode data directory; the
    paths of their transcripts. A database that cannot be read raises."""
    paths = []
    for database in databases(store):
        paths.extend(_export_database(database))
    return paths


def export_session(session_id: str, store: str | os.PathLike | None = None) -> Path | None:
    """Export one session, wherever its database is; its transcript's path,
    or None when no database in the data directory has it."""
    if not SESSION_ID.match(session_id or ''):
        return None
    for database in databases(store):
        try:
            found = _export_database(database, only=session_id)
        except sqlite3.Error:
            continue
        if found:
            return found[0]
    return None


def is_export(record: dict) -> bool:
    return record.get('type') == SESSION
