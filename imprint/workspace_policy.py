"""Durable filesystem scope and purge exclusions, independent of workspace.json.

Also which workspace a directory belongs to (ADR 0072): a project's
``.imprint/workspace.json`` proposes one, and this device's approved roots
decide whether it is honoured.

Stdlib only: the session-start and Stop hooks resolve a session's workspace
through it.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3

from . import config
from .wsl_paths import client_path, native_path


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
        # Placement rules for moved memories (imprint.projects, ADR 0036)
        conn.execute('CREATE TABLE IF NOT EXISTS placements (workspace TEXT, project TEXT, source TEXT, target TEXT, PRIMARY KEY(workspace, project, source))')
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


def _overlaps(conn, ws, path):
    """Other workspaces whose roots contain, or lie inside, ``path``."""
    return sorted({owner for owner, other in conn.execute('SELECT workspace, path FROM roots')
                   if owner != ws and (contains(Path(other), path) or contains(path, Path(other)))})


def _add_root(conn, ws, path, allow_overlap):
    conflicts = _overlaps(conn, ws, path)
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
            first = ingest_root(paths[0])
            if not first.is_dir():
                raise ValueError('Ingest root does not exist')
            _add_root(conn, ws, first, False)
            approved = [first]
        for path in paths:
            if not any(contains(root, path) for root in approved):
                raise ValueError(f'{path} is outside workspace {ws!r} roots. Use imprint workspace roots add <directory> to approve another root.')
    return ws


def ingest_root(path):
    """The directory whose approval admits ``path``: itself, or a file's folder."""
    return path if path.is_dir() else path.parent


def unapproved_root(path, workspace=None):
    """The root ``ensure_ingest_paths([path], workspace)`` would refuse, without binding anything.

    None when that ingest is admitted, including when it would bind the first
    root of a workspace without roots. Otherwise the directory that admits
    ``path`` once approved, with the other workspaces whose roots it overlaps:
    adding it then needs their sharing confirmed (ADR 0015).
    """
    ws, path = workspace_name(workspace), canonical(path)
    root = ingest_root(path)
    with _db() as conn:
        approved = [Path(row[0]) for row in conn.execute('SELECT path FROM roots WHERE workspace=?', (ws,))]
        if any(contains(other, path) for other in approved):
            return None
        if not approved and not root.is_dir():
            raise ValueError('Ingest root does not exist')
        overlaps = _overlaps(conn, ws, root)
        if not approved and not overlaps:
            return None
        return {'workspace': ws, 'root': str(root), 'overlaps': overlaps}


def path_allowed(path, workspace=None):
    resolved = canonical(str(path))
    return any(contains(Path(root), resolved) for root in roots(workspace))


def transcript_allowed(path, workspace=None):
    """Whether a client conversation belongs to ``workspace`` (ADR 0034, ADR 0072).

    Ownership comes from the recorded cwd evidence (Claude Code records it per
    entry, Codex per session and turn; ADR 0037; Cursor's folder name when it
    stands for exactly one existing directory, ADR 0052), never Claude's
    encoded folder name. Every cwd must be claimed by this workspace or by
    none (``Place.claimants``): a project file this device approved claims its
    project for the one workspace it names; otherwise every workspace whose
    roots contain the cwd claims it. A conversation about a project another
    workspace claimed stays there; one no workspace claimed is learned by the
    workspace that learns it. Without cwd evidence it belongs nowhere.
    """
    from .transcripts import cwds
    ws = workspace_name(workspace)
    with _db() as conn:
        claims = _claims(conn)
    # Clients repeat the cwd on most records. Resolve each one once: through
    # \\wsl.localhost every resolution is a filesystem round trip.
    checked = set()
    for cwd in cwds(path):
        if not isinstance(cwd, str):
            return False
        if cwd in checked:
            continue
        checked.add(cwd)
        if not Path(native_path(cwd)).is_absolute():
            return False
        claimants = place(canonical(cwd), claims, git=False).claimants
        if claimants and ws not in claimants:
            return False
    return bool(checked)


# ── Which workspace a directory belongs to (ADR 0072) ──────────────────────

PROJECT_FILE = Path('.imprint', 'workspace.json')


def _claims(conn):
    return [(owner, Path(root)) for owner, root in conn.execute('SELECT workspace, path FROM roots')]


def _homes():
    """The home directories of the user whose paths are resolved: this
    runtime's and, for a WSL client of a Windows runtime, the client's."""
    homes = {Path.home().resolve()}
    if os.environ.get('IMPRINT_WSL_HOME'):
        homes.add(canonical(os.environ['IMPRINT_WSL_HOME']))
    return homes


