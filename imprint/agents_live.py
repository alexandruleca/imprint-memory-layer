"""Live: who is working on what on this device right now (ADR 0233).

Joins what Imprint already has, without asking any model:

- the Imprint servers' session records (``agent_activity``): client, folder,
  workspace, whether the process runs, the agent's Board name, Claude Code's
  conversation id and the session's Imprint tool calls;
- the Board: the Task each session holds, its status, Stalling and Stale, and
  its last checkpoint; and paired devices' holders, which the Board syncs;
- the Dispatcher's runs: a Dispatched session's role (work, review, takeover);
- the tail of each session's own transcript (``live_transcripts``): model,
  tokens, the tool call in flight and the last few, and whether a turn runs.

Transcripts are found by the path the client's SessionStart or Stop hook
recorded, else by Claude Code's layout (folder and conversation id), else,
for Codex, by matching the folder and start time to a rollout. They are read
only when Live is asked for, and only the bytes added since the last read.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import agent_activity, live_transcripts

WAITING_MINUTES = 15      # after a turn ends, the agent waits for you this long, then counts as idle
RECENT_CALL = 60.0        # an Imprint call this recent is what a session without a transcript is doing
LONG_TOOL = 60.0          # a tool call without a result this long may be waiting for approval
MATCH = 300.0             # a transcript's first record comes within this many seconds of its Imprint server's start
RESCAN = 30.0             # a session without a transcript is looked for again after this long
_FOLDER = re.compile(r'[^A-Za-z0-9]')
_projects: dict[str, str] = {}
_found: dict[str, str] = {}        # session id → its transcript
_looked: dict[str, float] = {}     # session id → when its transcript was last looked for
_firsts: dict[str, tuple[float | None, str | None]] = {}  # transcript → its first record's time and folder


def _project(directory: str | None) -> str:
    """The project a session's folder holds, named as the Board names it: a
    WSL folder by the path its client sees (ADR 0211)."""
    if not directory:
        return ''
    if directory not in _projects:
        try:
            from .cli_conversations import derive_project
            _, folder = _home(directory)
            _projects[directory] = derive_project(folder or directory) or ''
        except Exception:  # noqa: BLE001 - a folder Live cannot name shows without a project
            _projects[directory] = ''
    return _projects[directory]


def _client(name: str | None) -> str:
    from .tasks import client_id
    return client_id(name) or 'unknown'


# ── Finding a session's transcript ────────────────────────────────────

def _home(directory: str | None) -> tuple[str | None, str | None]:
    """The client's home folder, as this runtime reads it, and the session's
    folder as the client sees it. A WSL session served by a Windows runtime
    has a \\\\wsl.localhost folder: its home is the distribution's /home/<user>."""
    if not directory:
        return None, None
    if directory.startswith('\\\\'):
        from .wsl_paths import windows_to_linux
        located = windows_to_linux(directory)
        found = re.match(r'(/home/[^/]+|/root)(?=/|$)', located[1]) if located else None
        if not found:
            return None, None
        return f'\\\\wsl.localhost\\{located[0]}' + found.group(1).replace('/', '\\'), located[1]
    return str(Path.home()), directory


def _join(home: str, *parts: str) -> str:
    separator = '\\' if home.startswith('\\\\') else '/'
    return separator.join([home.rstrip('\\/'), *parts]) if separator == '\\' else str(Path(home, *parts))


def _claude_path(directory: str | None, conversation: str) -> str | None:
    home, folder = _home(directory)
    if not home:
        return None
    candidate = _join(home, '.claude', 'projects', _FOLDER.sub('-', folder), f'{conversation}.jsonl')
    return candidate if Path(candidate).is_file() else None


def _first(path: str) -> tuple[float | None, str | None]:
    """When a transcript's first timestamped record was written, and the
    folder it names (Codex's session_meta). Read once per file."""
    if path not in _firsts:
        found: tuple[float | None, str | None] = (None, None)
        try:
            with open(path, encoding='utf-8', errors='replace') as handle:
                for number, line in enumerate(handle):
                    if number > 50:
                        break
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    payload = record.get('payload') if isinstance(record.get('payload'), dict) else {}
                    at = live_transcripts._timestamp(payload) or live_transcripts._timestamp(record)
                    if at is not None:
                        found = (at, payload.get('cwd') or record.get('cwd'))
                        break
        except OSError:
            return None, None  # unreadable now: try again next time
        _firsts[path] = found
    return _firsts[path]


