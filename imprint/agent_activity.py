"""Which agents use this Imprint's MCP server, and what they asked of it.

Every client starts its own MCP server process (``python -m imprint``), so a
process is one agent session. It records itself here: the client's name and
version from the MCP handshake, the directory and workspace it serves, and
each tool call with its timing, outcome and a redacted, shortened copy of its
arguments. The dashboard's Agents page reads it (ADR 0117).

The database is per installation (``data/agent_activity.sqlite3``), not per
workspace: one session can switch workspaces. Recording never fails a tool
call; a write that fails is skipped.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

from . import config
from .processes import is_alive
from .redaction import redact

RETENTION_DAYS = 30
VALUE_CHARS = 160      # each string argument
ARGUMENTS_CHARS = 600  # the whole argument summary
ERROR_CHARS = 300

_conn: sqlite3.Connection | None = None
_session_id: str | None = None


def _db_path() -> Path:
    return config.get_data_dir() / "agent_activity.sqlite3"


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    config.get_data_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path()), check_same_thread=False, timeout=2)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_sessions (
            id             TEXT PRIMARY KEY,
            pid            INTEGER NOT NULL,
            client         TEXT,
            client_version TEXT,
            directory      TEXT,
            workspace      TEXT,
            started_at     REAL NOT NULL,
            last_seen      REAL NOT NULL,
            ended_at       REAL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_sessions_seen
            ON agent_sessions(last_seen DESC);

        CREATE TABLE IF NOT EXISTS agent_calls (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
            tool        TEXT NOT NULL,
            started_at  REAL NOT NULL,
            duration_ms REAL NOT NULL,
            ok          INTEGER NOT NULL,
            error       TEXT,
            arguments   TEXT,
            result_chars INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_agent_calls_session
            ON agent_calls(session_id, id DESC);
        """
    )
    conn.commit()
    _conn = conn
    return conn


def close() -> None:
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except sqlite3.Error:
            pass
        _conn = None


def _write(sql: str, params: tuple) -> None:
    try:
        conn = _connect()
        conn.execute(sql, params)
        conn.commit()
    except (sqlite3.Error, OSError):
        pass


# ── Recording, in the MCP server process ──────────────────────────────────

def start_session(directory: str | None, workspace: str | None) -> str:
    """Record this process as a session; its client is named at initialize."""
    global _session_id
    _session_id = uuid.uuid4().hex
    now = time.time()
    _write("DELETE FROM agent_sessions WHERE last_seen < ?", (now - RETENTION_DAYS * 86400,))
    _write(
        "INSERT INTO agent_sessions (id, pid, directory, workspace, started_at, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
        (_session_id, os.getpid(), directory, workspace, now, now),
    )
    return _session_id


def name_client(name: str | None, version: str | None) -> None:
    if _session_id:
        _write("UPDATE agent_sessions SET client = ?, client_version = ?, last_seen = ? WHERE id = ?",
               ((name or "")[:120] or None, (version or "")[:60] or None, time.time(), _session_id))


def summarize_arguments(arguments: dict | None) -> str:
    """The arguments as compact JSON, each string cut short, then redacted."""
    def short(value):
        if isinstance(value, str):
            return value if len(value) <= VALUE_CHARS else value[:VALUE_CHARS] + "…"
        if isinstance(value, dict):
            return {k: short(v) for k, v in value.items()}
        if isinstance(value, list):
            return [short(v) for v in value[:20]]
        return value
    text = json.dumps(short(arguments or {}), ensure_ascii=False, default=str)
    text = redact(text)
    return text if len(text) <= ARGUMENTS_CHARS else text[:ARGUMENTS_CHARS] + "…"


def record_call(tool: str, started_at: float, duration_ms: float, *, ok: bool,
                error: str | None = None, arguments: dict | None = None,
                result_chars: int | None = None, workspace: str | None = None) -> None:
    if not _session_id:
        return
    try:
        summary = summarize_arguments(arguments)
        error = redact(error)[:ERROR_CHARS] if error else None
    except Exception:  # Redaction must not cost the agent its result.
        summary, error = None, "error" if error else None
    _write(
        "INSERT INTO agent_calls (session_id, tool, started_at, duration_ms, ok, error, arguments, result_chars)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (_session_id, tool, started_at, round(duration_ms, 1), int(ok), error, summary, result_chars),
    )
    _write("UPDATE agent_sessions SET last_seen = ?, workspace = COALESCE(?, workspace) WHERE id = ?",
           (time.time(), workspace, _session_id))


def end_session() -> None:
    if _session_id:
        _write("UPDATE agent_sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
               (time.time(), _session_id))


# ── Reading, for the dashboard ────────────────────────────────────────────

def _session(row: sqlite3.Row) -> dict:
    session = dict(row)
    # A process killed without exiting cleanly never wrote ended_at.
    session["connected"] = session["ended_at"] is None and is_alive(session["pid"])
    if session["ended_at"] is None and not session["connected"]:
        session["ended_at"] = session["last_seen"]
    return session


_SESSION_COLUMNS = """
    s.*,
    (SELECT COUNT(*) FROM agent_calls c WHERE c.session_id = s.id) AS calls,
    (SELECT COUNT(*) FROM agent_calls c WHERE c.session_id = s.id AND c.ok = 0) AS errors,
    (SELECT tool FROM agent_calls c WHERE c.session_id = s.id ORDER BY c.id DESC LIMIT 1) AS last_tool
"""


def list_sessions(limit: int = 100) -> list[dict]:
    """Connected sessions first, then the most recently active."""
    if not _db_path().exists():
        return []
    rows = _connect().execute(
        f"SELECT {_SESSION_COLUMNS} FROM agent_sessions s ORDER BY s.last_seen DESC LIMIT ?",
        (max(1, min(limit, 500)),),
    ).fetchall()
    sessions = [_session(row) for row in rows]
    return sorted(sessions, key=lambda s: (not s["connected"], -s["last_seen"]))


def get_session(session_id: str) -> dict | None:
    if not _db_path().exists():
        return None
    row = _connect().execute(f"SELECT {_SESSION_COLUMNS} FROM agent_sessions s WHERE s.id = ?",
                             (session_id,)).fetchone()
    return _session(row) if row else None


def session_calls(session_id: str, limit: int = 200, before: int | None = None) -> list[dict]:
    """A session's calls, newest first; ``before`` pages back by call id."""
    if not _db_path().exists():
        return []
    sql = "SELECT * FROM agent_calls WHERE session_id = ?"
    params: list = [session_id]
    if before is not None:
        sql += " AND id < ?"
        params.append(before)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(limit, 1000)))
    return [dict(row) for row in _connect().execute(sql, params).fetchall()]


def clients(sessions: list[dict]) -> list[dict]:
    """Sessions grouped by client: how many are connected, calls, last seen."""
    grouped: dict[str, dict] = {}
    for s in sessions:
        name = s["client"] or "Unknown client"
        entry = grouped.setdefault(name, {"client": name, "sessions": 0, "connected": 0,
                                          "calls": 0, "errors": 0, "last_seen": 0.0})
        entry["sessions"] += 1
        entry["connected"] += int(s["connected"])
        entry["calls"] += s["calls"]
        entry["errors"] += s["errors"]
        entry["last_seen"] = max(entry["last_seen"], s["last_seen"])
    return sorted(grouped.values(), key=lambda c: (-c["connected"], -c["last_seen"]))