def _candidates(directory):
    """``directory`` and its ancestors that can be a project, nearest first.

    A filesystem root, a home directory or an ancestor of one never is: a
    project file there would assign every directory beneath it, and a home
    that is a git repository (dotfiles) is not a project.
    """
    homes = _homes()
    for candidate in (directory, *directory.parents):
        if candidate.parent == candidate or any(contains(candidate, home) for home in homes):
            return
        yield candidate


def read_project_file(path):
    """(workspace the project file names, why it was ignored)."""
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        return None, f'{client_path(str(path))} was ignored: {error}'
    name = data.get('workspace') if isinstance(data, dict) else None
    error = config.validate_workspace_name(name) if isinstance(name, str) else 'expected {"workspace": "<name>"}'
    if error:
        return None, f'{client_path(str(path))} was ignored: {error}'
    return name, ''


class Place:
    """What this device says about one directory's workspace.

    ``project`` is the nearest directory that holds a project file or is a git
    repository's root (``None`` outside any project); ``file`` the nearest
    project file. The file's workspace is honoured (``trusted``) only when a
    root of that workspace contains the file's directory: the file proposes,
    this device's approval decides. ``assigned`` is the trusted file's
    workspace, else the workspace with the most specific root containing the
    directory, else ``None`` (no claim, or roots of several workspaces tie).
    """

    def __init__(self, directory, claims, *, git=True):
        self.project = self.file = self.proposal = None
        self.note = ''
        if directory is not None:
            for candidate in _candidates(directory):
                has_file = (candidate / PROJECT_FILE).is_file()
                if self.project is None and (has_file or git and (candidate / '.git').exists()):
                    self.project = candidate
                if has_file:
                    self.file = candidate / PROJECT_FILE
                    self.proposal, self.note = read_project_file(self.file)
                    break
        self.trusted = bool(self.proposal) and any(
            owner == self.proposal and contains(root, self.file.parent) for owner, root in claims)
        containing = [(owner, root) for owner, root in claims
                      if directory is not None and contains(root, directory)]
        self.roots = sorted({owner for owner, _ in containing})
        deepest = max((len(root.parts) for _, root in containing), default=0)
        nearest = sorted({owner for owner, root in containing if len(root.parts) == deepest})
        self.root = next((str(root) for _, root in containing if len(root.parts) == deepest), '')
        if self.trusted:
            self.assigned, self.reason = self.proposal, 'file'
        elif len(nearest) == 1:
            self.assigned, self.reason = nearest[0], 'root'
        else:
            self.assigned, self.reason = None, ''
        self.tied = nearest if len(nearest) > 1 else []

    @property
    def claimants(self):
        """The workspaces this directory's conversations belong to: the trusted
        file's, else every workspace whose roots contain it (shared roots
        share them), else none."""
        return [self.proposal] if self.trusted else self.roots


def place(directory, claims=None, *, git=True):
    if claims is None:
        with _db() as conn:
            claims = _claims(conn)
    return Place(directory, claims, git=git)


def _default(session):
    """(workspace, why) when nothing assigns a directory: the active workspace
    for a command; for an agent session ``workspace.unassigned`` when set."""
    if session:
        from .config_schema import resolve as setting
        name, _ = setting('workspace.unassigned')
        if name:
            return workspace_name(name), 'the workspace.unassigned setting'
    return workspace_name(), 'the active workspace'


def resolve(directory, *, session=False):
    """Which workspace a command or agent session in ``directory`` uses.

    ``IMPRINT_WORKSPACE`` first, then the directory's ``Place``: its trusted
    project file, else its most specific approved root. Otherwise the default
    (``_default``). ``directory`` is ``None`` for a session without a working
    directory. A tool's explicit ``workspace`` argument outranks all of these.
    """
    where = place(None if directory is None else canonical(str(directory)))
    pinned = os.environ.get('IMPRINT_WORKSPACE')
    if pinned:
        workspace, reason, source = workspace_name(pinned), 'env', 'IMPRINT_WORKSPACE'
    elif where.assigned:
        workspace, reason = workspace_name(where.assigned), where.reason
        source = client_path(str(where.file) if reason == 'file' else where.root)
    else:
        (workspace, source), reason = _default(session), 'default'
    return {'workspace': workspace, 'reason': reason, 'source': source,
            'project': client_path(str(where.project)) if where.project else None,
            'project_dir': str(where.project) if where.project else None,
            'file': client_path(str(where.file)) if where.file else None,
            'proposal': where.proposal, 'trusted': where.trusted, 'tied': where.tied, 'note': where.note}


