"""One name per topic, and how topics relate (ADRs 0198, 0199).

LLM tagging writes free-form topics, so one idea is stored under several
spellings: ``unit-tests``, ``unit-test``, ``unittest``. A topic's key folds
case, separators and plural endings; across the store, the spelling most
memories use names the key. New tags take that name when they are written
(``canonical``), and views that count or filter topics merge every spelling
of a key (``merge``, ``spellings``). Stored tags are never rewritten, so
older devices and existing filters keep working.

Topics relate when memories carry them together more often than their
sizes predict (normalized pointwise mutual information). ``topic_map``
links the largest topics that way, groups them by label propagation and
names each group's largest topic its hub.
"""
from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass, field

from . import config

_SEPARATORS = re.compile(r'[\s_\-]+')
_TTL = 300.0
_VOCABULARY_LIMIT = 100_000


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith('ies'):
        return word[:-3] + 'y'
    if word.endswith(('sses', 'xes', 'zes', 'ches', 'shes')):
        return word[:-2]
    if len(word) > 3 and word.endswith('s') and not word.endswith(('ss', 'us', 'is')):
        return word[:-1]
    return word


def key(name: str) -> str:
    """What spellings of one topic share: lower case, no separators, each
    word's plural ending folded."""
    return ''.join(_singular(word) for word in _SEPARATORS.split(str(name).strip().lower()) if word)


@dataclass
class Vocabulary:
    """The store's topics: each spelling's name, each name's spellings and count."""
    name: dict[str, str] = field(default_factory=dict)         # key -> name
    spellings: dict[str, list[str]] = field(default_factory=dict)  # key -> spellings, most used first
    count: dict[str, int] = field(default_factory=dict)        # key -> memories, all spellings

    @classmethod
    def from_counts(cls, pairs) -> 'Vocabulary':
        groups: dict[str, list[tuple[str, int]]] = {}
        for spelling, count in pairs:
            if spelling:
                groups.setdefault(key(spelling), []).append((spelling, int(count)))
        vocabulary = cls()
        for k, members in groups.items():
            if not k:
                continue
            members.sort(key=lambda sc: (-sc[1], len(sc[0]), sc[0]))
            vocabulary.name[k] = members[0][0]
            vocabulary.spellings[k] = [spelling for spelling, _ in members]
            vocabulary.count[k] = sum(count for _, count in members)
        return vocabulary

    def named(self, spelling: str) -> str:
        return self.name.get(key(spelling), spelling)


_cache: dict[str, tuple[float, tuple, Vocabulary]] = {}
_lock = threading.Lock()


def _facet(filt=None, limit=_VOCABULARY_LIMIT, workspace: str | None = None) -> list[tuple[str, int]]:
    from . import vectorstore as vs
    client, coll = vs._ensure_collection(workspace)
    hits = client.facet(collection_name=coll, key='tags.topics', facet_filter=filt, limit=limit).hits
    return [(hit.value, hit.count) for hit in hits]


def _opened(workspace: str) -> bool:
    from . import vectorstore as vs
    try:
        return vs._resolve_collection(workspace) in vs._collections_ready
    except Exception:
        return False


def vocabulary(workspace: str | None = None, *, open: bool = False) -> Vocabulary:
    """The workspace's topic vocabulary, read again when its point count
    changes or after a few minutes. Naming and filtering helpers read it only
    from a collection this process already opened (``open=False``), so they
    never start or reach a store on their own; graph views open it."""
    ws = workspace or config.get_active_workspace()
    if not open and not _opened(ws):
        return Vocabulary()
    from . import vectorstore as vs
    client, coll = vs._ensure_collection(ws)
    stamp = (coll, client.get_collection(coll).points_count or 0)
    with _lock:
        hit = _cache.get(ws)
    if hit and hit[1] == stamp and time.monotonic() - hit[0] < _TTL:
        return hit[2]
    built = Vocabulary.from_counts(_facet(workspace=ws))
    with _lock:
        _cache[ws] = (time.monotonic(), stamp, built)
    return built


def reset() -> None:
    with _lock:
        _cache.clear()


def canonical(names, workspace: str | None = None) -> list[str]:
    """New tags under the name the store already uses for them, without
    repeats. Unknown topics, or an unreadable store, keep their spelling."""
    try:
        known = vocabulary(workspace)
    except Exception:
        known = Vocabulary()
    out: list[str] = []
    for name in names:
        named = known.named(name)
        if named not in out:
            out.append(named)
    return out


def merge(pairs, workspace: str | None = None) -> list[tuple[str, int]]:
    """Facet ``(spelling, count)`` pairs merged by key, under the store's
    name for each key, largest first."""
    try:
        known = vocabulary(workspace)
    except Exception:
        known = Vocabulary()
    totals: dict[str, int] = {}
    names: dict[str, str] = {}
    for spelling, count in pairs:
        if not spelling:
            continue
        k = key(spelling)
        names.setdefault(k, known.name.get(k, spelling))
        totals[k] = totals.get(k, 0) + int(count)
    return sorted(((names[k], total) for k, total in totals.items()), key=lambda nc: (-nc[1], nc[0]))


def spellings(name: str, workspace: str | None = None) -> list[str]:
    """Every stored spelling of ``name``'s topic, for filters (``name`` included)."""
    try:
        found = vocabulary(workspace).spellings.get(key(name), [])
    except Exception:
        found = []
    return list(dict.fromkeys([name, *found]))


