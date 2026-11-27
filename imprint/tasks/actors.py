"""Who changes the Board: the user, or an agent session, on a device."""

from __future__ import annotations

import re


USER = {'kind': 'user'}


def _device() -> str:
    try:
        from ..device import current
        return current()['id']
    except (OSError, ValueError):
        return ''


def agent(session: str, name: str | None = None, client: str | None = None, device: str | None = None) -> dict:
    """The actor for a change an agent session makes."""
    actor = {'kind': 'agent', 'session': session, 'name': name, 'client': client}
    if device:
        actor['device'] = device
    return actor


def _actor(actor: dict | None) -> dict:
    actor = dict(actor or USER)
    if actor.get('kind') not in ('user', 'agent'):
        raise ValueError('actor must be the user or an agent')
    if actor['kind'] == 'agent' and not actor.get('session'):
        raise ValueError('an agent actor needs its session')
    actor.setdefault('device', _device())
    return actor


# Clients by the names their MCP handshake gives, as the client table names
# them: comments and assignees use the table's names ("codex", "cursor").
CLIENT_NAMES = {'claude-code': 'claude-code', 'codex-mcp-client': 'codex', 'cursor-vscode': 'cursor',
                'claude-ai': 'claude-desktop', 'opencode': 'opencode', 'cline': 'cline', 'openclaw': 'openclaw'}
_CLIENT_PREFIXES = ('codex', 'cursor', 'cline', 'copilot', 'opencode', 'openclaw')
# Every client name an address can mean, in the table's names.
CLIENTS = frozenset({*CLIENT_NAMES.values(), 'copilot', 'claude', 'chatgpt', 'gemini'})


def client_id(name: str | None) -> str | None:
    """A client as the Board knows it, from the name its handshake gave."""
    raw = (name or '').strip().lower()
    if not raw:
        return None
    if raw in CLIENT_NAMES:
        return CLIENT_NAMES[raw]
    known = next((prefix for prefix in _CLIENT_PREFIXES if raw.startswith(prefix)), None)
    return known or re.sub(r'[^a-z0-9-]+', '-', raw).strip('-')[:40] or None


def device_name() -> str:
    """This device as assignees address it (``@bedrock``): its host name in lower case."""
    try:
        from ..device import current
        return current()['name'].lower()
    except (OSError, ValueError):
        return ''


def label(holder: dict) -> str:
    """An agent as people read it: its name and client, ``grimforge (claude-code)``."""
    name, client = holder.get('name') or '', holder.get('client') or ''
    if name and client:
        return f'{name} ({client})'
    return name or client or holder.get('session') or 'an agent'


def _who(event: dict) -> dict:
    if event['actor'] == 'user':
        return {'kind': 'user', 'device': event['device']}
    return {'kind': 'agent', 'session': event['session'], 'name': event['agent'],
            'client': event['client'], 'device': event['device']}