def _candidates(client: str, directory: str | None, started: float) -> list[str]:
    """Transcripts this session's client may be writing: Claude Code's in the
    folder's projects entry, Codex's rollouts of the days around its start."""
    home, folder = _home(directory)
    if not home:
        return []
    try:
        if client == 'claude-code':
            paths = list(Path(_join(home, '.claude', 'projects', _FOLDER.sub('-', folder))).glob('*.jsonl'))
        elif client == 'codex':
            root = Path(_join(home, '.codex', 'sessions'))
            days = {time.strftime('%Y/%m/%d', time.localtime(started + offset)) for offset in (-86400, 0)}
            paths = [path for day in days for path in (root / day).glob('rollout-*.jsonl')]
        else:
            return []
        return [str(path) for path in paths if path.stat().st_mtime >= started - MATCH]
    except OSError:
        return []


def _resolve(sessions: list[dict], recorded: dict[str, dict], hooks: list[dict], now: float) -> dict[str, str]:
    """Each session's transcript, one transcript per session: by Claude
    Code's conversation id, else the path its hook recorded, else the
    transcript whose first record is nearest the session's start (within
    :data:`MATCH`). Matches are kept; a session without one is looked for
    again after :data:`RESCAN`."""
    from .tasks.sessions import conversation_id
    found: dict[str, str] = {}
    for session in sessions:
        conversation = conversation_id(session.get('board_session'))
        path = _found.get(session['id'])
        if not path and conversation:
            path = recorded[conversation]['path'] if conversation in recorded else (
                _claude_path(session.get('directory'), conversation) if _client(session.get('client')) == 'claude-code' else None)
        if path:
            found[session['id']] = _found[session['id']] = path
    taken = set(found.values())
    pairs = []
    for session in sessions:
        if session['id'] in found or now - _looked.get(session['id'], 0) < RESCAN:
            continue
        client, started = _client(session.get('client')), session['started_at']
        options = [(abs(t['recorded_at'] - started), t['path']) for t in hooks
                   if t.get('client') == client and t.get('cwd') == session.get('directory')]
        _, folder = _home(session.get('directory'))
        for path in _candidates(client, session.get('directory'), started):
            at, cwd = _first(path)
            if at is not None and (client != 'codex' or cwd == folder):
                options.append((abs(at - started), path))
        pairs += [(distance, session['id'], path) for distance, path in options if distance <= MATCH]
        _looked[session['id']] = now
    for distance, session_id, path in sorted(pairs):
        if session_id not in found and path not in taken:
            found[session_id] = _found[session_id] = path
            taken.add(path)
            _looked.pop(session_id, None)
    return found


# ── What a session is doing ───────────────────────────────────────────

def _imprint_activity(tool: str | None) -> dict:
    action = live_transcripts.classify(f'mcp__imprint__{tool}', None) if tool else {}
    return {'kind': action.get('kind', 'imprint'), 'label': action.get('label', 'Using Imprint'), 'detail': action.get('detail', '')}


def activity(state: live_transcripts.State | None, session: dict, now: float) -> tuple[str, dict]:
    """``(state, activity)``: working, waiting or idle, and what it is doing.

    From the transcript when there is one: the newest tool call in flight
    (one without a result for :data:`LONG_TOOL` may be waiting for your
    approval, which the transcript cannot tell apart), else Thinking while a
    turn runs, else Waiting for you for :data:`WAITING_MINUTES` after a turn
    ends, then Idle. Without one, a recent Imprint call, else Idle since the
    session was last seen."""
    if state is not None and (state.turn_changed or state.last_at):
        running = sorted(state.pending.values(), key=lambda a: a.get('at') or 0, reverse=True)
        if running:
            current = running[0]
            since = current.get('at') or state.last_at or now
            if current['kind'] == 'asking':
                return 'waiting', {**current, 'since': since, 'long': False}
            return 'working', {**current, 'since': since, 'long': now - since > LONG_TOOL}
        if state.turn_running:
            return 'working', {'kind': 'thinking', 'label': 'Thinking', 'detail': '',
                               'since': state.last_at or state.turn_changed or now, 'long': False}
        ended = state.turn_changed or state.last_at or now
        if now - ended <= WAITING_MINUTES * 60:
            return 'waiting', {'kind': 'waiting', 'label': 'Waiting for you', 'detail': '', 'since': ended, 'long': False}
        return 'idle', {'kind': 'idle', 'label': 'Idle', 'detail': '', 'since': ended, 'long': False}
    last_call = session.get('last_call_at')
    if last_call and now - last_call <= RECENT_CALL:
        return 'working', {**_imprint_activity(session.get('last_tool')), 'since': last_call, 'long': False}
    since = last_call or session.get('last_seen') or session.get('started_at') or now
    return 'idle', {'kind': 'idle', 'label': 'Idle', 'detail': '', 'since': since, 'long': False}


