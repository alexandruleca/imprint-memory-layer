"""Private SQLite stores in the data directory (sync and browser pairings)."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def connect(path: Path, timeout: float) -> sqlite3.Connection:
    """Open the SQLite store at ``path``, creating it readable only by this user.

    The file is created in binary mode. ``os.open`` defaults to text mode on
    Windows, where the C runtime drops a trailing Ctrl-Z (0x1A) byte from a file
    it opens for reading and writing. That cut the last byte of a pairing store
    whose final index entry pointed at row 26, and every read then failed with
    "database disk image is malformed".

    A store damaged that way lost the end of an index entry, which REINDEX
    rebuilds from its table, so a store that fails its integrity check is
    reindexed once. One that still fails is reported with its path.
    """
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_BINARY', 0), 0o600)
    os.close(descriptor)
    if os.name != 'nt':
        os.chmod(path, 0o600)
    conn = sqlite3.connect(path, timeout=timeout)
    try:
        if _problem(conn):
            try:
                conn.execute('REINDEX')
            except sqlite3.DatabaseError:
                pass  # reported below
            problem = _problem(conn)
            if problem:
                raise sqlite3.DatabaseError(f'{path} is damaged ({problem}). Move it aside and pair again.')
    except BaseException:
        conn.close()
        raise
    return conn


def _problem(conn: sqlite3.Connection) -> str | None:
    try:
        result = conn.execute('PRAGMA integrity_check').fetchone()[0]
    except sqlite3.DatabaseError as error:
        return str(error)
    return None if result == 'ok' else result