def condition(name: str, workspace: str | None = None):
    """A Qdrant condition matching memories tagged with any spelling of ``name``."""
    from qdrant_client import models as qm
    return qm.FieldCondition(key='tags.topics', match=qm.MatchAny(any=spellings(name, workspace)))


# ── How topics relate ─────────────────────────────────────────────────

MIN_TOGETHER = 3      # memories carrying both, below which a pair is noise
EDGES_PER_TOPIC = 6   # strongest links kept for each topic
RELATED_SHARE = 0.005  # share of a topic's memories a related topic must carry too


def strength(together: int, a: int, b: int, total: int) -> float:
    """Normalized pointwise mutual information, 0 when unrelated, 1 when the
    two topics only ever appear together."""
    if together <= 0 or a <= 0 or b <= 0 or total <= 0 or together >= total:
        return 0.0
    p_ab = together / total
    pmi = math.log(p_ab / ((a / total) * (b / total)))
    return max(0.0, min(1.0, pmi / -math.log(p_ab)))


def together(name: str, workspace: str | None = None, limit: int = 400) -> dict[str, int]:
    """name -> memories tagged with both it and ``name`` (other topics only)."""
    own = key(name)
    out: dict[str, int] = {}
    from qdrant_client import models as qm
    for other, count in merge(_facet(qm.Filter(must=[condition(name, workspace)]), limit, workspace), workspace):
        if key(other) != own:
            out[other] = count
    return out


def related(name: str, workspace: str | None = None, limit: int = 8) -> list[dict]:
    """The topics most associated with ``name``: {name, count, together, strength}."""
    known = vocabulary(workspace, open=True)
    total = _total(workspace)
    own = known.count.get(key(name), 0)
    # A tiny topic always found with a large one scores high but says little:
    # it must share at least half a percent of the large one's memories.
    floor = max(MIN_TOGETHER, math.ceil(own * RELATED_SHARE))
    rows = []
    for other, both in together(name, workspace).items():
        if both < floor:
            continue
        s = strength(both, own, known.count.get(key(other), both), total)
        if s > 0:
            rows.append({'name': other, 'count': known.count.get(key(other), both), 'together': both, 'strength': s})
    rows.sort(key=lambda row: (-row['strength'], -row['together'], row['name']))
    return rows[:limit]


def _total(workspace: str | None) -> int:
    from . import vectorstore as vs
    client, coll = vs._ensure_collection(workspace)
    return int(client.count(coll, exact=False).count or 0)


def communities(names: list[str], links: dict[tuple[str, str], float], sizes: dict[str, int]) -> dict[str, str]:
    """name -> its group's hub, by weighted label propagation. Deterministic:
    topics are visited largest first and ties go to the larger, then the
    alphabetically first, label. The hub is the group's largest topic."""
    neighbours: dict[str, dict[str, float]] = {name: {} for name in names}
    for (a, b), weight in links.items():
        neighbours[a][b] = weight
        neighbours[b][a] = weight
    order = sorted(names, key=lambda n: (-sizes.get(n, 0), n))
    label = {name: name for name in names}
    for _ in range(30):
        changed = False
        for name in order:
            if not neighbours[name]:
                continue
            votes: dict[str, float] = {}
            for other, weight in neighbours[name].items():
                votes[label[other]] = votes.get(label[other], 0.0) + weight
            best = max(votes.values())
            current = votes.get(label[name], 0.0)
            if current >= best - 1e-12:
                continue
            choice = min((l for l, v in votes.items() if v >= best - 1e-12), key=lambda l: (-sizes.get(l, 0), l))
            label[name], changed = choice, True
        if not changed:
            break
    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(label[name], []).append(name)
    hub = {}
    for members in groups.values():
        top = min(members, key=lambda n: (-sizes.get(n, 0), n))
        hub.update({member: top for member in members})
    return hub


def topic_map(cap: int, workspace: str | None = None) -> dict:
    """The ``cap`` largest topics, the links between them, each one's group
    hub, and whether more topics exist."""
    from concurrent.futures import ThreadPoolExecutor
    known = vocabulary(workspace, open=True)
    ranked = sorted(known.count.items(), key=lambda kc: (-kc[1], known.name[kc[0]]))
    top = [known.name[k] for k, _ in ranked[:cap]]
    sizes = {name: known.count[key(name)] for name in top}
    total = _total(workspace)
    chosen = {key(name): name for name in top}
    with ThreadPoolExecutor(max_workers=4) as pool:
        pairs = dict(zip(top, pool.map(lambda name: together(name, workspace), top)))
    candidates: dict[tuple[str, str], tuple[float, int]] = {}
    for name, others in pairs.items():
        for other, both in others.items():
            other = chosen.get(key(other))
            if not other or other == name or both < MIN_TOGETHER:
                continue
            a, b = sorted((name, other))
            s = strength(both, sizes[a], sizes[b], total)
            if s > 0 and (a, b) not in candidates:
                candidates[(a, b)] = (s, both)
    best: dict[str, set[tuple[str, str]]] = {}
    for name in top:
        mine = sorted((pair for pair in candidates if name in pair),
                      key=lambda pair: (-candidates[pair][0], -candidates[pair][1], pair))
        best[name] = set(mine[:EDGES_PER_TOPIC])
    kept = {pair: candidates[pair] for pair in candidates if pair in best[pair[0]] or pair in best[pair[1]]}
    hubs = communities(top, {pair: s for pair, (s, _) in kept.items()}, sizes)
    return {'topics': [(name, sizes[name]) for name in top], 'links': kept, 'hubs': hubs,
            'more': len(ranked) > cap}
