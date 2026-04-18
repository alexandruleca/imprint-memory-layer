import atexit
import sqlite3
import time

from . import config

_conns: dict[str, sqlite3.Connection] = {}


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
) -> int:
    """Add a temporal fact. Returns the fact ID."""
    conn = _get_conn(workspace)
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
    workspace: str | None = None,
) -> list[dict]:
    """Query facts. Filters by subject and/or predicate."""
    conn = _get_conn(workspace)
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


def invalidate(fact_id: int, workspace: str | None = None) -> bool:
    """Mark a fact as ended (no longer valid)."""
    conn = _get_conn(workspace)
    cur = conn.execute(
        "UPDATE facts SET ended = ? WHERE id = ? AND ended IS NULL",
        (time.time(), fact_id),
    )
    conn.commit()
    return cur.rowcount > 0


def recent(limit: int = 5, workspace: str | None = None) -> list[dict]:
    """Get the most recent active facts."""
    return query(active_only=True, limit=limit, workspace=workspace)


def count(active_only: bool = True, workspace: str | None = None) -> int:
    """Return the total number of facts (active only by default)."""
    conn = _get_conn(workspace)
    where = " WHERE ended IS NULL" if active_only else ""
    row = conn.execute(f"SELECT COUNT(*) FROM facts{where}").fetchone()
    return row[0]
