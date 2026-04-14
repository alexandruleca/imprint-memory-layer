import sqlite3
import time

from . import config

_conn = None
_conn_workspace: str | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn, _conn_workspace
    ws = config.get_active_workspace()
    if _conn is not None and _conn_workspace == ws:
        return _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None

    data_dir = config.get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = config.graph_db_path(ws)

    _conn = sqlite3.connect(str(db_path))
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute(
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
    _conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject)"
    )
    _conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_facts_predicate ON facts(predicate)"
    )
    _conn.commit()
    _conn_workspace = ws
    return _conn


def add(
    subject: str,
    predicate: str,
    object: str,
    source: str = "",
) -> int:
    """Add a temporal fact. Returns the fact ID."""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO facts (subject, predicate, object, valid_from, source) VALUES (?, ?, ?, ?, ?)",
        (subject, predicate, object, time.time(), source),
    )
    conn.commit()
    return cur.lastrowid


def query(
    subject: str = "",
    predicate: str = "",
    active_only: bool = True,
    limit: int = 20,
) -> list[dict]:
    """Query facts. Filters by subject and/or predicate."""
    conn = _get_conn()
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
        }
        for r in rows
    ]


def invalidate(fact_id: int) -> bool:
    """Mark a fact as ended (no longer valid)."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE facts SET ended = ? WHERE id = ? AND ended IS NULL",
        (time.time(), fact_id),
    )
    conn.commit()
    return cur.rowcount > 0


def recent(limit: int = 5) -> list[dict]:
    """Get the most recent active facts."""
    return query(active_only=True, limit=limit)