# ── The Board and the Dispatcher ──────────────────────────────────────

def _boards(workspaces: set[str]) -> dict[str, list[dict]]:
    from . import tasks
    found = {}
    for workspace in workspaces:
        try:
            found[workspace] = [t for t in tasks.listing(workspace=workspace) if t.get('holder')]
        except (ValueError, LookupError, OSError):
            found[workspace] = []
    return found


def _runs() -> list[dict]:
    from .dispatch import store
    if not store._db_path().exists():
        return []
    try:
        return store.unfinished()
    except Exception:  # noqa: BLE001 - Live shows sessions without their role rather than nothing
        return []


_ROLES = {'work': 'working', 'review': 'reviewing', 'takeover': 'taking-over', 'refine': 'refining'}


def _task(held: dict | None, run: dict | None) -> dict | None:
    if not held:
        return None
    checkpoint = held.get('last_checkpoint') or {}
    role = _ROLES.get((run or {}).get('role') or '', 'reviewing' if held['status'] == 'review' else 'working')
    return {'id': held['id'], 'title': held['title'], 'status': held['status'], 'role': role,
            'stalling': bool(held.get('stalling')), 'next': checkpoint.get('next') or None,
            'checkpoint_at': checkpoint.get('at')}


# ── Live ──────────────────────────────────────────────────────────────

def _calls(session_ids: list[str]) -> dict[str, float]:
    """When each session last called Imprint."""
    if not session_ids:
        return {}
    try:
        conn = agent_activity._connect()
        rows = conn.execute(f"SELECT session_id, MAX(started_at) AS at FROM agent_calls WHERE session_id IN "
                            f"({','.join('?' * len(session_ids))}) GROUP BY session_id", session_ids).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {row['session_id']: row['at'] for row in rows}


