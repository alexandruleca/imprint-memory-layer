"""Durable filesystem scope and purge exclusions, independent of workspace.json."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from . import config
from .wsl_paths import native_path


def workspace_name(workspace=None):
    name = workspace or config.get_active_workspace()
    if config.validate_workspace_name(name) or name not in config.get_known_workspaces():
        raise ValueError(f'Unknown or invalid workspace: {name}')
    return name


@contextmanager
def _db():
    directory = config.get_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(directory / 'workspace-policy.sqlite3', timeout=10)
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS roots (workspace TEXT, path TEXT, PRIMARY KEY(workspace, path))')
        conn.execute('CREATE TABLE IF NOT EXISTS exclusions (workspace TEXT, kind TEXT, path TEXT, recursive INTEGER, PRIMARY KEY(workspace, kind, path))')
        conn.execute('CREATE TABLE IF NOT EXISTS project_roots (workspace TEXT, project TEXT, path TEXT, PRIMARY KEY(workspace, project))')
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def canonical(path):
    if not isinstance(path, str) or not path.strip() or '\x00' in path:
        raise ValueError('A nonempty filesystem path is required')
    return Path(native_path(path)).expanduser().resolve()


def contains(root, path):
    return path == root or path.is_relative_to(root)


def _add_root(conn, ws, path, allow_overlap):
    conflicts = sorted({owner for owner, other in conn.execute('SELECT workspace, path FROM roots')
                        if owner != ws and (contains(Path(other), path) or contains(path, Path(other)))})
    if conflicts and not allow_overlap:
        raise ValueError(f'Root overlaps workspace(s): {", ".join(conflicts)}. Confirm with workspace roots add --allow-overlap.')
    conn.execute('INSERT OR IGNORE INTO roots VALUES (?, ?)', (ws, str(path)))
    return conflicts


def roots(workspace=None):
    ws = workspace_name(workspace)
    with _db() as conn:
        return [row[0] for row in conn.execute('SELECT path FROM roots WHERE workspace=? ORDER BY path', (ws,))]


def add_root(path, workspace=None, *, allow_overlap=False):
    ws, path = workspace_name(workspace), canonical(path)
    if not path.is_dir():
        raise ValueError('Workspace roots must be existing directories')
    with _db() as conn:
        conflicts = _add_root(conn, ws, path, allow_overlap)
    return {'workspace': ws, 'roots': roots(ws), 'overlaps': conflicts}


def remove_root(path, workspace=None):
    ws, path = workspace_name(workspace), canonical(path)
    with _db() as conn:
        conn.execute('DELETE FROM roots WHERE workspace=? AND path=?', (ws, str(path)))
    return {'workspace': ws, 'roots': roots(ws)}


def ensure_ingest_paths(paths, workspace=None):
    """Bind the first root atomically; later roots require an explicit add."""
    ws = workspace_name(workspace)
    paths = [canonical(path) for path in paths]
    if not paths:
        raise ValueError('At least one ingest path is required')
    with _db() as conn:
        approved = [Path(row[0]) for row in conn.execute('SELECT path FROM roots WHERE workspace=?', (ws,))]
        if not approved:
            first = paths[0] if paths[0].is_dir() else paths[0].parent
            if not first.is_dir():
                raise ValueError('Ingest root does not exist')
            _add_root(conn, ws, first, False)
            approved = [first]
        for path in paths:
            if not any(contains(root, path) for root in approved):
                raise ValueError(f'{path} is outside workspace {ws!r} roots. Use imprint workspace roots add <directory> to approve another root.')
    return ws


def path_allowed(path, workspace=None):
    resolved = canonical(str(path))
    return any(contains(Path(root), resolved) for root in roots(workspace))


def transcript_allowed(path, workspace=None):
    """Whether a client conversation belongs to ``workspace`` (ADR 0034).

    Ownership comes from the recorded cwd evidence, never the lossy encoded
    Claude folder name: every cwd must be inside this workspace's roots or
    outside every other workspace's roots. A conversation about a project
    another workspace claimed stays there; one no workspace claimed is learned
    by the workspace that learns it. Without cwd evidence it belongs nowhere.
    """
    import json
    ws = workspace_name(workspace)
    with _db() as conn:
        claims = conn.execute('SELECT workspace, path FROM roots').fetchall()
    own = [Path(root) for owner, root in claims if owner == ws]
    others = [Path(root) for owner, root in claims if owner != ws]
    seen = False
    with open(path, encoding='utf-8') as transcript:
        for line in transcript:
            try:
                cwd = json.loads(line).get('cwd')
            except (ValueError, AttributeError):
                continue
            if cwd:
                seen = True
                if not isinstance(cwd, str) or not Path(native_path(cwd)).is_absolute():
                    return False
                where = canonical(cwd)
                if not any(contains(root, where) for root in own) and any(contains(root, where) for root in others):
                    return False
    return seen


def source_key(value):
    if not isinstance(value, str) or not value.strip() or value in ('.', '/', '\\') or '\x00' in value:
        raise ValueError('A specific source or directory prefix is required')
    value = value.replace('\\', '/').rstrip('/')
    if value in ('', '.', '..'):
        raise ValueError('A specific source or directory prefix is required')
    return value


def matches(value, target, recursive, kind):
    if kind == 'path':
        if not value:
            return False
        path = canonical(value)
        root = canonical(target)
        return contains(root, path) if recursive else path == root
    value = value.replace('\\', '/')
    return value == target or (recursive and value.startswith(target + '/'))


def exclude(target, kind, recursive, workspace=None):
    ws = workspace_name(workspace)
    with _db() as conn:
        conn.execute('INSERT OR REPLACE INTO exclusions VALUES (?, ?, ?, ?)', (ws, kind, target, int(recursive)))


def exclusions(workspace=None):
    ws = workspace_name(workspace)
    with _db() as conn:
        return [{'kind': kind, 'path': path, 'recursive': bool(recursive)} for kind, path, recursive in
                conn.execute('SELECT kind, path, recursive FROM exclusions WHERE workspace=? ORDER BY kind, path', (ws,))]


def is_excluded(*, path='', source='', workspace=None, rules=None):
    rules = exclusions(workspace) if rules is None else rules
    return any(matches(path if rule['kind'] == 'path' else source,
                       rule['path'], rule['recursive'], rule['kind']) for rule in rules)


def _check_records(records, rules):
    for record in records:
        path = record.get('source_path', '') if record.get('source_path_local') is not False else ''
        if is_excluded(path=path, source=record.get('source', ''), rules=rules):
            raise ValueError(f"Source excluded after purge: {record.get('source', '')}. Restore its workspace exclusion explicitly before importing it again.")


def check_records(records, workspace=None):
    _check_records(records, exclusions(workspace))


@contextmanager
def write_guard(records, workspace=None):
    # Serialize the final upsert against purge exclusions, after slow inference.
    ws = workspace_name(workspace)
    with _db() as conn:
        rules = [{'kind': kind, 'path': path, 'recursive': bool(recursive)} for kind, path, recursive in
                 conn.execute('SELECT kind, path, recursive FROM exclusions WHERE workspace=?', (ws,))]
        _check_records(records, rules)
        yield


def restore(target, kind, workspace=None):
    ws = workspace_name(workspace)
    target = str(canonical(target)) if kind == 'path' else source_key(target)
    with _db() as conn:
        conn.execute('DELETE FROM exclusions WHERE workspace=? AND kind=? AND path=?', (ws, kind, target))


def remove_workspace(workspace):
    with _db() as conn:
        conn.execute('DELETE FROM roots WHERE workspace=?', (workspace,))
        conn.execute('DELETE FROM exclusions WHERE workspace=?', (workspace,))
        conn.execute('DELETE FROM project_roots WHERE workspace=?', (workspace,))


def map_project(project, path, workspace=None):
    ws, path = workspace_name(workspace), canonical(path)
    if not isinstance(project, str) or not project.strip() or len(project) > 256 or not path.is_dir():
        raise ValueError('A project name and existing local directory are required')
    ensure_ingest_paths([str(path)], ws)
    with _db() as conn:
        conn.execute('INSERT OR REPLACE INTO project_roots VALUES (?, ?, ?)', (ws, project, str(path)))
    return {'workspace': ws, 'project': project, 'path': str(path)}


def project_root(project, workspace=None):
    ws = workspace_name(workspace)
    with _db() as conn:
        row = conn.execute('SELECT path FROM project_roots WHERE workspace=? AND project=?', (ws, project)).fetchone()
        return row[0] if row else None
