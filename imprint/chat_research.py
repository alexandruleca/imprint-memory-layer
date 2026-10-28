"""Reply-local research budgets and evidence-novelty heuristics (ADR 0102).

Novelty is a stopping hint, not a relevance or truth classifier. Resource tokens
are conservative reservations (prompt + maximum output), not billing usage.
"""
from __future__ import annotations

import hashlib
import json
import re
import time


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

    def reason(self):
        if not self.remaining():
            return 'time'
        if self.reserved >= self.tokens:
            return 'tokens'
        if self.stale >= self.patience:
            return 'no_progress'
        return None

    def reserve(self, prompt, output):
        self.check_time()
        if prompt < 0 or output < 1:
            raise ValueError('Research requests need a nonnegative prompt and positive output allowance')
        if self.reserved + prompt + output > self.tokens:
            raise ResearchExhausted('tokens')
        self.reserved += prompt + output

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
_EMPTY = re.compile(r'^(?:\[tool error\]|\[research stopped\]|no (?:results|memories|sources|facts|matches|indexed)|not found|error:)', re.I)
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
