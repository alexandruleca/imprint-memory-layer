"""The Dispatcher's switches and record of its runs, in ``data/dispatch.sqlite3``.

Device-local and never synced (ADR 0220): each device switches its own
Dispatcher's roles on per workspace (ADR 0237) and keeps what it started.
Each run's output goes to ``data/dispatch/<run id>.log``.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .. import config

# What a device's Dispatcher may do in a workspace, each switched on by the person (ADR 0237).
ROLES = ('work', 'review', 'refine', 'deploy')
# The old single switch meant both of these (ADR 0220).
SWITCH_ROLES = ('work', 'review')

COLUMNS = ('id', 'workspace', 'task', 'title', 'project', 'client', 'role', 'session', 'conversation', 'claim',
           'pid', 'worktree', 'branch', 'base', 'started_at', 'ended_at', 'exit_code', 'outcome', 'detail',
           'watcher', 'stop_reason', 'distro', 'cleaned', 'pid_started', 'watcher_started')
# Columns later Tasks of the Dispatcher added to a store that existed before them. ``pid_started``
# and ``watcher_started`` identify the processes behind ``pid`` and ``watcher`` (processes.started),
# so a pid the system gave to another process later is never taken for them.
_ADDED = {'watcher': 'INTEGER', 'stop_reason': 'TEXT', 'distro': 'TEXT', 'cleaned': 'REAL', 'pid_started': 'TEXT',
          'watcher_started': 'TEXT'}


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
    have = {row[1] for row in conn.execute('PRAGMA table_info(runs)')}
    for column, kind in _ADDED.items():
        if column not in have:
            conn.execute(f'ALTER TABLE runs ADD COLUMN {column} {kind}')
    conn.execute('CREATE TABLE IF NOT EXISTS switches (workspace TEXT PRIMARY KEY, enabled INTEGER NOT NULL,'
                 ' changed_at REAL NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS refine_switches (workspace TEXT PRIMARY KEY, enabled INTEGER NOT NULL,'
                 ' changed_at REAL NOT NULL)')
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'roles'").fetchone():
        with conn:
            conn.execute('CREATE TABLE roles (workspace TEXT NOT NULL, role TEXT NOT NULL, enabled INTEGER NOT NULL,'
                         ' changed_at REAL NOT NULL, PRIMARY KEY (workspace, role))')
            # The switches of earlier releases, once: on is Work and Review, refinement is Refine (ADR 0237).
            for role in SWITCH_ROLES:
                conn.execute('INSERT INTO roles SELECT workspace, ?, enabled, changed_at FROM switches', (role,))
            conn.execute("INSERT INTO roles SELECT workspace, 'refine', enabled, changed_at FROM refine_switches")
    return conn


def roles(workspace: str) -> set[str]:
    """The roles the person switched on for ``workspace`` on this device; none by default (ADR 0237)."""
    with closing(_connect()) as conn:
        return {row['role'] for row in conn.execute('SELECT role FROM roles WHERE workspace = ? AND enabled = 1',
                                                    (workspace,)) if row['role'] in ROLES}


def set_roles(workspace: str, wanted) -> set[str]:
    """Switch on exactly ``wanted`` for ``workspace`` on this device, and the
    rest off; returns them."""
    wanted = set(wanted)
    unknown = wanted - set(ROLES)
    if unknown:
        raise ValueError(f"unknown Dispatcher role {', '.join(sorted(unknown))}: the roles are {', '.join(ROLES)}")
    now = time.time()
    with closing(_connect()) as conn, conn:
        for role in ROLES:
            conn.execute('INSERT INTO roles (workspace, role, enabled, changed_at) VALUES (?, ?, ?, ?) '
                         'ON CONFLICT (workspace, role) DO UPDATE SET enabled = excluded.enabled, '
                         'changed_at = CASE WHEN enabled = excluded.enabled THEN changed_at ELSE excluded.changed_at END',
                         (workspace, role, int(role in wanted), now))
        # Kept for a release that reads only these, should the device go back to one (ADR 0237).
        for table, on in (('switches', any(role in wanted for role in SWITCH_ROLES)),
                          ('refine_switches', 'refine' in wanted)):
            conn.execute(f'INSERT INTO {table} (workspace, enabled, changed_at) VALUES (?, ?, ?) ON CONFLICT '
                         '(workspace) DO UPDATE SET enabled = excluded.enabled, changed_at = excluded.changed_at',
                         (workspace, int(on), now))
    return wanted


def set_role(workspace: str, role: str, enabled: bool) -> set[str]:
    """Switch one role on or off for ``workspace``; returns the roles now on."""
    current = roles(workspace)
    return set_roles(workspace, current | {role} if enabled else current - {role})


def switched_on(workspace: str) -> bool:
    """Whether any role is on for ``workspace`` on this device; off by default."""
    return bool(roles(workspace))


def switch(workspace: str, enabled: bool) -> bool:
    """The old switch, kept as a shortcut: on is Work and Review, off is every role off."""
    set_roles(workspace, roles(workspace) | set(SWITCH_ROLES) if enabled else set())
    return bool(enabled)


def last_switched_on() -> float:
    """When a role on this device was last switched on; 0 when never."""
    with closing(_connect()) as conn:
        row = conn.execute('SELECT MAX(changed_at) AS at FROM roles WHERE enabled = 1').fetchone()
    return float(row['at'] or 0)


def switched_on_workspaces() -> list[str]:
    """The workspaces with any role on, on this device."""
    with closing(_connect()) as conn:
        return [row['workspace'] for row in conn.execute(
            'SELECT DISTINCT workspace FROM roles WHERE enabled = 1 AND role IN (%s) ORDER BY workspace'
            % ','.join('?' * len(ROLES)), ROLES)]


def workspaces_with(role: str) -> list[str]:
    """The workspaces where ``role`` is on, on this device."""
    with closing(_connect()) as conn:
        return [row['workspace'] for row in conn.execute(
            'SELECT workspace FROM roles WHERE role = ? AND enabled = 1 ORDER BY workspace', (role,))]


def refining(workspace: str) -> bool:
    """Whether the Refine role is on for ``workspace`` on this device (ADRs 0230, 0237)."""
    return 'refine' in roles(workspace)


def switch_refining(workspace: str, enabled: bool) -> bool:
    set_role(workspace, 'refine', enabled)
    return bool(enabled)


def refining_workspaces() -> list[str]:
    return workspaces_with('refine')


def mark_run_cleaned(run_id: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute('UPDATE runs SET cleaned = ? WHERE id = ? AND cleaned IS NULL', (time.time(), run_id))


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


def runs_of(task: str) -> list[dict]:
    """Every run this device started on ``task``, newest first."""
    with closing(_connect()) as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM runs WHERE task = ? ORDER BY started_at DESC', (task,))]


def unfinished() -> list[dict]:
    """Runs that have not recorded how they ended, oldest first."""
    with closing(_connect()) as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM runs WHERE ended_at IS NULL ORDER BY started_at')]


def uncleaned() -> list[dict]:
    """Ended runs whose worktree has not been removed yet, one per Task, newest first."""
    with closing(_connect()) as conn:
        rows = conn.execute('SELECT * FROM runs WHERE ended_at IS NOT NULL AND cleaned IS NULL AND worktree IS NOT NULL '
                            'ORDER BY started_at DESC').fetchall()
    seen, found = set(), []
    for row in rows:
        if (row['workspace'], row['task']) not in seen:
            seen.add((row['workspace'], row['task']))
            found.append(dict(row))
    return found


def mark_cleaned(workspace: str, task: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute('UPDATE runs SET cleaned = ? WHERE workspace = ? AND task = ? AND cleaned IS NULL',
                     (time.time(), workspace, task))


def recent(limit: int = 20) -> list[dict]:
    with closing(_connect()) as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM runs ORDER BY started_at DESC LIMIT ?', (limit,))]
