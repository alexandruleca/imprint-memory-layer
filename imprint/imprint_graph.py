import atexit
import contextlib
import json
import sqlite3
import threading
import time

from . import config

_conns: dict[str, sqlite3.Connection] = {}
# Each workspace shares one connection across threads (MCP tool calls,
# recall's concurrent steps). A sqlite3 connection must not run statements
# from two threads at once: they share prepared statements, and concurrent
# queries came back empty or raised "bad parameter or other API misuse".
_lock = threading.RLock()


@contextlib.contextmanager
def _connection(workspace: str | None = None):
    """The workspace's connection, held by one thread at a time."""
    with _lock:
        yield _get_conn(workspace)


def _get_conn(workspace: str | None = None) -> sqlite3.Connection:
    ws = workspace or config.get_active_workspace()
    if ws in _conns:
        return _conns[ws]

    data_dir = config.get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = config.graph_db_path(ws)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute('BEGIN IMMEDIATE')
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            valid_from REAL NOT NULL,
            ended REAL,
            source TEXT DEFAULT ''
        )
    """
    )
    if 'recorded_device' not in {row[1] for row in conn.execute('PRAGMA table_info(facts)')}:
        conn.execute("ALTER TABLE facts ADD COLUMN recorded_device TEXT NOT NULL DEFAULT '{}'")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_facts_predicate ON facts(predicate)"
    )
    conn.commit()
    _conns[ws] = conn
    return conn


def _close_all() -> None:
    with _lock:
        for conn in _conns.values():
            try:
                conn.close()
            except Exception:
                pass
        _conns.clear()


atexit.register(_close_all)


def add(
    subject: str,
    predicate: str,
    object: str,
    source: str = "",
    workspace: str | None = None,
    recorded_device: dict | None = None,
) -> int:
    """Add a temporal fact, its secrets redacted (ADR 0060). Returns the fact ID."""
    from .workspace_policy import workspace_name, write_guard
    from .redaction import redact_all
    workspace = workspace_name(workspace)
    subject, predicate, object = redact_all([subject, predicate, object])
    from .device import current, _identity
    origin = current() if recorded_device is None else _identity(recorded_device)
    with write_guard([{'source': source}], workspace), _connection(workspace) as conn:
        cur = conn.execute(
            "INSERT INTO facts (subject, predicate, object, valid_from, source, recorded_device) VALUES (?, ?, ?, ?, ?, ?)",
            (subject, predicate, object, time.time(), source, json.dumps(origin)),
        )
        conn.commit()
    return cur.lastrowid


def query(
    subject: str = "",
    predicate: str = "",
    active_only: bool = True,
    limit: int = 20,
    workspace: str | None = None,
) -> list[dict]:
    """Query facts. Filters by subject and/or predicate."""
    conditions = []
    params = []

    if subject:
        conditions.append("subject LIKE ?")
        params.append(f"%{subject}%")
    if predicate:
        conditions.append("predicate LIKE ?")
        params.append(f"%{predicate}%")
    if active_only:
        conditions.append("ended IS NULL")

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with _connection(workspace) as conn:
        rows = conn.execute(
            f"SELECT * FROM facts{where} ORDER BY valid_from DESC LIMIT ?",
            params + [limit],
        ).fetchall()

    return [
        {
            "id": r["id"],
            "subject": r["subject"],
            "predicate": r["predicate"],
            "object": r["object"],
            "valid_from": r["valid_from"],
            "ended": r["ended"],
            "source": r["source"],
            "recorded_device": json.loads(r['recorded_device'] or '{}'),
        }
        for r in rows
    ]


def invalidate(fact_id: int, workspace: str | None = None) -> bool:
    """Mark a fact as ended (no longer valid)."""
    with _connection(workspace) as conn:
        cur = conn.execute(
            "UPDATE facts SET ended = ? WHERE id = ? AND ended IS NULL",
            (time.time(), fact_id),
        )
        conn.commit()
    return cur.rowcount > 0


def _supersedes(newer: tuple, older: tuple) -> bool:
    """Whether fact text ``newer`` is ``older`` redacted further (ADR 0060)."""
    from .redaction import supersedes
    return (all(new == old or supersedes(new, old) for new, old in zip(newer, older))
            and any(supersedes(new, old) for new, old in zip(newer, older)))


def import_fact(record: dict, workspace: str | None = None) -> bool:
    """Preserve origin/timestamps, enforce exclusions and redact secrets
    (whatever the sender did, ADR 0060) on imported facts.

    A fact's versions share its valid_from and differ only in redaction. The most redacted one is kept: a received fact this
    workspace holds more redacted is skipped, and one that redacts a held
    version further replaces it."""
    from .workspace_policy import workspace_name, write_guard
    from .device import _identity
    from .redaction import redact_all
    ws = workspace_name(workspace)
    text = tuple(redact_all([record.get('subject', ''), record.get('predicate', ''), record.get('object', '')]))
    when, source = record.get('valid_from', 0), record.get('source', '') or ''
    with write_guard([{'source': source}], ws), _connection(ws) as conn, conn:
        versions = {row['id']: (row['subject'], row['predicate'], row['object']) for row in conn.execute(
            'SELECT id, subject, predicate, object FROM facts WHERE valid_from=?', (when,))}
        if any(held == text or _supersedes(held, text) for held in versions.values()):
            return False
        conn.executemany('DELETE FROM facts WHERE id=?', [(fact_id,) for fact_id, held in versions.items()
                                                          if _supersedes(text, held)])
        conn.execute('INSERT INTO facts (subject, predicate, object, valid_from, ended, source, recorded_device) VALUES (?, ?, ?, ?, ?, ?, ?)',
                     (*text, when, record.get('ended'), source, json.dumps(_identity(record.get('recorded_device')))))
    return True


def recent(limit: int = 5, workspace: str | None = None) -> list[dict]:
    """Get the most recent active facts."""
    return query(active_only=True, limit=limit, workspace=workspace)


def count(active_only: bool = True, workspace: str | None = None) -> int:
    """Return the total number of facts (active only by default)."""
    where = " WHERE ended IS NULL" if active_only else ""
    with _connection(workspace) as conn:
        row = conn.execute(f"SELECT COUNT(*) FROM facts{where}").fetchone()
    return row[0]