def describe(resolution):
    """Why a resolution chose its workspace, for people and agents."""
    return {'env': 'IMPRINT_WORKSPACE', 'file': f'project file {resolution["source"]}',
            'root': f'approved root {resolution["source"]}'}.get(
        resolution['reason'], f'{resolution["source"]}; nothing assigns this directory')


def ask(resolution):
    """What an agent should ask the user about this project's workspace, or ''.

    Nothing outside a project, and nothing once a trusted project file or an
    explicit ``IMPRINT_WORKSPACE`` decided. In a project its roots assign,
    only a project file proposing another workspace is asked about.
    """
    if not resolution['project'] or resolution['reason'] in ('env', 'file'):
        return ''
    proposal, current = resolution['proposal'], resolution['workspace']
    if resolution['reason'] == 'root' and proposal in (None, current):
        return ''
    known = config.get_known_workspaces()
    existing = ', '.join(known)
    if proposal:
        missing = '' if proposal in known else ', which does not exist on this device yet'
        return (f"{resolution['file']} asks for workspace '{proposal}'{missing}. "
                f"This device has not approved it for this project, so this session uses '{current}'. "
                f"Ask the user once whether to use '{proposal}' or another workspace ({existing}), "
                f"then call use_workspace with their choice.")
    shared = f"; roots of {' and '.join(resolution['tied'])} both cover it" if resolution['tied'] else ''
    return (f"No workspace is assigned to this project ({resolution['project']}{shared}), "
            f"so this session uses '{current}'. Ask the user once which workspace it should use, "
            f"an existing one ({existing}) or a new name, then call use_workspace with it.")


def notice(resolution):
    """The note and question an agent session in this directory gets, or ''."""
    told = ' '.join(part for part in (resolution['note'], ask(resolution)) if part)
    return f'Imprint: {told}' if told else ''


def use_workspace(name, directory, *, allow_overlap=False):
    """Assign the project at ``directory`` to workspace ``name`` on this device.

    Creates the workspace if it is new (like the CLI and dashboard), approves
    ``directory`` as one of its roots unless a root of it already contains
    the directory, and writes ``directory/.imprint/workspace.json``. Other
    workspaces' roots that overlap the directory need ``allow_overlap``, as
    any root does (ADR 0015); then nothing changes before it is confirmed.
    Git is left alone: committing the file is the user's choice.
    """
    error = config.validate_workspace_name(name) if isinstance(name, str) else 'a workspace name is required'
    if error:
        raise ValueError(f'Invalid workspace name: {error}')
    directory = canonical(str(directory))
    if not directory.is_dir():
        raise ValueError(f'{client_path(str(directory))} is not a directory')
    if next(_candidates(directory), None) != directory:
        raise ValueError('A filesystem root or home directory cannot be a project: '
                         'a project file there would assign every directory beneath it')
    with _db() as conn:
        claims = _claims(conn)
        overlaps = _overlaps(conn, name, directory)
        if overlaps and not allow_overlap:
            raise ValueError(f'{client_path(str(directory))} overlaps the roots of workspace(s) {", ".join(overlaps)}. '
                             f'Ask whether {name!r} should share it; if so, repeat with --allow-overlap '
                             '(command line) or allow_overlap=true (use_workspace).')
        created = config.register_workspace(name)
        approved = not any(owner == name and contains(root, directory) for owner, root in claims)
        if approved:
            conn.execute('INSERT OR IGNORE INTO roots VALUES (?, ?)', (name, str(directory)))
        file = directory / PROJECT_FILE
        file.parent.mkdir(exist_ok=True)
        file.write_text(json.dumps({'workspace': name}) + '\n', encoding='utf-8')
    return {'workspace': name, 'created': created, 'project': client_path(str(directory)),
            'file': client_path(str(file)), 'root_added': approved, 'overlaps': overlaps}


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
    from . import config_schema
    with _db() as conn:
        conn.execute('DELETE FROM roots WHERE workspace=?', (workspace,))
        conn.execute('DELETE FROM exclusions WHERE workspace=?', (workspace,))
        conn.execute('DELETE FROM project_roots WHERE workspace=?', (workspace,))
        conn.execute('DELETE FROM placements WHERE workspace=?', (workspace,))
    # Sessions outside an assigned project fall back to the active workspace.
    if config_schema.resolve('workspace.unassigned') == (workspace, 'config'):
        config_schema.reset_value('workspace.unassigned')


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
