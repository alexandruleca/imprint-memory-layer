"""The Board through Imprint's MCP server: what the ``tasks`` and
``task_edit`` tools reply (ADR 0208). The tools' functions in ``server.py``
pass the session's project and client; everything else is here, so the CLI
and the tools word the Board the same way."""

from __future__ import annotations

import os
import time

from . import tasks

STATUS_LABELS = {'backlog': 'Backlog', 'ready': 'Ready', 'in_progress': 'In progress', 'review': 'Review',
                 'done': 'Done', 'cancelled': 'Cancelled'}
COLUMN_LINES = 20


def status_ref(value: str) -> str:
    """A status in its stored form, from ``In progress``, ``in-progress`` or ``in_progress``."""
    status = (value or '').strip().lower().replace('-', '_').replace(' ', '_')
    if status not in tasks.STATUSES:
        raise ValueError(f'status must be one of {", ".join(STATUS_LABELS.values())}')
    return status


def session() -> str:
    """The agent session this MCP server serves (ADR 0210): the client's own
    conversation id where it passes one (Claude Code's), else this process."""
    conversation = os.environ.get('CLAUDE_CODE_SESSION_ID') or os.environ.get('IMPRINT_CLIENT_SESSION')
    if conversation:
        return f'conversation:{conversation}'
    from . import agent_activity
    return f"process:{agent_activity.current().get('session') or os.getpid()}"


def actor(client: str | None = None) -> dict:
    """The agent session this MCP server serves, as the actor of its changes."""
    from . import agent_activity
    return tasks.agent(session(), client=client or agent_activity.current().get('client'))


# ── The holder's Imprint keeps its claims alive ──────────────────

_activity = {'at': None}


def touch() -> None:
    """The session just called Imprint: activity that counts as progress."""
    _activity['at'] = time.time()


def renew_now() -> int:
    return tasks.renew(session(), active_at=_activity['at'])


def start_renewing(interval: float = 60) -> None:
    """Renew this session's leases now and then every ``interval`` seconds,
    for as long as the process runs."""
    import threading

    def loop():
        while True:
            try:
                renew_now()
            except Exception:  # A busy or missing store must not end the renewals.
                pass
            time.sleep(interval)
    threading.Thread(target=loop, name='imprint-task-leases', daemon=True).start()


def notices(workspace: str | None = None) -> str:
    """What this session should hear on its next reply: claims it lost."""
    me = session()
    lost = tasks.lost_claims(me, workspace=workspace)
    told = set(tasks.take_unseen(me, [f"lost:{item['claim']}" for item in lost], workspace=workspace))
    lines = [f"You lost your claim on {item['task']} ({item['title']}) to {_label(item['to'])}, "
             'who claimed it first on another device. Stop working on it.'
             for item in lost if f"lost:{item['claim']}" in told]
    return '\n'.join(lines)


def project(given: str = '', session_project: str | None = None) -> str:
    """The project a new Task belongs to: the one named, else the session's."""
    if given.strip():
        return given.strip()
    if session_project:
        return session_project
    from .cli_conversations import derive_project
    found = derive_project(os.getcwd())
    if not found:
        raise ValueError('No project found for this directory: name the project')
    return found


# ── Wording ──────────────────────────────────────────────────────

def _who(person: dict) -> str:
    if person.get('kind') == 'user':
        return 'the user'
    name = person.get('name') or 'an agent'
    client = person.get('client')
    return f'{name} ({client})' if client else name


def _when(seconds: float) -> str:
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(seconds))


def _label(holder: dict) -> str:
    name = holder.get('name') or ''
    client = holder.get('client') or ''
    if name and client:
        return f'{name} ({client})'
    return name or client or 'an agent'


