"""Replay text, session provenance, output parsing and session-level scoring.

Each rule here copies ai-memory's evaluator (github.com/akitaonrails/ai-memory,
``evals/src/retrieval/{ingest,query,score}.rs``) so the numbers line up with
their published LongMemEval-S tables:

- every turn is replayed with its session date in front (``DatePrefix::Turn``);
  user turns are capped at 16 KiB with the prefix, assistant turns are cut to a
  2,000-byte excerpt before the prefix;
- a session's stored id is a UUIDv5 of ``lme-<question>\\n<raw id>``, so a
  session id that LongMemEval reuses across questions stays distinct;
- the ranked hits collapse to distinct sessions at their best rank before the
  top k is taken; hit@k, recall@k and recall-all@k are per question and
  macro-averaged;
- latency percentiles are nearest-rank over whole milliseconds;
- context tokens are the characters of each returned hit's displayed text
  divided by four, rounded up over the whole result.
"""
from __future__ import annotations

import math
import re
import statistics
import uuid

KS = (1, 3, 5, 10)
# ai-memory passes the largest k as the query limit.
QUERY_LIMIT = max(KS)
EXCERPT_MAX_BYTES = 2_000
USER_PROMPT_MAX_BYTES = 16 * 1024
CHARS_PER_TOKEN = 4


def project_for(question_id: str) -> str:
    return f"lme-{question_id}"


def workspace_for(question_id: str) -> str:
    """Imprint workspace names are lowercase letters, digits and hyphens."""
    return "lme-" + re.sub(r"[^a-z0-9-]", "-", question_id.lower())


def stored_session_uuid(question_id: str, raw: str) -> uuid.UUID:
    """Same UUIDv5 as ai-memory's ``stored_session_uuid``."""
    return uuid.uuid5(uuid.NAMESPACE_OID, f"{project_for(question_id)}\n{raw}")


def session_source(key: uuid.UUID) -> str:
    """The ``source`` stored with every memory of one session.

    Imprint's lexical scorer credits a query term (two or more word
    characters) found anywhere inside ``source``. Keeping one non-word
    character between every word character means no query term can match the
    provenance, so it cannot move a session up or down; the dataset's own ids
    (``answer_…``) never reach the store."""
    return "s:" + "-".join(key.hex[:12])


def cap_bytes(text: str, limit: int) -> str:
    """Truncate to ``limit`` UTF-8 bytes on a character boundary."""
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", "ignore")


def prefixed(date: str, text: str) -> str:
    return f"[session date: {date}] {text}"


def turn_text(role: str, date: str, content: str) -> str | None:
    """What the replay stores for one turn, or None for a role it skips."""
    if role == "user":
        return cap_bytes(prefixed(date, content), USER_PROMPT_MAX_BYTES)
    if role == "assistant":
        return cap_bytes(prefixed(date, cap_bytes(content, EXCERPT_MAX_BYTES)), USER_PROMPT_MAX_BYTES)
    return None


# ── Imprint's compact search output ─────────────────────────────────────────
# One block per hit: a provenance line "[n] id:<16 hex> …", the excerpt, an
# optional expand hint, then a blank line. A footer may follow.
_HEADER = re.compile(r"^\[(\d+)\] id:([0-9a-f]{16})(?=[ |]|$)(.*)$", re.M)
_SOURCE = re.compile(r"source(?: \(reference\))?:(s:[0-9a-f](?:-[0-9a-f]){11})")
_EXPAND = re.compile(r"\n\[excerpt chars \d+:\d+ of \d+; expand with get_memory\(memory_id=\"[0-9a-f]+\"\)\]$")
_FOOTER = "\n\nMore results may exist: offset="


def parse_hits(text: str, offset: int = 0) -> list[dict]:
    """Hits in rank order: ``id``, ``source`` (when shown) and ``snippet``,
    the excerpt the agent reads without the provenance line or expand hint.

    Imprint numbers a hit by its place in the ranked list and leaves out
    those under its relevance threshold, so numbers can skip; a header is
    accepted only when its number is above the previous one."""
    headers = []
    last = offset
    for match in _HEADER.finditer(text):
        if int(match.group(1)) > last:
            headers.append(match)
            last = int(match.group(1))
    hits = []
    for index, match in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        # A block is "<provenance>\n<excerpt>[\n<expand hint>]\n\n".
        body = text[match.end():end]
        if index + 1 == len(headers):
            footer = body.rfind(_FOOTER)
            if footer >= 0:
                body = body[:footer + 2]
        body = body.removeprefix("\n").removesuffix("\n\n")
        body = _EXPAND.sub("", body)
        source = _SOURCE.search(match.group(3))
        hits.append({"id": match.group(2), "source": source.group(1) if source else None, "snippet": body})
    return hits


def estimate_tokens(chars: int) -> int:
    return math.ceil(chars / CHARS_PER_TOKEN)


# ── Scores ──────────────────────────────────────────────────────────────────
def score_question(evidence: set, ranked: list, ks=KS) -> dict:
    """``ranked`` holds each hit's session (None when it has none) in rank
    order; repeated sessions keep their first rank."""
    seen, sessions = set(), []
    for session in ranked:
        if session is not None and session not in seen:
            seen.add(session)
            sessions.append(session)
    scores = {"retrieved_sessions": len(sessions)}
    for k in ks:
        top = set(sessions[:k])
        found = sum(1 for e in evidence if e in top)
        scores[f"hit@{k}"] = 1.0 if found else 0.0
        scores[f"recall@{k}"] = found / len(evidence) if evidence else 0.0
        scores[f"recall_all@{k}"] = 1.0 if evidence and found == len(evidence) else 0.0
    return scores


def percentile(values: list[int], p: float) -> int:
    """Nearest rank: the ceil(p·n)-th smallest, clamped to 1…n."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(p * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def aggregate(rows: list[dict], ks=KS) -> dict:
    """``rows``: per-question dicts with ``question_type``, scores,
    ``latency_ms`` and ``context_tokens``. Returns one entry per question
    type plus ``overall``, each a macro average."""
    slices: dict[str, list[dict]] = {}
    for row in rows:
        slices.setdefault(row["question_type"], []).append(row)
        slices.setdefault("overall", []).append(row)
    out = {}
    for name in sorted(slices):
        group = slices[name]
        n = len(group)
        entry = {"questions": n}
        for k in ks:
            for metric in ("hit", "recall", "recall_all"):
                entry[f"{metric}@{k}"] = sum(r[f"{metric}@{k}"] for r in group) / n
        latencies = [r["latency_ms"] for r in group]
        tokens = [r["context_tokens"] for r in group]
        entry["latency_p50_ms"] = percentile(latencies, 0.50)
        entry["latency_p95_ms"] = percentile(latencies, 0.95)
        entry["context_tokens_mean"] = sum(tokens) / n
        entry["context_tokens_median"] = float(statistics.median(tokens))
        for extra in ("response_tokens", "hits_shown", "retrieved_sessions"):
            if all(extra in r for r in group):
                entry[f"{extra}_mean"] = sum(r[extra] for r in group) / n
        out[name] = entry
    return out
