"""Dynamic taxonomy registry backed by SQLite.

Tracks observed type/domain/topic values and their frequency across the KB.
The LLM tagger uses the registry to prefer existing labels while allowing
new ones to emerge organically.

Storage: piggybacks on the imprint_graph SQLite DB (per-workspace isolation).
"""

from __future__ import annotations

import atexit
import sqlite3
import threading
import time
from typing import Any

from . import config

# ── Connection pool (mirrors imprint_graph.py pattern) ─────────

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
        CREATE TABLE IF NOT EXISTS taxonomy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            axis TEXT NOT NULL,
            value TEXT NOT NULL,
            frequency INTEGER DEFAULT 1,
            first_seen REAL NOT NULL,
            last_seen REAL NOT NULL,
            merged_into TEXT DEFAULT NULL,
            UNIQUE(axis, value)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_taxonomy_axis ON taxonomy(axis)"
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


atexit.register(_close_all)


# ── Seed values (current hardcoded taxonomy) ───────────────────

_SEED_TYPES = [
    "decision", "pattern", "bug", "preference",
    "milestone", "architecture", "finding",
]

_SEED_DOMAINS = [
    "auth", "db", "api", "math", "rendering", "ui", "testing",
    "infra", "ml", "perf", "security", "build", "payments",
]

_seeded: set[str] = set()


def seed_defaults(workspace: str | None = None) -> None:
    """Insert hardcoded types + domains with frequency=0.  Idempotent."""
    ws = workspace or config.get_active_workspace()
    if ws in _seeded:
        return
    conn = _get_conn(workspace)
    now = time.time()
    for val in _SEED_TYPES:
        conn.execute(
            "INSERT OR IGNORE INTO taxonomy (axis, value, frequency, first_seen, last_seen) "
            "VALUES (?, ?, 0, ?, ?)",
            ("type", val, now, now),
        )
    for val in _SEED_DOMAINS:
        conn.execute(
            "INSERT OR IGNORE INTO taxonomy (axis, value, frequency, first_seen, last_seen) "
            "VALUES (?, ?, 0, ?, ?)",
            ("domain", val, now, now),
        )
    conn.commit()
    _seeded.add(ws)


# ── In-memory cache (60s TTL) ──────────────────────────────────

_cache: dict[str, tuple[float, list[str]]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 60.0


def _cache_key(axis: str, workspace: str | None) -> str:
    ws = workspace or config.get_active_workspace()
    return f"{ws}:{axis}"


def _invalidate_cache(axis: str, workspace: str | None = None) -> None:
    key = _cache_key(axis, workspace)
    with _cache_lock:
        _cache.pop(key, None)


# ── Public API ─────────────────────────────────────────────────

def get_all_values(axis: str, workspace: str | None = None) -> list[str]:
    """Return active (non-merged) values for an axis, sorted by frequency desc.

    Cached for 60s.  Used to populate LLM prompts.
    """
    key = _cache_key(axis, workspace)
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry[0]) < _CACHE_TTL:
            return entry[1]

    seed_defaults(workspace)
    conn = _get_conn(workspace)
    rows = conn.execute(
        "SELECT value FROM taxonomy "
        "WHERE axis = ? AND merged_into IS NULL "
        "ORDER BY frequency DESC",
        (axis,),
    ).fetchall()
    values = [r["value"] for r in rows]

    with _cache_lock:
        _cache[key] = (time.time(), values)
    return values


def get_taxonomy(axis: str, workspace: str | None = None) -> list[dict]:
    """Return full taxonomy entries for an axis (excluding merged).

    Returns [{value, frequency, first_seen, last_seen}] sorted by freq desc.
    """
    seed_defaults(workspace)
    conn = _get_conn(workspace)
    rows = conn.execute(
        "SELECT value, frequency, first_seen, last_seen FROM taxonomy "
        "WHERE axis = ? AND merged_into IS NULL "
        "ORDER BY frequency DESC",
        (axis,),
    ).fetchall()
    return [dict(r) for r in rows]


def record_usage(
    axis: str,
    values: list[str],
    workspace: str | None = None,
) -> None:
    """Increment frequency for existing values, INSERT new ones.

    Resolves merges: if a value is merged, increments the canonical target.
    """
    if not values:
        return
    conn = _get_conn(workspace)
    now = time.time()
    for val in values:
        val = val.strip().lower()
        if not val:
            continue
        # Check if merged → redirect to canonical
        row = conn.execute(
            "SELECT merged_into FROM taxonomy WHERE axis = ? AND value = ?",
            (axis, val),
        ).fetchone()
        target = (row["merged_into"] if row and row["merged_into"] else val)

        conn.execute(
            "INSERT INTO taxonomy (axis, value, frequency, first_seen, last_seen) "
            "VALUES (?, ?, 1, ?, ?) "
            "ON CONFLICT(axis, value) DO UPDATE SET "
            "frequency = frequency + 1, last_seen = ?",
            (axis, target, now, now, now),
        )
    conn.commit()
    _invalidate_cache(axis, workspace)


def merge(
    axis: str,
    old_value: str,
    into_value: str,
    workspace: str | None = None,
) -> None:
    """Mark old_value as merged into into_value.

    Future record_usage calls for old_value will redirect to into_value.
    """
    conn = _get_conn(workspace)
    now = time.time()
    # Ensure target exists
    conn.execute(
        "INSERT INTO taxonomy (axis, value, frequency, first_seen, last_seen) "
        "VALUES (?, ?, 0, ?, ?) "
        "ON CONFLICT(axis, value) DO NOTHING",
        (axis, into_value, now, now),
    )
    # Mark old as merged
    conn.execute(
        "UPDATE taxonomy SET merged_into = ? WHERE axis = ? AND value = ?",
        (into_value, axis, old_value),
    )
    # Transfer frequency
    old_row = conn.execute(
        "SELECT frequency FROM taxonomy WHERE axis = ? AND value = ?",
        (axis, old_value),
    ).fetchone()
    if old_row:
        conn.execute(
            "UPDATE taxonomy SET frequency = frequency + ? WHERE axis = ? AND value = ?",
            (old_row["frequency"], axis, into_value),
        )
    conn.commit()
    _invalidate_cache(axis, workspace)


def delete_entry(axis: str, value: str, workspace: str | None = None) -> bool:
    """Remove a taxonomy entry.  Returns True if it existed."""
    conn = _get_conn(workspace)
    cur = conn.execute(
        "DELETE FROM taxonomy WHERE axis = ? AND value = ?",
        (axis, value),
    )
    conn.commit()
    _invalidate_cache(axis, workspace)
    return cur.rowcount > 0
