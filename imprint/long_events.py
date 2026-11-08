"""Long tool events in learned conversations (ADR 0162).

A tool output or tool call longer than the largest ordinary chunk
(``chunker.hard_max``) is one search chunk instead of a run of overlapping
ones, and its vector is computed from a reduced view of it: the event header,
the opening lines, lines that report errors or failures, and the closing
lines, with runs of same-shaped lines collapsed. The chunk's content stays
the exact span of the redacted conversation snapshot, so keyword search,
excerpts, ``get_memory`` and source reconstruction see all of it; only the
embedding input is reduced.
"""
from __future__ import annotations

import json
import re

from . import config

KINDS = ("tool_output", "tool_call")
# A longer event is split into pieces of at most this many characters; only
# the first piece starts with the header and is embedded from the view.
PIECE_MAX = 262_144

_HEADER = re.compile(r'\n--- (\{[^\n]*\}) ---\n')
_SIGNAL = re.compile(
    r'error|exception|traceback|fail|fatal|panic|warn|denied|refused|not found|no such|cannot|can\'t|'
    r'could not|unable|invalid|undefined|missing|timeout|timed out|abort|assert|exit code|exit status|'
    r'killed|segfault|\bE\d{3,}\b', re.IGNORECASE)
_HEXISH = re.compile(r'\b(?:0x)?[0-9a-f]{8,}\b', re.IGNORECASE)
_DIGITS = re.compile(r'\d+')


def _threshold() -> int:
    return max(1, config.CHUNK_HARD_MAX)


def chunk_spans(text: str, events: list[dict]) -> list[tuple[int, int]]:
    """Spans covering the whole snapshot: each long tool event (header
    through its closing newline, as ``conversation_source.prepare`` records
    it) on its own, and ordinary chunks between them."""
    from .source_memory import spans
    long = sorted((e['start'], e['end']) for e in events
                  if e.get('kind') in KINDS and e['end'] - e['start'] > _threshold())
    out, position = [], 0
    for start, end in long:
        if start < position:
            continue
        out.extend((position + a, position + b) for a, b in spans(text[position:start]))
        piece = start
        while piece < end:
            stop = min(end, piece + PIECE_MAX)
            if stop < end:
                cut = text.rfind('\n', piece + PIECE_MAX // 2, stop)
                stop = cut + 1 if cut >= 0 else stop
            out.append((piece, stop))
            piece = stop
        position = end
    out.extend((position + a, position + b) for a, b in spans(text[position:]))
    return out


def _shape(line: str) -> str:
    return _DIGITS.sub('0', _HEXISH.sub('h', line.strip().lower()))


def _collapse(lines: list[str]) -> list[str]:
    """Runs of three or more lines of one shape keep their first line and a count."""
    out, i = [], 0
    while i < len(lines):
        j = i + 1
        shape = _shape(lines[i])
        while j < len(lines) and _shape(lines[j]) == shape:
            j += 1
        out.append(lines[i])
        if j - i == 2:
            out.append(lines[i + 1])
        elif j - i > 2:
            out.append(f'[… {j - i - 1} similar lines …]')
        i = j
    return out


def reduce(body: str, budget: int) -> str:
    """Head, signal lines and tail of ``body`` within about ``budget`` characters."""
    lines = _collapse(body.split('\n'))
    if sum(len(line) + 1 for line in lines) <= budget:
        return '\n'.join(lines)
    head_budget, tail_budget = budget * 2 // 5, budget // 5
    head, used = 0, 0
    while head < len(lines) and used + len(lines[head]) + 1 <= head_budget:
        used += len(lines[head]) + 1
        head += 1
    if len(lines) == 1:  # One huge line (minified JSON): its start and its end.
        return lines[0][:head_budget] + ' … ' + lines[0][-tail_budget:]
    if head:
        out = lines[:head]
    else:  # The first line alone is longer than the head's share.
        out, head = [lines[0][:head_budget] + ' …'], 1
    tail, used = len(lines), 0
    while tail > head and used + len(lines[tail - 1]) + 1 <= tail_budget:
        used += len(lines[tail - 1]) + 1
        tail -= 1
    closing = lines[tail:]
    if not closing and tail > head:  # The last line alone is longer than the tail's share.
        closing, tail = ['… ' + lines[-1][-tail_budget:]], len(lines) - 1
    room = budget - head_budget - tail_budget
    picked, seen = [], set()
    for index in range(head, tail):
        line = lines[index]
        if _SIGNAL.search(line) and _shape(line) not in seen and len(line) + 1 <= room:
            seen.add(_shape(line))
            picked.append(index)
            room -= len(line) + 1
    previous = head - 1
    for index in picked + [tail]:
        if index - previous > 1:
            out.append(f'[… {index - previous - 1} lines omitted …]')
        if index < tail:
            out.append(lines[index])
        previous = index
    out.extend(closing)
    return '\n'.join(out)


def embedding_text(text: str) -> str:
    """What a chunk's vector is computed from: the reduced view for a long
    tool event chunk, the text itself otherwise. Ordinary chunks never
    exceed ``chunker.hard_max``, so only these chunks change."""
    if len(text) <= _threshold():
        return text
    match = _HEADER.match(text)
    if not match:
        return text
    try:
        kind = json.loads(match.group(1)).get('kind')
    except ValueError:
        return text
    if kind not in KINDS:
        return text
    header = match.group(0).strip('\n') + '\n'
    return header + reduce(text[match.end():], max(1000, config.CHUNK_SIZE_PROSE - len(header)))
