"""The Home page's summary of a workspace, served from a snapshot (ADR 0152).

Counting a large workspace takes Qdrant facets over every payload index, and
while learning or background tagging writes, every read used to start that
work again. Each workspace's summary is instead kept as a snapshot:

- A read whose snapshot still matches the workspace is answered at once.
- A read after a change starts one rebuild in a background thread, waits a
  moment for it, and otherwise answers with the previous snapshot marked
  ``stale``; the page shows it as updating and asks again shortly.
- A change is noticed from the collection's point count and the workspace
  WAL (written by every process), from :func:`invalidate` (dashboard writes
  and finished jobs), and from age: a snapshot older than ``MAX_AGE`` is
  rebuilt even when nothing was noticed, which bounds what a payload-only
  update made elsewhere can hide.
- The first read of a workspace builds synchronously, so errors such as an
  embedding profile conflict still reach the caller.

Snapshots are keyed by workspace and every build names its workspace
explicitly, so a summary never crosses workspaces.
"""

from __future__ import annotations

import datetime
import os
import threading
import time
from dataclasses import dataclass, field

from . import config

MAX_AGE = 60.0       # seconds before a snapshot is rebuilt without a noticed change
WAIT = 0.3           # how long a read waits for a rebuild before answering stale
MIN_INTERVAL = 2.0   # seconds between background rebuilds of one workspace
RECENT_SCAN = 400    # newest memories read to list recently added sources
RECENT_SOURCES = 6
TOP = 10
MONTHS = 12


@dataclass
class _Entry:
    data: dict
    fingerprint: tuple | None
    generation: int
    built_at: float          # wall clock, reported as the snapshot's age
    built_mono: float
    build_ms: float


@dataclass
class _State:
    entry: _Entry | None = None
    generation: int = 0      # bumped by invalidate()
    build_lock: threading.Lock = field(default_factory=threading.Lock)
    done: threading.Event = field(default_factory=threading.Event)
    last_build_mono: float = 0.0
    error: str | None = None


_states: dict[str, _State] = {}
_lock = threading.Lock()


def _state(workspace: str) -> _State:
    with _lock:
        return _states.setdefault(workspace, _State())


def fingerprint(workspace: str) -> tuple | None:
    """The collection, its point count and the workspace WAL's size and
    modification time; None when the store cannot be read."""
    from . import vectorstore as vs
    try:
        client, coll = vs._ensure_collection(workspace)
        count = client.get_collection(coll).points_count or 0
    except Exception:
        return None
    try:
        stat = os.stat(config.wal_path(workspace))
        wal = (stat.st_size, stat.st_mtime_ns)
    except OSError:
        wal = (0, 0)
    return coll, count, wal


def invalidate(workspace: str | None = None) -> None:
    """Mark one workspace's snapshot (every one when None) out of date. The
    snapshot is kept to answer reads until its rebuild finishes."""
    with _lock:
        targets = list(_states.values()) if workspace is None else [_states.setdefault(workspace, _State())]
        for state in targets:
            state.generation += 1


def reset() -> None:
    """Forget every snapshot, e.g. after a wipe recreated the store."""
    with _lock:
        _states.clear()


def _fresh(state: _State, entry: _Entry | None, current: tuple | None) -> bool:
    return (entry is not None and current is not None and entry.fingerprint == current
            and entry.generation == state.generation
            and time.monotonic() - entry.built_mono < MAX_AGE)


def _rebuild(workspace: str, state: _State) -> None:
    """Build and store a snapshot; the caller holds ``state.build_lock``.
    The fingerprint and generation are read first, so a write that lands
    during the build leaves the result out of date."""
    with _lock:
        generation = state.generation
    current = fingerprint(workspace)
    started = time.monotonic()
    data = build(workspace)
    entry = _Entry(data, current, generation, time.time(), time.monotonic(),
                   round((time.monotonic() - started) * 1000, 1))
    with _lock:
        state.entry = entry
        state.error = None
        state.last_build_mono = time.monotonic()


def _background(workspace: str, state: _State) -> None:
    try:
        _rebuild(workspace, state)
    except Exception as error:  # Keep answering with the older snapshot.
        with _lock:
            state.error = str(error) or type(error).__name__
            state.last_build_mono = time.monotonic()
    finally:
        state.build_lock.release()
        state.done.set()


def _start(workspace: str, state: _State) -> bool:
    """Start one background rebuild unless one runs or one just ran."""
    if time.monotonic() - state.last_build_mono < MIN_INTERVAL:
        return False
    if not state.build_lock.acquire(blocking=False):
        return True
    state.done = threading.Event()
    threading.Thread(target=_background, args=(workspace, state), daemon=True,
                     name=f"dashboard-summary-{workspace}").start()
    return True


def get(workspace: str | None = None, wait: float | None = WAIT) -> dict:
    """The workspace's summary with ``workspace``, ``built_at``, ``age`` (seconds),
    ``stale`` (a newer one is being built or due) and ``refreshing``.

    ``wait=None`` waits for an up-to-date snapshot, building it if needed."""
    ws = workspace or config.get_active_workspace()
    state = _state(ws)
    current = fingerprint(ws)
    with _lock:
        entry = state.entry
    if _fresh(state, entry, current):
        return _view(ws, state, entry, stale=False)
    if entry is None or wait is None:
        # Nothing to show yet, or the caller asked for a current answer:
        # build here, after any build already running, so errors surface.
        with state.build_lock:
            with _lock:
                entry = state.entry
            if not _fresh(state, entry, fingerprint(ws)):
                _rebuild(ws, state)
                state.done.set()
        with _lock:
            entry = state.entry
        return _view(ws, state, entry, stale=False)
    refreshing = _start(ws, state)
    if refreshing and wait > 0:
        state.done.wait(wait)
    with _lock:
        latest = state.entry
    stale = not _fresh(state, latest, current)
    return _view(ws, state, latest, stale=stale, refreshing=stale and state.build_lock.locked())


