"""A running agent's whole conversation, from its client's transcript on this
device (ADR 0244).

The Agents page opens a side panel on one session and shows its trail: the
person's prompts and the agent's replies in full, each tool call as a row
with its result, and what the client put into the conversation itself
(context, other sessions' messages). Thinking is left out. Everything is
redacted and read only while the panel asks for it: first the end of the
transcript, then earlier parts on request, and while the panel is open only
the bytes written since its last read. A line the client is still writing
waits for the next read.

Claude Code writes one JSON record per message or content block; Codex
writes ``response_item`` records for messages, tool calls and their output.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from . import transcripts
from .live_transcripts import classify
from .redaction import redact

TAIL_BYTES = 2 * 1024 * 1024   # the first read takes this much from the end
READ_BYTES = 4 * 1024 * 1024   # any read takes at most this much
TEXT_CHARS = 20_000            # a prompt or reply shown in full up to this
TOOL_CHARS = 2_000             # a tool call's input or result, shortened
# What Codex puts into a conversation as user messages, not typed by the person.
_CODEX_CONTEXT = ('<environment_context>', '<user_instructions>', '<turn_aborted>', '# AGENTS.md', '<permissions')


def _at(record: dict) -> float | None:
    stamp = record.get('timestamp')
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def _text(value, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = redact(text)
    return text if len(text) <= limit else text[:limit] + f'… ({len(text) - limit} more characters)'


def _result(content) -> str:
    """A tool result's readable text: its text blocks, else its JSON."""
    if isinstance(content, list):
        parts = [b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text']
        if parts:
            return '\n'.join(parts)
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)


def _tool(call: str, name: str, payload, at, offset: int) -> dict:
    action = classify(name, payload)
    return {'kind': 'tool', 'id': f'{offset}:{call}', 'call': call, 'tool': name, 'label': action['label'],
            'detail': action['detail'], 'input': _text(payload, TOOL_CHARS), 'at': at}


def _claude(record: dict, at, offset: int) -> list[dict]:
    kind = record.get('type')
    message = record.get('message') if isinstance(record.get('message'), dict) else {}
    content = message.get('content')
    found = []
    if kind == 'assistant' and isinstance(content, list):
        for n, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'text' and str(block.get('text') or '').strip():
                found.append({'kind': 'reply', 'id': f'{offset}:{n}', 'text': _text(block['text'], TEXT_CHARS), 'at': at})
            elif block.get('type') == 'tool_use' and block.get('id'):
                found.append(_tool(block['id'], block.get('name') or '', block.get('input'), at, offset))
    elif kind == 'user':
        blocks = content if isinstance(content, list) else []
        results = [b for b in blocks if isinstance(b, dict) and b.get('type') == 'tool_result']
        for block in results:
            found.append({'kind': 'result', 'id': f"{offset}:{block.get('tool_use_id')}", 'call': block.get('tool_use_id'),
                          'text': _text(_result(block.get('content')), TOOL_CHARS), 'error': bool(block.get('is_error')),
                          'at': at})
        if not results:
            said = transcripts.message(record)
            if said is not None and said[1].strip():
                found.append({'kind': 'prompt', 'id': f'{offset}:p', 'text': _text(said[1], TEXT_CHARS), 'at': at})
            elif content and not record.get('isCompactSummary'):
                text = content if isinstance(content, str) else '\n'.join(
                    b.get('text', '') for b in blocks if isinstance(b, dict) and b.get('type') == 'text')
                if text.strip():
                    origin = record.get('origin') if isinstance(record.get('origin'), dict) else {}
                    found.append({'kind': 'context', 'id': f'{offset}:c', 'text': _text(text, TOOL_CHARS),
                                  'origin': origin.get('kind') or ('meta' if record.get('isMeta') else None), 'at': at})
    return found


def _codex(record: dict, at, offset: int) -> list[dict]:
    if record.get('type') != 'response_item':
        return []
    item = record.get('payload') if isinstance(record.get('payload'), dict) else {}
    sub = item.get('type')
    if sub == 'message':
        role = item.get('role')
        text = '\n\n'.join(b.get('text', '') for b in item.get('content') or []
                           if isinstance(b, dict) and isinstance(b.get('text'), str))
        if not text.strip() or role not in ('user', 'assistant'):
            return []
        if role == 'user' and text.lstrip().startswith(_CODEX_CONTEXT):
            return [{'kind': 'context', 'id': f'{offset}:c', 'text': _text(text, TOOL_CHARS), 'origin': 'client', 'at': at}]
        return [{'kind': 'prompt' if role == 'user' else 'reply', 'id': f'{offset}:m', 'text': _text(text, TEXT_CHARS),
                 'at': at}]
    if sub in ('function_call', 'custom_tool_call', 'local_shell_call') and item.get('call_id'):
        body = item.get('arguments') if sub == 'function_call' else item.get('input')
        if sub == 'local_shell_call':
            body = item.get('action') or {}
        if isinstance(body, str) and sub == 'function_call':
            try:
                body = json.loads(body)
            except ValueError:
                pass
        return [_tool(item['call_id'], item.get('name') or sub, body, at, offset)]
    if sub in ('function_call_output', 'custom_tool_call_output', 'local_shell_call_output') and item.get('call_id'):
        output = item.get('output')
        if isinstance(output, dict):
            output = output.get('content', output)
        return [{'kind': 'result', 'id': f"{offset}:{item['call_id']}", 'call': item['call_id'],
                 'text': _text(_result(output), TOOL_CHARS), 'error': False, 'at': at}]
    return []


def read(path: str, client: str, *, after: int | None = None, before: int | None = None) -> dict:
    """Entries of the transcript at ``path`` (``client`` is ``claude`` or
    ``codex``): from byte ``after`` to the last complete line (the panel's
    poll), else the part that ends at ``before`` (earlier parts), else the
    end of the file. ``start`` and ``next`` are the byte offsets the
    entries span; ``more_before`` says whether earlier parts remain."""
    size = os.path.getsize(path)
    if after is not None:
        start = min(max(0, after), size)
        end = min(size, start + READ_BYTES)
    else:
        end = size if before is None else min(max(0, before), size)
        start = max(0, end - (TAIL_BYTES if before is None else READ_BYTES))
    with open(path, 'rb') as handle:
        if start and after is None:  # Begin on a line boundary.
            handle.seek(start - 1)
            if handle.read(1) != b'\n':
                handle.readline()
            start = handle.tell()
        handle.seek(start)
        data = handle.read(max(0, end - start))
    if after is not None:
        cut = data.rfind(b'\n')
        data = data[:cut + 1] if cut >= 0 else b''  # A line still being written waits.
    entries, offset = [], start
    fold = _codex if client == 'codex' else _claude
    for line in data.split(b'\n'):
        if line.strip():
            try:
                record = json.loads(line)
            except ValueError:
                record = None
            if isinstance(record, dict):
                entries.extend(fold(record, _at(record), offset))
        offset += len(line) + 1
    return {'entries': entries, 'start': start, 'next': start + len(data), 'size': size, 'more_before': start > 0,
            'raw': data}


def seen(raw: bytes, messages: list[dict]) -> list[str]:
    """The delivered messages whose text the transcript part ``raw`` shows,
    as the agent saw it in a tool result, a hook's context or its inbox."""
    found = []
    for message in messages:
        if message.get('state') != 'delivered' or message.get('seen_at'):
            continue
        snippet = message['text'].strip().splitlines()[0][:60] if message['text'].strip() else ''
        if snippet and json.dumps(snippet, ensure_ascii=False)[1:-1].encode('utf-8') in raw:
            found.append(message['id'])
    return found
