"""Bounded lexical/semantic rank fusion, shared by MCP and dashboard search."""
from __future__ import annotations

import math
import re
from collections import Counter

STOP_WORDS = set('a an and are as at be by can do does for from how i in is it of on or should that the this to use was we what when where which with you'.split())
CANDIDATE_LIMIT = 256


def query_terms(query: str) -> list[str]:
    words = re.findall(r'[\w]+', query.casefold())
    return list(dict.fromkeys(word for word in words if len(word) >= 2 and word not in STOP_WORDS))[:12]


def lexical_score(query: str, payload: dict) -> float:
    terms = query_terms(query)
    if not terms:
        return 0.0
    content = str(payload.get('content', '')).casefold()
    source = str(payload.get('source', '')).casefold()
    words = Counter(re.findall(r'[\w]+', content))
    coverage = sum(1 for term in terms if words[term] or term in source) / len(terms)
    tf = sum(math.log1p(words[term]) for term in terms)
    # Exact identifiers/path fragments deserve more weight than a common word.
    exact = sum(2 for term in terms if ('_' in term or any(c.isdigit() for c in term)) and (words[term] or term in source))
    phrase = 2 if query.strip().casefold() in content or query.strip().casefold() in source else 0
    return coverage + tf / max(1, math.log2(len(words) + 2)) + exact + phrase


def fuse(query: str, semantic: list[dict], lexical: list[dict]) -> list[dict]:
    candidates = {row['id']: dict(row) for row in [*lexical, *semantic]}
    semantic_rank = {row['id']: rank for rank, row in enumerate(semantic, 1)}
    lexical_order = sorted(candidates.values(), key=lambda row: (-lexical_score(query, row), row['id']))
    lexical_rank = {row['id']: rank for rank, row in enumerate(lexical_order, 1) if lexical_score(query, row) > 0}
    for mid, row in candidates.items():
        ranks = [rank[mid] for rank in (semantic_rank, lexical_rank) if mid in rank]
        row['retrieval_score'] = sum(1 / (60 + rank) for rank in ranks)
        row['match_type'] = 'hybrid' if mid in semantic_rank and mid in lexical_rank else ('lexical' if mid in lexical_rank else 'semantic')
        row['lexical_score'] = round(lexical_score(query, row), 4)
    return sorted(candidates.values(), key=lambda row: (-row['retrieval_score'], -row['lexical_score'], -row.get('similarity', 0), row['id']))