def live(now: float | None = None) -> dict:
    """This device's running agents, paired devices' Board holders, counts and today's tokens."""
    now = now if now is not None else time.time()
    from .tasks import actors
    from .tasks.sessions import conversation_id
    day_start = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    sessions = agent_activity.connected_sessions(seen_since=day_start)
    # One card per conversation: a client that restarted its Imprint server keeps its newest process.
    newest: dict[str, dict] = {}
    for session in sessions:
        key = session.get('board_session') or session['id']
        if key not in newest or session['last_seen'] > newest[key]['last_seen']:
            newest[key] = session
    sessions = list(newest.values())
    ids = {s['id'] for s in sessions}
    for cache in (_found, _looked):
        for key in [k for k in cache if k not in ids]:
            del cache[key]
    if len(_firsts) > 5000:
        _firsts.clear()
    calls = _calls([s['id'] for s in sessions])
    for session in sessions:
        session['last_call_at'] = calls.get(session['id'])
        # A session that never used the Board would claim as its process (tasks_tool.session).
        session['board_session'] = session.get('board_session') or f"process:{session['id']}"
    conversations = [c for c in (conversation_id(s.get('board_session')) for s in sessions) if c]
    recorded = {t['conversation']: t for t in agent_activity.transcripts(conversations)}
    paths = _resolve(sessions, recorded, agent_activity.transcripts(since=day_start - 86400), now)
    boards = _boards({s['workspace'] for s in sessions if s.get('workspace')} | {_active_workspace()})
    held = {t['holder'].get('session'): t for tasks_ in boards.values() for t in tasks_}
    runs = _runs()
    device = actors._device()
    today = time.strftime('%Y-%m-%d', time.localtime(now))
    agents, day_tokens = [], {}
    for session in sessions:
        client = _client(session.get('client'))
        path = paths.get(session['id'])
        state = live_transcripts.read(path, 'codex' if client == 'codex' else 'claude') if path else None
        if state is not None:
            totals = day_tokens.setdefault(client, {'new_input': 0, 'cached_input': 0, 'output': 0})
            for key, value in state.day_tokens(today).items():
                totals[key] += value
        if not session['connected']:
            continue  # ended today: counted in today's tokens only
        status, doing = activity(state, session, now)
        conversation = conversation_id(session.get('board_session'))
        run = next((r for r in runs if (conversation and r.get('conversation') == conversation)
                    or (r.get('session') and r.get('session') == session.get('board_session'))), None)
        agents.append({
            'id': session['id'], 'board_session': session.get('board_session'), 'name': session.get('agent_name'),
            'client': client, 'client_version': session.get('client_version'),
            'model': state.model if state else None, 'directory': session.get('directory'),
            'project': _project(session.get('directory')), 'workspace': session.get('workspace'),
            'pid': session.get('pid'), 'started_at': session['started_at'], 'last_seen': session['last_seen'],
            'state': status, 'activity': doing,
            'task': _task(held.get(session.get('board_session')), run),
            'dispatched': {'run': run['id'], 'role': run.get('role')} if run else None,
            'actions': state.actions(now) if state else [],
            'tokens': state.tokens() if state else None, 'transcript': path is not None,
            'last_call_at': session.get('last_call_at'),
        })
    order = {'waiting': 0, 'working': 1, 'idle': 2}
    agents.sort(key=lambda a: (a['project'] or '~', order[a['state']], -(a['activity'].get('since') or 0)))
    others = [{'name': t['holder'].get('name'), 'client': t['holder'].get('client'), 'device': t['holder'].get('device'),
               'task': t['id'], 'title': t['title'], 'project': t['project'], 'status': t['status'],
               'next': (t.get('last_checkpoint') or {}).get('next'), 'progress_at': t['holder'].get('progress_at'),
               'stalling': bool(t.get('stalling'))}
              for tasks_ in boards.values() for t in tasks_ if t['holder'].get('device') not in (device, None)]
    counts = {state: sum(1 for a in agents if a['state'] == state) for state in order}
    return {'now': now, 'device': device, 'agents': agents, 'others': others,
            'counts': {**counts, 'others': len(others)},
            'today': [{'client': client, **totals} for client, totals in sorted(day_tokens.items())]}


def waiting(now: float | None = None) -> dict:
    """The agents waiting for you, by Live's rules: ``{count, agents}``,
    each with its name, client, project, Task and since when (ADR 0232)."""
    report = live(now)
    found = [{'id': a['id'], 'name': a['name'], 'client': a['client'], 'project': a['project'],
              'task': {'id': a['task']['id'], 'title': a['task']['title']} if a['task'] else None,
              'since': a['activity']['since']}
             for a in report['agents'] if a['state'] == 'waiting']
    return {'count': len(found), 'agents': found}


# ── History ───────────────────────────────────────────────────────────

HISTORY_TOTALS = 5        # finished sessions whose transcripts one History request may read for totals


def _source_key(path: str | None, conversation: str | None) -> str | None:
    """The source a learned conversation is stored under, as learning keys it
    (``cli_conversations.identify``)."""
    if path:
        from . import transcripts
        try:
            session = transcripts.session(path)
        except OSError:
            session = None
        return f"conversation/{session or Path(path.replace(chr(92), '/')).stem[:8]}"
    return f"conversation/{conversation[:8]}" if conversation else None


def _dispatch_runs(conversations: list[str], boards: list[str]) -> list[dict]:
    from .dispatch import store
    if not store._db_path().exists() or not (conversations or boards):
        return []
    from contextlib import closing
    try:
        with closing(store._connect()) as conn:
            return [dict(row) for row in conn.execute(
                f"SELECT * FROM runs WHERE conversation IN ({','.join('?' * len(conversations)) or 'NULL'})"
                f" OR session IN ({','.join('?' * len(boards)) or 'NULL'}) ORDER BY started_at DESC", [*conversations, *boards])]
    except Exception:  # noqa: BLE001 - History shows sessions without their outcome rather than nothing
        return []


