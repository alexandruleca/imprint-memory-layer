"""Finding work (ADRs 0211, 0219): what an agent may take unasked, a Feature's frontier, and a handoff."""

from __future__ import annotations

import json
import time

from . import sessions
from .actors import _who
from .store import _events, _reading, _uid, _writing
from .view import _View, _order, _view


AUTONOMY = ('off', 'stale', 'ready')


def autonomy(workspace: str | None = None) -> str:
    """What agents in this workspace take unasked: ``off``, ``stale`` (Stale
    Tasks only) or ``ready`` (Ready and Stale). A workspace's own choice,
    else this device's ``tasks.autonomy``."""
    with _reading(workspace) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'autonomy'").fetchone() if conn else None
    if row and row['value'] in AUTONOMY:
        return row['value']
    from ..config_schema import resolve
    value = str(resolve('tasks.autonomy')[0]).strip().lower()
    return value if value in AUTONOMY else 'ready'


def set_autonomy(value: str, workspace: str | None = None) -> str:
    value = (value or '').strip().lower()
    if value not in AUTONOMY:
        raise ValueError('autonomy is off, stale or ready')
    with _writing(workspace) as conn:
        conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('autonomy', ?, ?) "
                     'ON CONFLICT (key) DO UPDATE SET value = excluded.value, '
                     'updated_at = MAX(excluded.updated_at, updated_at + 0.001)', (value, time.time()))
    return value


DISPATCHING = 'dispatching:'


def announce_dispatching(device: str, name: str, on: bool, workspace: str | None = None) -> None:
    """Tell paired devices whether this device's Dispatcher is switched on
    for this workspace (ADR 0220). The switch itself stays on the device;
    this copy travels with the Board only so other devices can show it."""
    value = json.dumps({'on': bool(on), 'name': str(name)[:64]})
    with _writing(workspace) as conn:
        conn.execute('INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) '
                     'ON CONFLICT (key) DO UPDATE SET value = excluded.value, '
                     'updated_at = MAX(excluded.updated_at, updated_at + 0.001)',
                     (f'{DISPATCHING}{device}', value, time.time()))


def dispatching(workspace: str | None = None) -> list[dict]:
    """Every device that announced its Dispatcher for this workspace:
    ``{'device', 'name', 'on', 'at'}``."""
    with _reading(workspace) as conn:
        rows = conn.execute('SELECT key, value, updated_at FROM settings WHERE key LIKE ?',
                            (f'{DISPATCHING}%',)).fetchall() if conn else []
    found = []
    for row in rows:
        try:
            value = json.loads(row['value'])
            found.append({'device': row['key'][len(DISPATCHING):], 'name': str(value['name']), 'on': bool(value['on']),
                          'at': float(row['updated_at'])})
        except (ValueError, KeyError, TypeError):
            continue
    return sorted(found, key=lambda d: d['name'].lower())


def _for_agent(assignees: list[str], client: str | None, device: str | None) -> bool:
    """Whether an agent of ``client`` on ``device`` may take it unasked."""
    if 'user' in assignees:
        return False
    if not assignees:
        return True
    client, device = (client or '').lower(), (device or '').lower()
    return any(a in (client, f'@{device}', f'{client}@{device}') for a in assignees if a)


def _takeable(view: _View, state: dict, *, ready: bool) -> bool:
    """Open to a claim: Stale, or (when ``ready``) Ready and unclaimed, and
    neither marked Blocked nor waiting on a Task that is not Done."""
    if state['blocked'] or any(view.by_uid[b]['status'] != 'done' for b in state['blockers'] if b in view.by_uid):
        return False
    _, stale = view.claim(state)
    if stale:
        return state['status'] in ('ready', 'in_progress')
    return ready and not state['holder'] and state['status'] == 'ready'


def _standalone(view: _View, state: dict, features: set[str]) -> bool:
    """Neither a Feature (a Task other Tasks belong to) nor a Task of one
    (ADR 0219); a Task whose Feature was deleted stands alone again."""
    return state['uid'] not in features and state['feature'] not in view.by_uid


def takeable(*, projects=None, client: str | None = None, device: str | None = None,
             workspace: str | None = None) -> list[dict]:
    """Every Task an agent of ``client`` on ``device`` may take unasked, in
    the Board's order (highest priority, then position, then oldest):
    standalone, Ready or Stale as the workspace's autonomy allows, open to a
    claim and for that agent. ``projects`` limits it to those projects; the
    Dispatcher looks across every project with a folder on its device
    (ADR 0220)."""
    mode = autonomy(workspace)
    if mode == 'off':
        return []
    with _reading(workspace) as conn:
        view = _view(conn)
    features = {s['feature'] for s in view.by_uid.values() if s['feature']}
    found = [s for s in view.by_uid.values()
             if (projects is None or s['project'] in projects) and _standalone(view, s, features)
             and _for_agent(s['assignees'], client, device) and _takeable(view, s, ready=mode == 'ready')]
    return [view.public(s) for s in sorted(found, key=_order)]


def next_task(*, project: str, client: str | None = None, device: str | None = None,
              workspace: str | None = None) -> dict | None:
    """The Task an agent of ``client`` on ``device`` working in ``project``
    may take unasked, if any: a standalone one, highest priority, then
    position, then oldest. A Feature's Tasks are taken when asked, through
    its frontier."""
    found = takeable(projects={project}, client=client, device=device, workspace=workspace)
    return found[0] if found else None


def frontier(feature: str, workspace: str | None = None) -> list[dict]:
    """A Feature's Tasks that can be taken now, in order: Ready or Stale,
    unblocked, unclaimed and not the user's. Planning skills work it when
    asked, so the workspace's autonomy does not limit it."""
    with _reading(workspace) as conn:
        uid = _uid(conn, feature)
        view = _view(conn)
    found = [s for s in view.by_uid.values()
             if s['feature'] == uid and 'user' not in s['assignees'] and _takeable(view, s, ready=True)]
    return [view.public(s) for s in sorted(found, key=_order)]


def _conversation(holder: dict | None) -> str | None:
    """The source a holder's conversation is learned under, when its session
    is a client conversation: Claude Code and Cursor key it by the first
    eight characters of its id, the other clients by the whole id."""
    conversation = sessions.conversation_id((holder or {}).get('session'))
    if not conversation:
        return None
    short = (holder.get('client') or '').lower().startswith(('claude', 'cursor'))
    return f'conversation/{conversation[:8] if short else conversation}'


def handoff(task: str, workspace: str | None = None) -> dict:
    """What the next agent starts from: the Task, who held it, its
    checkpoints, where the work is, and the previous holder's conversation."""
    with _reading(workspace) as conn:
        uid = _uid(conn, task)
        view = _view(conn)
        events = _events(conn, uid)
    shown = view.public(view.by_uid[uid])
    checkpoints = [{**e['data'], 'by': _who(e), 'at': e['recorded_at']} for e in events if e['kind'] == 'checkpoint']
    last = checkpoints[-1] if checkpoints else None
    files = []
    for checkpoint in reversed(checkpoints):
        files.extend(f for f in checkpoint.get('files', []) if f not in files)
    previous = shown['holder'] or shown.get('previous_holder')
    return {'task': shown, 'previous_holder': previous, 'last_checkpoint': last, 'checkpoints': checkpoints[-10:],
            'branch': (last or {}).get('branch'), 'commit': (last or {}).get('commit'), 'files': files[:100],
            'conversation': _conversation(previous)}
