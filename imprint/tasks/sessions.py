"""Agent sessions as the Board names them (ADR 0210): ``conversation:<id>``
when the client says which conversation it serves (Claude Code's session
id), else ``process:<id>`` for the Imprint server process that serves it."""

from __future__ import annotations

CONVERSATION = 'conversation:'
PROCESS = 'process:'


def conversation(conversation_id: str) -> str:
    return f'{CONVERSATION}{conversation_id}'


def process(process_id: object) -> str:
    return f'{PROCESS}{process_id}'


def conversation_id(session: str | None) -> str | None:
    """The client's conversation id a session names, or None for a process."""
    return session[len(CONVERSATION):] if session and session.startswith(CONVERSATION) else None
