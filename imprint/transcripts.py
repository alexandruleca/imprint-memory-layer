"""Coding-client transcripts: Claude Code sessions and Codex rollouts.

Both clients append one JSON record per line. Learning reads three things from
them: user/assistant text, the working directories the client recorded and the
session identity. Transcript content is never rewritten.

Stdlib only: ``workspace_policy`` reads recorded cwds through it, including
for the WSL bridge's Stop check.
"""
import json

# Codex keeps record data under "payload". The session's working directory is
# in its session_meta record and each turn's in a turn_context record.
_CODEX_CONTEXT = ('session_meta', 'turn_context')
_TEXT_BLOCKS = ('text', 'input_text', 'output_text')


def records(path):
    """Each JSON object in a transcript; lines that are not one are skipped."""
    with open(path, encoding='utf-8') as transcript:
        for line in transcript:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record


def recorded_cwd(record):
    """The working directory a record states, if any."""
    if record.get('type') in _CODEX_CONTEXT:
        payload = record.get('payload')
        return payload.get('cwd') if isinstance(payload, dict) else None
    return record.get('cwd')


def codex_session(record):
    """A Codex rollout's session id, from its session_meta record."""
    payload = record.get('payload')
    if record.get('type') == 'session_meta' and isinstance(payload, dict):
        session = payload.get('id')
        return session if isinstance(session, str) and session else None
    return None


def message(record):
    """``(role, text)`` for a user or assistant message record, else None.

    Only text blocks count: tool calls, tool output and reasoning are not
    conversation text.
    """
    if record.get('type') == 'response_item':  # Codex
        item = record.get('payload')
        if not isinstance(item, dict) or item.get('type') != 'message':
            return None
        role, content = item.get('role'), item.get('content')
    else:  # Claude Code
        role, body = record.get('type'), record.get('message')
        content = body.get('content') if isinstance(body, dict) else None
    if role not in ('user', 'assistant'):
        return None
    if isinstance(content, str):
        return role, content
    if not isinstance(content, list):
        return None
    return role, '\n\n'.join(block['text'] for block in content
                             if isinstance(block, dict) and block.get('type') in _TEXT_BLOCKS
                             and isinstance(block.get('text'), str))