def _view(workspace: str, state: _State, entry: _Entry, stale: bool, refreshing: bool = False) -> dict:
    return {
        **entry.data,
        "workspace": workspace,
        "built_at": entry.built_at,
        "age": round(max(0.0, time.time() - entry.built_at), 1),
        "build_ms": entry.build_ms,
        "stale": stale,
        "refreshing": refreshing,
        **({"refresh_error": state.error} if state.error and stale else {}),
    }


# ── Building ───────────────────────────────────────────────────

def _month_starts(now: datetime.datetime) -> list[datetime.datetime]:
    """The first instant of each of the last MONTHS calendar months, oldest first, in local time."""
    year, month = now.year, now.month
    starts = []
    for _ in range(MONTHS):
        starts.append(datetime.datetime(year, month, 1).astimezone())
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return starts[::-1]


def build(workspace: str) -> dict:
    """Count one workspace: totals, the largest groups of each kind, memories
    added per month, recently added sources and the tagging backlog. Every
    count comes from a facet or count over a payload index; only the newest
    RECENT_SCAN memories are read."""
    from qdrant_client import models as qm
    from . import vectorstore as vs
    from .project_associations import project_counts

    client, coll = vs._ensure_collection(workspace)
    total = client.get_collection(coll).points_count or 0
    empty = {"total": 0, "project_count": 0, "projects": [], "types": [], "langs": [], "lang_count": 0,
             "domains": [], "domain_count": 0, "layers": [], "topics": [], "topic_count": 0,
             "source_count": 0, "tag_pending": 0, "added_7d": 0, "timeline": [], "recent": []}

    now = datetime.datetime.now().astimezone()
    starts = _month_starts(now)
    if total == 0:
        return {**empty, "timeline": [{"month": s.strftime("%Y-%m"), "count": 0} for s in starts]}

    def facet(key: str, limit: int) -> list[list]:
        try:
            hits = client.facet(coll, key, limit=limit).hits
        except Exception:
            return []
        return [[hit.value, hit.count] for hit in hits if hit.value not in (None, "")]

    def count(condition) -> int:
        try:
            return client.count(coll, count_filter=qm.Filter(must=[condition]), exact=True).count
        except Exception:
            return 0

    try:
        projects = [[name, n] for name, n in project_counts(workspace, None, 10_000) if name]
    except Exception:
        projects = facet("project", 10_000)
    langs = facet("tags.lang", 1_000)
    domains = facet("tags.domain", 10_000)
    topics = facet("tags.topics", 100_000)
    sources = facet("source", 1_000_000)

    timeline = []
    bounds = [s.timestamp() for s in starts] + [None]
    for start, lo, hi in zip(starts, bounds, bounds[1:]):
        timeline.append({"month": start.strftime("%Y-%m"),
                         "count": count(qm.FieldCondition(key="timestamp", range=qm.Range(gte=lo, lt=hi)))})

    return {
        **empty,
        "total": total,
        "project_count": len(projects),
        "projects": projects[:TOP],
        "types": facet("type", 100)[:TOP],
        "langs": langs[:TOP],
        "lang_count": len(langs),
        "domains": domains[:TOP],
        "domain_count": len(domains),
        "layers": facet("tags.layer", 100)[:TOP],
        "topics": topics[:TOP],
        "topic_count": len(topics),
        "source_count": len(sources),
        "tag_pending": count(qm.FieldCondition(key="tag_pending", match=qm.MatchValue(value=True))),
        "added_7d": count(qm.FieldCondition(key="timestamp", range=qm.Range(gte=time.time() - 7 * 86400))),
        "timeline": timeline,
        "recent": _recent(client, coll, workspace),
    }


def _recent(client, coll: str, workspace: str) -> list[dict]:
    """The sources of the newest memories, newest first, with a conversation's title."""
    from qdrant_client import models as qm
    try:
        points, _ = client.scroll(coll, limit=RECENT_SCAN, with_payload=["source", "project", "timestamp", "type"],
                                  order_by=qm.OrderBy(key="timestamp", direction=qm.Direction.DESC))
    except Exception:
        return []
    recent: dict[str, dict] = {}
    for point in points:
        payload = point.payload or {}
        source = payload.get("source") or ""
        if not source:
            continue
        if source not in recent:
            if len(recent) >= RECENT_SOURCES:
                continue
            recent[source] = {"source": source, "project": payload.get("project") or "",
                              "type": payload.get("type") or "", "timestamp": payload.get("timestamp"), "memories": 0}
        recent[source]["memories"] += 1
    if recent:
        try:
            from .source_snapshots import conversation_titles
            titles = conversation_titles(workspace)
        except Exception:
            titles = {}
        for item in recent.values():
            item["title"] = titles.get(item["source"], "")
    return list(recent.values())
