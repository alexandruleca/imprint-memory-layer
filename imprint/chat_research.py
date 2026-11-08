"""Reply-local research budgets and evidence-novelty heuristics (ADR 0102),
and the notes the final answer is written from (ADR 0127).

Novelty is a stopping hint, not a relevance or truth classifier. Resource tokens
count the work a request does: the prompt tokens past the prefix the previous
request already processed, plus the output it generated. A request must fit
its whole output allowance before it starts; what it leaves unused is refunded
(ADR 0157). They are estimates, not billing usage.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass


class ResearchExhausted(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class ResearchBudget:
    def __init__(self, *, seconds, tokens, patience, clock=None):
        self.clock = clock or time.monotonic
        self.deadline = self.clock() + seconds
        self.tokens = tokens
        self.reserved = 0
        self.patience = patience
        self.stale = 0
        self.rounds = 0
        self.seen = set()

    def remaining(self):
        return max(0., self.deadline - self.clock())

    def check_time(self):
        if not self.remaining():
            raise ResearchExhausted('time')

    def extend(self, seconds):
        """Time the reply spent waiting for the user's answer is not research
        time: the deadline moves by it (ADR 0159)."""
        self.deadline += max(0., seconds)

    def reason(self):
        if not self.remaining():
            return 'time'
        if self.reserved >= self.tokens:
            return 'tokens'
        if self.stale >= self.patience:
            return 'no_progress'
        return None

    def reserve(self, prompt, output, cached=0):
        """Charge a request before it starts: its prompt past the ``cached``
        prefix the model already holds, plus its whole output allowance."""
        self.check_time()
        if prompt < 0 or output < 1:
            raise ValueError('Research requests need a nonnegative prompt and positive output allowance')
        charge = max(0, prompt - max(0, cached)) + output
        if self.reserved + charge > self.tokens:
            raise ResearchExhausted('tokens')
        self.reserved += charge
        return charge

    def settle(self, allowance, used):
        """Refund the output allowance a finished request did not use."""
        self.reserved -= max(0, allowance - max(0, used))

    def observe(self, results):
        """A full completed round: results are (text, writes) pairs.

        Track evidence across query/tool changes and nonconsecutive cycles.
        Writes never count toward the no-progress heuristic. Failed reads and
        empty results cannot establish novelty just by changing error text.
        """
        self.rounds += 1
        progress = False
        for text, writes in results:
            if writes:
                progress = True
                continue
            keys = _evidence_keys(text)
            if keys - self.seen:
                progress = True
            # Keep tracking bounded even for many large source reads.
            if len(self.seen) < 65536:
                self.seen.update(sorted(keys - self.seen)[:65536 - len(self.seen)])
        self.stale = 0 if progress else self.stale + 1
        return progress


_VOLATILE = {'timestamp', 'updated_at', 'recorded_at', 'similarity', 'score', 'elapsed', 'duration', 'next_start', 'offset'}
_EMPTY = re.compile(r'^(?:\[tool error\]|\[research stopped\]|\[repeated call\]|no (?:results|memories|sources|facts|matches|indexed)|not found|error:)', re.I)
_RANK = re.compile(r'^\s*\[\d+\]\s*')
_SCORE = re.compile(r'\(?\b(?:semantic similarity|similarity|sim|score|elapsed|duration|timestamp|updated_at)\s*[:=]\s*[^|\n)]+\)?', re.I)


def _evidence_keys(text):
    if not text.strip() or _EMPTY.match(text.lstrip()):
        return set()
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        value = None
    if isinstance(value, (dict, list)):
        if isinstance(value, dict) and value.get('error'):
            return set()
        def clean(item):
            if isinstance(item, dict):
                return {key: clean(child) for key, child in item.items() if key not in _VOLATILE}
            if isinstance(item, list):
                # Result ranking/reordering is not new evidence.
                return sorted((clean(child) for child in item), key=lambda child: json.dumps(child, sort_keys=True))
            return item
        text = json.dumps(clean(value), sort_keys=True, ensure_ascii=False)
    keys = set()
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > 128:
        lines = [lines[index * (len(lines) - 1) // 127] for index in range(128)]
    samples_per_line = 4096 // max(1, len(lines))
    for line in lines:
        line = _SCORE.sub('', _RANK.sub('', line)).strip()
        words = re.findall(r'\w+', line.casefold())
        if not words:
            continue
        width = min(12, len(words))
        positions = len(words) - width + 1
        # Sample across the whole source and each selected line, including
        # its tail, rather than exhausting the cap on the first paragraphs.
        samples = min(samples_per_line, positions)
        for sample in range(samples):
            start = sample * (positions - 1) // max(1, samples - 1)
            keys.add(hashlib.sha256(' '.join(words[start:start + width]).encode()).digest()[:16])
    return keys


# ── Research notes for the final answer (ADR 0127) ──────────────
# Tool results carry provenance for the research turns: ids, similarity,
# hashes, devices and paths. The answer needs what each result says and where
# it came from, once, even when several searches returned it.

@dataclass
class Note:
    label: str  # Where it came from, for the reader: kind, source, project.
    text: str
    key: str    # A memory id, else a fingerprint of the text.
    hits: int = 1


_HIT = re.compile(r'^\[\d+\] id:(\S+)(.*)$')
_NOISE = re.compile(r'^(?:Workspace: .*verify live sources before edits\.|More results may exist: .*|'
                    r'\[excerpt chars \d+:\d+ of \d+; expand with .*\])$')
_PROVENANCE = re.compile(r' \| (?:sha256|recorded on|original path \(reference\)|path status|local path \(verify\)):?[^|\n]*')


def notes(messages: list[dict], per_note: int = 700) -> list[Note]:
    """What this reply's tool results found, after its last user message:
    search hits by memory id, other results whole, empty reads and errors
    left out. A note several results returned ranks first."""
    anchor = max(i for i, message in enumerate(messages) if message['role'] == 'user')
    calls = {call['id']: call.get('function', {}) for message in messages[anchor + 1:]
             for call in message.get('tool_calls') or []}
    found: dict[str, Note] = {}
    for message in messages[anchor + 1:]:
        if message['role'] != 'tool':
            continue
        call = calls.get(message.get('tool_call_id'), {})
        for note in _notes_of(message.get('content', ''), call.get('name', 'tool'), call.get('arguments'), per_note):
            if note.key in found:
                found[note.key].hits += 1
            else:
                found[note.key] = note
    ranked = sorted(enumerate(found.values()), key=lambda item: (-item[1].hits, item[0]))
    return [note for _, note in ranked]


def _notes_of(text: str, tool: str, args, per_note: int) -> list[Note]:
    lines = [line for line in text.strip().splitlines() if not _NOISE.match(line.strip())]
    body = '\n'.join(lines).strip()
    if not body or _EMPTY.match(body) or body.startswith('No relevant matches'):
        return []
    hits, current = [], None
    for line in lines:
        match = _HIT.match(line)
        if match:
            fields = dict(field.split(':', 1) for field in match.group(2).split(' | ') if ':' in field)
            current = (match.group(1), _label(fields) or tool, [])
            hits.append(current)
        elif current:
            current[2].append(line)
    if hits:
        return [Note(label, _clean(body_lines, per_note), memory_id) for memory_id, label, body_lines in hits
                if _clean(body_lines, per_note)]
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    subject = next((str(args[key]) for key in ('source', 'subject', 'query', 'url') if isinstance(args, dict) and args.get(key)), '')
    cleaned = _clean(lines, per_note)
    return [Note(f'{tool} {subject}'.strip(), cleaned, hashlib.sha256(cleaned.encode()).hexdigest()[:16])]


def _label(fields: dict[str, str]) -> str:
    """``conversation/784bca90 (migration), project``: the kind only where
    the source does not already say it."""
    field = lambda key: fields.get(key, '').strip()
    source, kind, project = field('source') or field('source (reference)'), field('type'), field('project')
    label = source + (f' ({kind})' if kind and not source.startswith(kind) else '') if source else kind
    return label + (f', {project}' if project and label else project)


def _clean(lines: list[str], limit: int) -> str:
    text = _PROVENANCE.sub('', '\n'.join(lines)).replace('\\n', '\n')
    text = re.sub(r'\n{3,}', '\n\n', re.sub(r'[ \t]+', ' ', text)).strip()
    return text if len(text) <= limit else text[:limit].rsplit(' ', 1)[0] + ' …'


def digest(found: list[Note], max_chars: int = 6000) -> str:
    """The notes, numbered for citation, within ``max_chars``."""
    blocks, used = [], 0
    for number, note in enumerate(found, 1):
        block = f'[{number}] {note.label}\n{note.text}'
        if blocks and used + len(block) > max_chars:
            break
        blocks.append(block)
        used += len(block) + 2
    return '\n\n'.join(blocks)


def highlights(found: list[Note], count: int = 4, width: int = 240) -> str:
    """A short list of the strongest notes, for a reply no model wrote."""
    items = []
    for note in found[:count]:
        text = ' '.join(note.text.split())
        if len(text) > width:
            text = text[:width].rsplit(' ', 1)[0] + ' …'
        items.append(f'- **{note.label}**: {text}')
    return '\n'.join(items)


def sources(text: str, found: list[Note]) -> str:
    """The notes an answer cites as [n], as a list to append to it."""
    cited = sorted({int(number) for number in re.findall(r'\[(\d+)\]', text)
                    if 0 < int(number) <= len(found)})
    return '\n'.join(f'- [{number}] {found[number - 1].label}' for number in cited)
