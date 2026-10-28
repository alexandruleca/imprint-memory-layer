"""Lossless redacted source text and contiguous character-span search chunks."""
from __future__ import annotations

import hashlib
import json

from . import config
from .redaction import redact_all

FORMAT_VERSION = 1
SPAN_FIELDS = ['source_version', 'source_start', 'source_end', 'source_prefix', 'source_chunk_count',
               'source_content_hash', 'source_format', 'source_profile', 'event_roles', 'event_kinds']


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def spans(text: str, *, code: bool = False, prefix: str = '') -> list[tuple[int, int]]:
    """Cover the complete text without stripping indentation or short tails.

    Prefer paragraph/line boundaries, with bounded overlap. Source offsets are
    Unicode character offsets, the same unit as MCP pagination. No model runs
    before indexing. The canonical snapshot retains exact UTF-8 line endings.
    """
    if not text:
        return []
    target = config.CHUNK_SIZE_CODE if code else config.CHUNK_SIZE_PROSE
    size = max(1, min(max(1, target), max(1, config.CHUNK_HARD_MAX - len(prefix))))
    overlap = max(0, min(config.CHUNK_OVERLAP, size // 3))
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            for sep in ('\n\n', '\n'):
                cut = text.rfind(sep, start + size // 2, end)
                if cut >= 0:
                    end = cut + len(sep)
                    break
        if len(out) >= 100000:
            raise ValueError('Source needs more than 100000 chunks; increase the chunk size')
        out.append((start, end))
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
    return out


def document(text: str, *, kind: str = 'text', metadata: dict | None = None) -> dict:
    """Redact the whole source before either snapshots or chunks can see it."""
    text = redact_all([text])[0]
    return {'text': text, 'kind': kind, 'metadata': metadata or {}, 'format': FORMAT_VERSION}


def attach(doc: dict, records: list[dict], *, prefix: str = '', base: int = 0, code: bool = False) -> list[dict]:
    """Associate existing ordered source slices with their canonical document.

    Prefixes are derived retrieval context, not source bytes. This also accepts
    unprefixed chunks used by callers that already supply their own context.
    """
    preferred = spans(doc['text'], code=code, prefix=prefix)
    exact = len(preferred) == len(records) and all(
        row['content'] == prefix + doc['text'][a:b] for row, (a, b) in zip(records, preferred))
    previous = -1
    for index, record in enumerate(records):
        content = record['content']
        actual_prefix = prefix if content.startswith(prefix) else ''
        body = content[len(actual_prefix):]
        # Start positions strictly advance, so repeated identical chunks retain
        # their own positions rather than all resolving to the first occurrence.
        start = preferred[index][0] if exact else doc['text'].find(body, max(base, previous + 1))
        if start < 0:
            raise ValueError('Chunk text is not a contiguous slice of the redacted source')
        previous = start
        record.update(source_start=start, source_end=start + len(body), source_prefix=actual_prefix,
                      source_format=FORMAT_VERSION)
    if records:
        records[0]['_source_document'] = doc
    return records


def records(doc: dict, *, prefix: str = '', code: bool = False) -> list[dict]:
    return [{'content': prefix + doc['text'][start:end], 'chunk_index': i,
             'source_start': start, 'source_end': end, 'source_prefix': prefix,
             'source_format': FORMAT_VERSION}
            for i, (start, end) in enumerate(spans(doc['text'], code=code, prefix=prefix))]


def reconstruct(chunks: list[dict], total_chars: int) -> str:
    """Reassemble source slices, rejecting gaps, wrong lengths and conflicts."""
    if total_chars == 0:
        if chunks:
            raise ValueError('Unexpected chunks for an empty source')
        return ''
    out, end = '', 0
    for row in sorted(chunks, key=lambda r: (r['source_start'], r['source_end'])):
        start, stop = row['source_start'], row['source_end']
        prefix = row.get('source_prefix', '')
        content = row['content']
        if not content.startswith(prefix):
            raise ValueError('Source prefix mismatch')
        body = content[len(prefix):]
        if not 0 <= start <= stop <= total_chars or len(body) != stop - start:
            raise ValueError('Invalid source span')
        if start > end:
            raise ValueError(f'Missing source characters {end}:{start}')
        overlap = min(end, stop) - start
        if overlap > 0 and out[start:start + overlap] != body[:overlap]:
            raise ValueError('Conflicting source overlap')
        if stop > end:
            out += body[max(0, end - start):]
            end = stop
    if end != total_chars:
        raise ValueError(f'Missing source characters {end}:{total_chars}')
    return out


def redaction_edits(newer: str, older: str) -> list[tuple[int, int, str]]:
    """Map length-labelled redaction placeholders back to source coordinates."""
    if newer == older:
        return []
    import re
    marks = list(re.finditer(r'\[REDACTED:[^:\]]+:(\d+)\]', newer))
    literals = [newer[a:b] for a, b in zip([0] + [m.end() for m in marks], [m.start() for m in marks] + [len(newer)])]
    if not marks or not older.startswith(literals[0]):
        raise ValueError('Source text changed outside redaction placeholders')
    states = {len(literals[0]): []}
    for mark, literal in zip(marks, literals[1:]):
        following = {}
        for position, edits in states.items():
            same_end = position + len(mark.group())
            raw_end = position + int(mark.group(1))
            if older.startswith(mark.group(), position) and older.startswith(literal, same_end):
                following[same_end + len(literal)] = edits
            if older.startswith(literal, raw_end):
                following.setdefault(raw_end + len(literal), edits + [(position, raw_end, mark.group())])
        states = following
    if len(older) not in states:
        raise ValueError('Could not align source redaction')
    return states[len(older)]


def remap(position: int, edits: list[tuple[int, int, str]], *, right=False) -> int:
    delta = 0
    for start, end, replacement in edits:
        if position <= start:
            break
        if position < end:
            return start + delta + (len(replacement) if right else 0)
        delta += len(replacement) - (end - start)
    return position + delta


def tighten(doc: dict, rows: list[dict]) -> tuple[dict, list[dict]]:
    """Carry a stored stricter redaction into the full source, including overlap.

    A looser device/configuration must not restore a credential into a snapshot
    that its already-redacted chunks had removed (ADR 0060).
    """
    edits = []
    text = doc['text']
    for row in rows:
        prefix = row.get('source_prefix', '')
        content = row['content']
        if not content.startswith(prefix):
            raise ValueError('Source prefix changed during redaction')
        start, end = row['source_start'], row['source_end']
        for a, b, replacement in redaction_edits(content[len(prefix):], text[start:end]):
            edits.append((start + a, start + b, replacement))
    if not edits:
        return doc, rows
    # Identical overlapping copies describe one edit. Conflicting overlaps are
    # conservatively redacted as a union, never filled from an unredacted copy.
    merged = []
    for edit in sorted(set(edits)):
        a, b, replacement = edit
        if merged and a < merged[-1][1]:
            x, y, previous = merged.pop()
            merged.append((x, max(y, b), previous if (x, y, previous) == edit else f'[REDACTED:secret:{max(y, b)-x}]'))
        else:
            merged.append(edit)
    updated = text
    for a, b, replacement in reversed(merged):
        updated = updated[:a] + replacement + updated[b:]
    result = []
    for row in rows:
        a = remap(row['source_start'], merged)
        b = remap(row['source_end'], merged, right=True)
        result.append({**row, 'source_start': a, 'source_end': b,
                       'content': row.get('source_prefix', '') + updated[a:b]})
    return {**doc, 'text': updated}, result


def extracted_documents(items):
    """One canonical extracted-text source, retaining each document's range."""
    if items and all(item.metadata.get('audio') for item in items):
        return audio_documents(items)
    raw, boundaries = '', [0]
    for item in items:
        raw += ('\n\n' if raw else '') + item.text
        boundaries.append(len(raw))
    doc = document(raw, kind='extracted', metadata={'parts': []})
    edits = redaction_edits(doc['text'], raw)
    boundaries = [remap(value, edits) for value in boundaries]
    segments = []
    for item, start, end in zip(items, boundaries, boundaries[1:]):
        segments.append((start, doc['text'][start:end]))
        doc['metadata']['parts'].append({'start': start, 'end': end, 'metadata': item.metadata or {}})
    return doc, segments


def audio_documents(items):
    """ASR is a conversation with unknown speakers, never invented chat roles."""
    parts = ['Imprint audio transcript — machine transcription; speakers unidentified\n']
    boundaries = [0]
    events = []
    offset = len(parts[0])
    for index, item in enumerate(items):
        meta = item.metadata
        event = {'event': index, 'role': 'speaker', 'kind': 'message', 'turn': index + 1,
                 'audio_start': meta['audio_start'], 'audio_end': meta['audio_end'],
                 'language': meta.get('language', ''), 'speaker': None}
        header = '\n--- ' + json.dumps(event, ensure_ascii=False, separators=(',', ':')) + ' ---\n'
        start = offset
        parts.extend((header, item.text, '\n'))
        offset += len(header) + len(item.text) + 1
        boundaries.append(offset)
        events.append({**event, 'start': start, 'end': offset})
    raw = ''.join(parts)
    first = items[0].metadata
    doc = document(raw, kind='conversation', metadata={
        'media': 'audio', 'audio_profile': first['audio_profile'], 'title': first.get('title', ''),
        'transcription_model': first.get('transcription_model', ''), 'model_revision': first.get('model_revision', ''),
        'event_count': len(events), 'speaker_diarization': False, 'raw_transcript_exact': False,
    })
    edits = redaction_edits(doc['text'], raw)
    doc['events'] = [{**event, 'start': remap(event['start'], edits),
                      'end': remap(event['end'], edits, right=True)} for event in events]
    boundaries = [remap(value, edits) for value in boundaries]
    return doc, [(start, doc['text'][start:end]) for start, end in zip(boundaries, boundaries[1:])]


def profile() -> str:
    from .config_schema import resolve
    return digest(json.dumps([FORMAT_VERSION, config.CHUNK_SIZE_CODE, config.CHUNK_SIZE_PROSE,
                              config.CHUNK_HARD_MAX, config.CHUNK_OVERLAP,
                              bool(resolve('redact.privacy_filter')[0])], separators=(',', ':')))
