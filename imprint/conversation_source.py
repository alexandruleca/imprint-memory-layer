"""Readable transcript events, including evidence and available analysis.

Opaque/encrypted reasoning and binary attachments are counted, never decoded or
invented. Client-injected messages retain a context role rather than becoming
human questions. All text is redacted before offsets or chunks are derived.
"""
from __future__ import annotations

import json

from . import transcripts
from .source_memory import document


def _render(value, omissions):
    def readable(item):
        if isinstance(item, str) and item.startswith(('data:image/', 'data:audio/')):
            omissions['binary_attachments'] += 1
            return '[binary attachment omitted]'
        if isinstance(item, dict):
            if item.get('type') in ('image', 'input_image', 'image_url', 'audio', 'input_audio', 'output_audio'):
                omissions['binary_attachments'] += 1
                return '[binary attachment omitted]'
            result = {}
            for key, child in item.items():
                if key in ('encrypted_content', 'encrypted_reasoning', 'encryptedContent'):
                    omissions['opaque_reasoning'] += 1
                    result[key] = '[opaque reasoning unavailable]'
                else:
                    result[key] = readable(child)
            return result
        if isinstance(item, list):
            return [readable(child) for child in item]
        return item
    cleaned = readable(value)
    return cleaned if isinstance(cleaned, str) else json.dumps(cleaned, ensure_ascii=False, indent=2)


def prepare(path: str) -> dict:
    events, omissions = [], {'opaque_reasoning': 0, 'binary_attachments': 0, 'zero_filled_lines': 0}
    turn = 0
    render = lambda value: _render(value, omissions)

    def add(role, kind, text, row, **metadata):
        nonlocal turn
        if not isinstance(text, str) or not text:
            return
        if role == 'user' and kind == 'message':
            turn += 1
        meta = {'event': len(events), 'role': role, 'kind': kind, 'turn': turn}
        timestamp = row.get('timestamp') or row.get('ts')
        if timestamp is not None:
            meta['timestamp'] = timestamp
        for key in ('agentId', 'parentToolCallId'):
            if row.get(key):
                meta[key] = row[key]
        meta.update(metadata)
        events.append((meta, text))

    for row in transcripts.records(path, strict=True, omissions=omissions):
        payload = row.get('payload')
        if row.get('type') == 'tool.execution_complete':
            data = row.get('data') or {}
            add('tool', 'tool_output', render(data.get('result', data.get('output', ''))), row,
                call_id=str(data.get('toolCallId') or ''))
            continue
        if row.get('type') == 'response_item' and isinstance(payload, dict):
            kind = payload.get('type')
            if kind in ('function_call', 'custom_tool_call'):
                name = str(payload.get('name') or 'tool')
                add('assistant', 'tool_call', name + '\n' + render(payload.get('arguments', payload.get('input', ''))),
                    row, tool=name, call_id=str(payload.get('call_id') or ''))
                continue
            if kind in ('function_call_output', 'custom_tool_call_output'):
                add('tool', 'tool_output', render(payload.get('output', '')), row,
                    call_id=str(payload.get('call_id') or ''))
                continue
            if kind == 'reasoning':
                parts = [s.get('text', '') for s in payload.get('summary', []) if isinstance(s, dict)]
                parts += [s.get('text', '') for s in payload.get('content', []) if isinstance(s, dict)
                          and s.get('type') in ('reasoning_text', 'text')]
                text = '\n'.join(parts)
                if text:
                    add('assistant', 'analysis', text, row)
                else:
                    omissions['opaque_reasoning'] += 1
                continue
            if kind != 'message':
                continue
            role, body = payload.get('role'), payload
        elif row.get('type') in ('user.message', 'assistant.message'):
            role, body = row['type'].split('.')[0], row.get('data', {})
            if not transcripts._copilot_conversation(row, body):
                role = 'context'
        else:
            body = row.get('message', row)
            role = row.get('role') or row.get('type')
        if not isinstance(body, dict) or role not in ('user', 'assistant', 'system', 'developer', 'tool', 'context'):
            continue
        if transcripts._claude_injected(row) or transcripts._cline_hidden(row):
            role = 'context'
        content = body.get('content', '')
        blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) else content
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get('type')
            if kind in ('text', 'input_text', 'output_text'):
                text = block.get('text', '')
                if not isinstance(text, str):
                    continue
                if role == 'user' and 'message' in row and 'role' in row:
                    typed = transcripts._cursor_query(text)
                    if typed != text:
                        context = transcripts._CURSOR_QUERY.sub('', text)
                        add('context', 'client_context', context, row)
                    text = typed
                elif role == 'user' and str(path).endswith('.messages.json'):
                    typed = transcripts._cline_input(text)
                    if not typed and text:
                        add('context', 'client_context', text, row)
                        continue
                    text = typed
                phase = body.get('channel') or body.get('phase') or row.get('channel')
                add(role, 'analysis' if role == 'assistant' and phase in ('analysis', 'reasoning') else 'message', text, row)
            elif kind in ('thinking', 'reasoning', 'reasoning_text', 'summary_text'):
                text = block.get('thinking') or block.get('text') or block.get('summary')
                if isinstance(text, str) and text:
                    add('assistant', 'analysis', text, row)
                else:
                    omissions['opaque_reasoning'] += 1
            elif kind in ('tool_use', 'function_call'):
                name = str(block.get('name') or 'tool')
                add('assistant', 'tool_call', name + '\n' + render(block.get('input', block.get('arguments', ''))),
                    row, tool=name, call_id=str(block.get('id') or ''))
            elif kind in ('tool_result', 'tool_output'):
                add('tool', 'tool_output', render(block.get('content', block.get('output', ''))), row,
                    call_id=str(block.get('tool_use_id') or ''))
            elif kind == 'file' and isinstance(block.get('content'), str):
                add('context', 'attachment', str(block.get('path') or '') + '\n' + block['content'], row)
            elif kind in ('image', 'input_image', 'image_url', 'audio', 'redacted_thinking'):
                omissions['opaque_reasoning' if kind == 'redacted_thinking' else 'binary_attachments'] += 1
        # Copilot exposes these outside its text field.
        if isinstance(body.get('reasoningText'), str):
            add('assistant', 'analysis', body['reasoningText'], row)
        for call in body.get('toolRequests', []) or []:
            if isinstance(call, dict):
                name = str(call.get('name') or 'tool')
                add('assistant', 'tool_call', name + '\n' + render(call.get('arguments', {})), row,
                    tool=name, call_id=str(call.get('toolCallId') or ''))
    parts = ['Imprint conversation — available readable events\n']
    event_meta = []
    offset = len(parts[0])
    for meta, text in events:
        header = '\n--- ' + json.dumps(meta, ensure_ascii=False, separators=(',', ':')) + ' ---\n'
        start = offset
        parts.extend((header, text, '\n'))
        offset += len(header) + len(text) + 1
        event_meta.append({**meta, 'start': start, 'end': offset})
    canonical = ''.join(parts)
    result = document(canonical, kind='conversation', metadata={
        'event_count': len(events), 'omissions': omissions,
        'raw_transcript_exact': False, 'available_readable_events': True,
    })
    from .source_memory import redaction_edits, remap
    edits = redaction_edits(result['text'], canonical)
    result['events'] = [{**meta, 'start': remap(meta['start'], edits),
                         'end': remap(meta['end'], edits, right=True)} for meta in event_meta]
    return result
