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


def actor(client: str | None = None) -> dict:
    """The agent session this MCP server serves, as the actor of its changes."""
    from . import agent_activity
    current = agent_activity.current()
    session = current.get('session') or f'process-{os.getpid()}'
    return tasks.agent(session, client=client or current.get('client'))


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


def line(state: dict) -> str:
    """One Task on one line: id, priority, title, project, tags, assignees."""
    text = f"{state['id']} {state['priority']} {state['title']} · {state['project']}"
    if state.get('tags'):
        text += ' ' + ' '.join(f'#{tag}' for tag in state['tags'])
    if state.get('assignees'):
        text += ' → ' + ', '.join(state['assignees'])
    return text


def detail(state: dict) -> str:
    lines = [f"{state['id']} · {state['title']}",
             f"Status: {STATUS_LABELS[state['status']]} · Priority: {state['priority']} · Project: {state['project']}"]
    if state.get('tags'):
        lines.append('Tags: ' + ', '.join(state['tags']))
    if state.get('assignees'):
        lines.append('Assignees: ' + ', '.join(state['assignees']))
    lines.append(f"Created by {_who(state['created_by'])}, {_when(state['created_at'])}")
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


# ── The tools ────────────────────────────────────────────────────

def read(action: str, *, task: str = '', project: str = '', status: str = '', workspace: str = '') -> str:
    workspace = workspace or None
    if action == 'board':
        return board_text(tasks.board(project=project or None, workspace=workspace), project or None)
    if action == 'list':
        return list_text(tasks.listing(project=project or None, status=status_ref(status) if status else None,
                                       workspace=workspace))
    if action == 'get':
        if not task:
            raise ValueError('Name the task to get: task="T-…"')
        return detail(tasks.get(task, workspace=workspace))
    raise ValueError(f'Unknown action {action!r}')


def create(*, title: str, project: str, description: str = '', status: str = '', priority: str = '',
           acceptance=None, tags=None, assignees=None, actor: dict, workspace: str = '') -> str:
    made = tasks.create(title, project=project, description=description,
                        status=status_ref(status) if status else 'backlog', priority=priority or 'P2',
                        acceptance=acceptance or (), tags=tags or (), assignees=assignees or (),
                        actor=actor, workspace=workspace or None)
    return f"Created {made['id']}.\n{detail(made)}"