def history(sessions: list[dict], now: float | None = None) -> list[dict]:
    """History's sessions with what Live knows (ADR 0233): the project, the
    Tasks each claimed, a Dispatched run's outcome, the model and token
    totals, and its conversation in Imprint once learned. A finished
    session's totals are read from its transcript the first time it is
    shown, at most :data:`HISTORY_TOTALS` a request, and saved on it, so
    they are never read again; one without a transcript keeps none."""
    from . import source_snapshots, tasks
    from .tasks.sessions import conversation_id
    now = now if now is not None else time.time()
    boards = {s['id']: s.get('board_session') or f"process:{s['id']}" for s in sessions}
    by_workspace: dict[str, list[str]] = {}
    for session in sessions:
        by_workspace.setdefault(session.get('workspace') or _active_workspace(), []).append(boards[session['id']])
    claims: dict[str, list[str]] = {}
    for workspace, names in by_workspace.items():
        try:
            claims.update(tasks.claimed_by(names, workspace=workspace))
        except (ValueError, LookupError, OSError):
            pass
    conversations = {s['id']: conversation_id(boards[s['id']]) for s in sessions}
    runs = _dispatch_runs([c for c in conversations.values() if c], list(boards.values()))
    pending = [s for s in sessions if not s.get('connected') and s.get('totals_at') is None][:HISTORY_TOTALS]
    if pending:
        recorded = {t['conversation']: t for t in agent_activity.transcripts([c for c in (conversations[s['id']] for s in pending) if c])}
        hooks = agent_activity.transcripts(since=min(s['started_at'] for s in pending) - 86400)
        for session in pending:
            session['board_session'] = boards[session['id']]
        paths = _resolve(pending, recorded, hooks, now)
        for session in pending:
            path = paths.get(session['id'])
            client = 'codex' if _client(session.get('client')) == 'codex' else 'claude'
            state = live_transcripts.summarize(path, client) if path else None
            figures = state.tokens() if state else None
            agent_activity.save_totals(session['id'], model=state.model if state else None, transcript=path if state else None,
                                       tokens=figures)
            session.update(model=state.model if state else None, transcript=path if state else None, totals_at=now,
                           tokens_new=(figures or {}).get('new_input'), tokens_cached=(figures or {}).get('cached_input'),
                           tokens_out=(figures or {}).get('output'))
    keys = {s['id']: _source_key(s.get('transcript') or _found.get(s['id']), conversations[s['id']]) for s in sessions}
    learned: dict[str, str] = {}
    for workspace in {s.get('workspace') or _active_workspace() for s in sessions}:
        try:
            learned.update(source_snapshots.learned([k for k in keys.values() if k], workspace))
        except Exception:  # noqa: BLE001 - a missing snapshot database means nothing is learned yet
            pass
    for session in sessions:
        board = boards[session['id']]
        run = next((r for r in runs if (conversations[session['id']] and r.get('conversation') == conversations[session['id']])
                    or r.get('session') == board), None)
        session['project'] = _project(session.get('directory'))
        session['tasks'] = claims.get(board, [])
        session['outcome'] = (run or {}).get('outcome')
        session['dispatched_role'] = (run or {}).get('role')
        if session.get('connected') and session['id'] in _found:  # running: Live's tail, never saved
            state = live_transcripts.read(_found[session['id']], 'codex' if _client(session.get('client')) == 'codex' else 'claude')
            figures = state.tokens()
            session['model'] = state.model
            session['tokens'] = {key: figures[key] for key in ('new_input', 'cached_input', 'output')}
        else:
            session['tokens'] = None if session.get('tokens_new') is None else {
                'new_input': session['tokens_new'], 'cached_input': session.get('tokens_cached') or 0,
                'output': session.get('tokens_out') or 0}
        key = keys[session['id']]
        session['conversation'] = {'source': key, 'project': learned[key]} if key in learned else None
    return sessions


def _active_workspace() -> str:
    from . import config
    return config.get_active_workspace()
