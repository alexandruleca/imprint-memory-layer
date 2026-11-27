"""The Dispatcher's record of its runs, in ``data/dispatch.sqlite3``.

Device-local and never synced: each device's Dispatcher keeps what it
started. Each run's output goes to ``data/dispatch/<run id>.log``.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .. import config

COLUMNS = ('id', 'workspace', 'task', 'title', 'project', 'client', 'role', 'session', 'conversation', 'claim',
           'pid', 'worktree', 'branch', 'base', 'started_at', 'ended_at', 'exit_code', 'outcome', 'detail')


def _db_path() -> Path:
    return config.get_data_dir() / 'dispatch.sqlite3'


def log_path(run_id: str) -> Path:
    return config.get_data_dir() / 'dispatch' / f'{run_id}.log'


def _connect() -> sqlite3.Connection:
    config.get_data_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path()), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute(
        'CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, workspace TEXT, task TEXT, title TEXT, project TEXT,'
        ' client TEXT, role TEXT, session TEXT, conversation TEXT, claim TEXT, pid INTEGER, worktree TEXT,'
        ' branch TEXT, base TEXT, started_at REAL NOT NULL, ended_at REAL, exit_code INTEGER, outcome TEXT,'
        ' detail TEXT)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC)')
    return conn


def add(run: dict) -> dict:
    run = {column: run.get(column) for column in COLUMNS}
    run['started_at'] = run['started_at'] or time.time()
    with closing(_connect()) as conn, conn:
        conn.execute(f'INSERT INTO runs ({", ".join(COLUMNS)}) VALUES ({", ".join("?" * len(COLUMNS))})',
                     [run[column] for column in COLUMNS])
    return run


def update(run_id: str, **fields) -> None:
    fields = {k: v for k, v in fields.items() if k in COLUMNS and k != 'id'}
    if not fields:
        return
    with closing(_connect()) as conn, conn:
        conn.execute(f'UPDATE runs SET {", ".join(f"{k} = ?" for k in fields)} WHERE id = ?',
                     [*fields.values(), run_id])


def get(run_id: str) -> dict | None:
    with closing(_connect()) as conn:
        row = conn.execute('SELECT * FROM runs WHERE id = ?', (run_id,)).fetchone()
    return dict(row) if row else None


def recent(limit: int = 20) -> list[dict]:
    with closing(_connect()) as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM runs ORDER BY started_at DESC LIMIT ?', (limit,))]
