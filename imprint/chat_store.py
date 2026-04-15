"""SQLite persistence for in-viz chat sessions.

Per-workspace database (`chat.sqlite3` or `chat_{ws}.sqlite3`) sitting in the
imprint data directory. Stores session metadata and full message transcripts so
conversations survive viz restart.
"""

from __future__ import annotations

import atexit
import json
import sqlite3
import time
import uuid
from pathlib import Path

from . import config

_conns: dict[str, sqlite3.Connection] = {}


def _db_path(workspace: str | None = None) -> Path:
    ws = workspace or config.get_active_workspace()
    if ws == "default":
        return config.get_data_dir() / "chat.sqlite3"
    return config.get_data_dir() / f"chat_{ws}.sqlite3"


def _get_conn(workspace: str | None = None) -> sqlite3.Connection:
    ws = workspace or config.get_active_workspace()
    if ws in _conns:
        return _conns[ws]

    config.get_data_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path(ws)), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id         TEXT PRIMARY KEY,
            title      TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_updated
            ON chat_sessions(updated_at DESC);

        CREATE TABLE IF NOT EXISTS chat_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
            seq        INTEGER NOT NULL,
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
            tool_name  TEXT,
            tool_args  TEXT,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session
            ON chat_messages(session_id, seq);
        """
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


# ── Sessions ─────────────────────────────────────────────────

def create_session(workspace: str | None = None, title: str = "New chat") -> str:
    conn = _get_conn(workspace)
    sid = uuid.uuid4().hex
    now = time.time()
    conn.execute(
        "INSERT INTO chat_sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (sid, title, now, now),
    )
    conn.commit()
    return sid


def list_sessions(workspace: str | None = None, limit: int = 100) -> list[dict]:
    conn = _get_conn(workspace)
    rows = conn.execute(
        """
        SELECT s.id, s.title, s.created_at, s.updated_at,
               (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.id) AS message_count
        FROM chat_sessions s
        ORDER BY s.updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_session(session_id: str, workspace: str | None = None) -> dict | None:
    conn = _get_conn(workspace)
    row = conn.execute(
        "SELECT id, title, created_at, updated_at FROM chat_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def rename_session(session_id: str, title: str, workspace: str | None = None) -> bool:
    conn = _get_conn(workspace)
    cur = conn.execute(
        "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?",
        (title, time.time(), session_id),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_session(session_id: str, workspace: str | None = None) -> bool:
    conn = _get_conn(workspace)
    cur = conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
    conn.commit()
    return cur.rowcount > 0


# ── Messages ─────────────────────────────────────────────────

def get_messages(session_id: str, workspace: str | None = None) -> list[dict]:
    conn = _get_conn(workspace)
    rows = conn.execute(
        """
        SELECT seq, role, content, tool_name, tool_args, created_at
        FROM chat_messages
        WHERE session_id = ?
        ORDER BY seq ASC
        """,
        (session_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("tool_args"):
            try:
                d["tool_args"] = json.loads(d["tool_args"])
            except (json.JSONDecodeError, TypeError):
                pass
        out.append(d)
    return out


def append_message(
    session_id: str,
    role: str,
    content: str,
    tool_name: str | None = None,
    tool_args: dict | None = None,
    workspace: str | None = None,
) -> int:
    """Append a message. Auto-bumps seq, touches session updated_at,
    and sets the session title to the first user message if still default."""
    conn = _get_conn(workspace)
    now = time.time()
    seq_row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM chat_messages WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    seq = seq_row["next"] if seq_row else 1

    cur = conn.execute(
        """
        INSERT INTO chat_messages
            (session_id, seq, role, content, tool_name, tool_args, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            seq,
            role,
            content,
            tool_name,
            json.dumps(tool_args) if tool_args is not None else None,
            now,
        ),
    )

    # Touch session; seed title from first user message if still "New chat"
    if role == "user" and seq == 1:
        title = content.strip().splitlines()[0][:60] if content.strip() else "New chat"
        conn.execute(
            "UPDATE chat_sessions SET updated_at = ?, title = CASE WHEN title = 'New chat' THEN ? ELSE title END WHERE id = ?",
            (now, title, session_id),
        )
    else:
        conn.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )

    conn.commit()
    return cur.lastrowid