def _ago(seconds: float) -> str:
    minutes = max(0, int((time.time() - seconds) // 60))
    return f'{minutes} min ago' if minutes < 120 else f'{minutes // 60} h ago'


def line(state: dict) -> str:
    """One Task on one line: id, priority, title, project, tags, assignees and
    what holds it up."""
    text = f"{state['id']} {state['priority']} {state['title']} · {state['project']}"
    if state.get('tags'):
        text += ' ' + ' '.join(f'#{tag}' for tag in state['tags'])
    if state.get('assignees'):
        text += ' → ' + ', '.join(state['assignees'])
    if state.get('holder'):
        text += f" [{'stalling, ' if state.get('stalling') else ''}held by {_label(state['holder'])}]"
    elif state.get('stale'):
        text += f" [stale: {_label(state['previous_holder'])} stopped]"
    if state.get('blocked'):
        text += ' [blocked]'
    if state.get('open_blockers'):
        text += f" [waits on {', '.join(state['open_blockers'])}]"
    return text


def detail(state: dict) -> str:
    lines = [f"{state['id']} · {state['title']}",
             f"Status: {STATUS_LABELS[state['status']]} · Priority: {state['priority']} · Project: {state['project']}"]
    if state.get('feature'):
        lines.append(f"Feature: {state['feature']} {state.get('feature_title') or ''}".rstrip())
    if state.get('tasks'):
        lines.append('Tasks: ' + ', '.join(state['tasks']))
    if state.get('tags'):
        lines.append('Tags: ' + ', '.join(state['tags']))
    if state.get('assignees'):
        lines.append('Assignees: ' + ', '.join(state['assignees']))
    if state.get('blockers'):
        open_ones = set(state.get('open_blockers') or [])
        lines.append('Blocked by: ' + ', '.join(f"{b} ({'open' if b in open_ones else 'done'})"
                                                for b in state['blockers']))
    if state.get('blocked'):
        lines.append(f"Blocked: {state['blocked']['reason']} ({_who(state['blocked']['by'])})")
    holder = state.get('holder')
    if holder:
        lines.append(f"Held by {_label(holder)} on {holder['device']} since {_when(holder['since'])}, "
                     f"last progress {_ago(holder['progress_at'])}")
        if state.get('stalling'):
            lines.append('Stalling: no progress for a while; checkpoint, or release it if you stopped')
    elif state.get('stale'):
        lines.append(f"Stale: {_label(state['previous_holder'])} stopped working on it; "
                     'take it over with task_edit(action="claim")')
    checkpoint = state.get('last_checkpoint')
    if checkpoint:
        where = ' @ '.join(part for part in (checkpoint.get('branch'), checkpoint.get('commit')) if part)
        lines.append(f"Last checkpoint ({_who(checkpoint['by'])}, {_when(checkpoint['at'])}"
                     f"{', ' + where if where else ''}): {checkpoint['text']}")
        if checkpoint.get('next'):
            lines.append(f"Next: {checkpoint['next']}")
        if checkpoint.get('files'):
            lines.append('Files: ' + ', '.join(checkpoint['files'][:20]))
    if state.get('worked_by') and state['status'] == 'review':
        lines.append('Waiting for review by the user or another session')
    lines.append(f"Created by {_who(state['created_by'])}, {_when(state['created_at'])}")
    if state.get('ended_at'):
        lines.append(f"Ended {_when(state['ended_at'])}")
    if state.get('acceptance'):
        lines.append('Acceptance criteria:')
        lines.extend(f'- {criterion}' for criterion in state['acceptance'])
    if state.get('description'):
        lines.extend(['Description:', state['description']])
    return '\n'.join(lines)


def board_text(board: dict, project: str | None = None) -> str:
    total = sum(len(column) for column in board['columns'].values())
    scope = f" (project {project})" if project else ''
    if not total:
        return (f"No tasks yet on workspace {board['workspace']}'s Board{scope}. "
                'Create one with task_edit(action="create", title="...").')
    lines = [f"Board of workspace {board['workspace']}{scope}: {total} tasks"]
    for status, column in board['columns'].items():
        lines.append(f'{STATUS_LABELS[status]} ({len(column)})')
        lines.extend(f'- {line(state)}' for state in column[:COLUMN_LINES])
        if len(column) > COLUMN_LINES:
            lines.append(f'- and {len(column) - COLUMN_LINES} more: tasks(action="list", status="{status}")')
    return '\n'.join(lines)


def list_text(states: list[dict]) -> str:
    if not states:
        return 'No tasks match.'
    return '\n'.join(line(state) for state in states)


def history_text(events: list[dict]) -> str:
    lines = []
    for event in events:
        who = 'the user' if event['actor'] == 'user' else (event.get('agent') or event.get('client') or 'an agent')
        data = {k: v for k, v in event['data'].items() if k != 'id'}
        lines.append(f"{_when(event['recorded_at'])} {event['kind']} by {who}" + (f': {data}' if data else ''))
    return '\n'.join(lines)


# ── The tools ────────────────────────────────────────────────────

def filters(*, project='', feature='', tag='', status='', priority='', assignee='', text='') -> dict:
    """The filters given, in the tasks module's form."""
    return {key: value for key, value in {
        'project': project or None, 'feature': feature or None, 'tag': tag or None,
        'status': status_ref(status) if status else None, 'priority': priority or None,
        'assignee': assignee or None, 'text': text or None}.items() if value}


def read(action: str, *, task: str = '', archived: bool = False, workspace: str = '', **given) -> str:
    workspace = workspace or None
    found = filters(**given)
    if action == 'board':
        return board_text(tasks.board(workspace=workspace, **found), found.get('project'))
    if action == 'list':
        return list_text(tasks.listing(archived=archived, workspace=workspace, **found))
    if not task:
        raise ValueError(f'Name the task: task="T-…"')
    if action == 'get':
        return detail(tasks.get(task, workspace=workspace))
    if action == 'history':
        return history_text(tasks.history(task, workspace=workspace))
    raise ValueError(f'Unknown action {action!r}')


def create(*, title: str, project: str, description: str = '', status: str = '', priority: str = '',
           acceptance=None, tags=None, assignees=None, feature: str = '', blockers=None, actor: dict,
           workspace: str = '') -> str:
    made = tasks.create(title, project=project, description=description or '',
                        status=status_ref(status) if status else 'backlog', priority=priority or 'P2',
                        acceptance=acceptance or (), tags=tags or (), assignees=assignees or (),
                        feature=feature or None, blockers=blockers or (), actor=actor, workspace=workspace or None)
    return f"Created {made['id']}.\n{detail(made)}"


def change(action: str, task: str, *, actor: dict, workspace: str = '', title=None, description=None,
           project=None, status='', priority=None, position=None, acceptance=None, feature=None,
           add=None, remove=None, text='', token='', branch='', commit='', files=None, next_step='') -> str:
    """Every task_edit action but create, on one existing Task."""
    if not task:
        raise ValueError('Name the task: task="T-…"')
    where = {'actor': actor, 'workspace': workspace or None}
    if action == 'claim':
        claimed = tasks.claim(task, actor=actor, token=token or None, workspace=workspace or None)
        state = claimed['task']
        if not claimed['token']:
            return f"You already hold {state['id']}.\n{detail(state)}"
        return (f"Claimed {state['id']}. Your claim token: {claimed['token']}\n"
                'Keep the token in this conversation, not on the Board; if your session restarts, take the '
                f'claim back with task_edit(action="claim", task="{state["id"]}", token="…"). Record a '
                'checkpoint at each step and at least every 30 minutes.\n' + detail(state))
    if action == 'checkpoint':
        state = tasks.checkpoint(task, text, branch=branch or None, commit=commit or None, files=files or (),
                                 next_step=next_step or None, **where)
    elif action == 'release':
        state = tasks.release(task, status=status_ref(status) if status else None, **where)
    elif action == 'edit':
        fields = {k: v for k, v in {'title': title, 'description': description, 'project': project,
                                    'priority': priority, 'acceptance': acceptance, 'position': position}.items()
                  if v is not None}
        state = tasks.edit(task, **fields, **where)
    elif action == 'append':
        state = tasks.append(task, text, **where)
    elif action == 'move':
        if not status:
            raise ValueError('Name the status to move to')
        state = tasks.move(task, status_ref(status), position=position, **where)
    elif action == 'cancel':
        state = tasks.move(task, 'cancelled', **where)
    elif action == 'tag':
        state = tasks.tag(task, add=add or (), remove=remove or (), **where)
    elif action == 'assign':
        state = tasks.assign(task, add=add or (), remove=remove or (), **where)
    elif action == 'link':
        links = {'add_blockers': add or (), 'remove_blockers': remove or ()}
        if feature is not None:
            links['feature'] = feature
        state = tasks.link(task, **links, **where)
    elif action == 'block':
        state = tasks.block(task, text, **where)
    elif action == 'unblock':
        state = tasks.unblock(task, **where)
    else:
        raise ValueError(f'Unknown action {action!r}')
    return detail(state)
