"""The background LLM tagging backlog (ADR 0067).

The paths that store memories in bulk (learn, ingest, refresh, ingest-url,
desktop learn, the Stop hook's import and extraction, inline content) store
them with deterministic, keyword and zero-shot tags only. While ``tagger.llm``
is on they also mark each memory ``tag_pending`` instead of calling the LLM,
so they finish once the memories are embedded and searchable.

This module drains that backlog: newest memories first, or in random order
while a paired device tags the same backlog (ADR 0167), ``concurrency()``
memories at a time, each written in the same payload update that clears its
marker (``vectorstore.set_classifications``). A crash, a cancel or a stop
loses at most the memories being classified.

Two callers:

- the dashboard's dispatcher runs ``python -m imprint.tag_backlog <job_id>``
  when no other job is waiting. It holds the queue lock as a preemptible
  holder, so any other lock taker stops it, and it exits between memories
  when a job is queued (exit code ``YIELDED``); the dispatcher starts it again
  later;
- ``imprint retag --pending`` drains it in the foreground.
"""

from __future__ import annotations

import sys
import time
from typing import Callable

COMMAND = "tag-backlog"
# EX_TEMPFAIL: stopped for a queued job, to be started again later.
YIELDED = 75
_PAGE = 16
_WAL_EVERY = 25


def deferring() -> bool:
    """Whether this device tags its backlog (``tagger.llm``)."""
    from .config_schema import resolve
    return bool(resolve("tagger.llm")[0])


def peers_tag(workspace: str | None = None) -> bool:
    """Whether a device this workspace is paired with said, at its last
    sync, that it runs LLM tagging (ADR 0138)."""
    from . import config, sync_pairs
    ws = config.get_active_workspace() if workspace is None else workspace
    try:
        return any(pair.get('workspace') == ws and pair.get('enabled', True) and pair.get('peer_tags') is True
                   for pair in sync_pairs.pairs())
    except Exception:
        return False


def marking(workspace: str | None = None) -> bool:
    """Whether storing paths mark new memories as waiting for LLM tagging:
    when this device tags them, or, with ``tagger.peer`` on, when a paired
    device does and sends the tags back (ADR 0138)."""
    if deferring():
        return True
    from .config_schema import resolve
    return bool(resolve("tagger.peer")[0]) and peers_tag(workspace)


def adopt(records: list, workspace: str | None = None) -> dict:
    """A peer's memories that wait for LLM tagging (ADR 0138): each one held
    here and not classified yet waits here too, so a device that tags takes
    it. One classified here is left; its tags travel back instead."""
    from . import config, vectorstore as vs
    ids = [r['id'] for r in records if isinstance(r, dict) and isinstance(r.get('id'), str) and 0 < len(r['id']) <= 128]
    stats = {'waiting': 0, 'skipped': len(records) - len(ids)}
    if not ids:
        return stats
    client = vs._get_client()
    coll = config.collection_name(workspace)
    if not client.collection_exists(coll):
        stats['skipped'] += len(ids)
        return stats
    held = {pl['_mid']: pl for pl in vs.retrieve_payloads(ids, ['_mid', 'tagged_at', 'tag_pending'], workspace)}
    marks = [vs._point_uuid(mid) for mid, pl in held.items() if not pl.get('tagged_at') and not pl.get('tag_pending')]
    for start in range(0, len(marks), 256):
        client.set_payload(collection_name=coll, payload={'tag_pending': True}, points=marks[start:start + 256])
    stats['waiting'] = len(marks)
    stats['skipped'] += len(ids) - len(marks)
    return stats


def _pending_filter(project: str = ""):
    from qdrant_client import models as qm
    must = [qm.FieldCondition(key="tag_pending", match=qm.MatchValue(value=True))]
    if project:
        must.append(qm.FieldCondition(key="project", match=qm.MatchValue(value=project)))
    return qm.Filter(must=must)


def pending_count(workspace: str | None = None, project: str = "") -> int:
    """Memories of one workspace that wait for LLM tagging. A workspace
    without a collection has none; counting never creates one."""
    from . import config, vectorstore as vs
    client = vs._get_client()
    coll = config.collection_name(workspace)
    if not client.collection_exists(coll):
        return 0
    return client.count(collection_name=coll, count_filter=_pending_filter(project), exact=True).count


def pending_by_workspace() -> dict[str, int]:
    """The backlog of every known workspace, the active one first."""
    from . import config
    active = config.get_active_workspace()
    known = [active, *(name for name in config.get_known_workspaces() if name != active)]
    return {name: pending_count(name) for name in known}


def _request(payload: dict) -> dict:
    """What the tagger is asked about a stored memory."""
    source = payload.get("source", "")
    return {"content": payload.get("content", ""), "rel_path": source,
            "project_hint": source.split("/", 1)[0] if "/" in source else ""}


def classify(payload: dict, workspace: str | None = None, classification=None) -> tuple[dict, str]:
    """LLM classification of a stored memory: its new tags and type.

    The prompt is redacted inside the tagger before any provider sees it
    (ADR 0060). Deterministic fields the memory got when it was stored stay
    when the classification leaves them blank: conversations carry
    lang=conversation, layer=session, kind=qa that no path rule derives.
    ``classification`` is the model's answer when a batch already asked it.
    """
    from . import tagger
    request = _request(payload)
    existing = payload.get("tags") or {}
    tags = tagger.build_payload_tags(
        request["content"],
        rel_path=request["rel_path"],
        llm=True,
        zero_shot=False,
        project_hint=request["project_hint"],
        workspace=workspace,
        classification=classification,
    )
    kind = tags.pop("_llm_type", "")
    for key in ("lang", "layer", "kind"):
        if existing.get(key) and not tags.get(key):
            tags[key] = existing[key]
    return tags, kind


def _next_page(client, coll: str, flt, shuffled: bool = False, limit: int = _PAGE) -> list:
    """The newest memories still on the backlog. Ordering is a preference:
    a memory without a timestamp, which ordering would skip, comes after.

    ``shuffled``: a random page instead, so devices that tag the same
    backlog at once each take different memories (ADR 0167)."""
    from qdrant_client import models as qm
    fields = ["content", "source", "tags"]
    if shuffled:
        return client.query_points(collection_name=coll, query=qm.SampleQuery(sample=qm.Sample.RANDOM),
                                   query_filter=flt, limit=limit, with_payload=fields, with_vectors=False).points
    points, _ = client.scroll(collection_name=coll, scroll_filter=flt, limit=limit, with_payload=fields,
                              with_vectors=False,
                              order_by=qm.OrderBy(key="timestamp", direction=qm.Direction.DESC))
    if not points:
        points, _ = client.scroll(collection_name=coll, scroll_filter=flt, limit=limit, with_payload=fields,
                                  with_vectors=False)
    return points


def concurrency() -> int:
    """How many memories to classify at once (``tagger.llm_concurrency``):
    parallel requests to a server provider, one batch for the local model
    (ADR 0165)."""
    from .config_schema import resolve
    return int(resolve("tagger.llm_concurrency")[0])


def classify_all(payloads: list[dict], workspace: str | None, workers: int) -> list[tuple[dict, str]]:
    """``classify`` for each payload, ``workers`` at a time; results in order."""
    if workers <= 1 or len(payloads) <= 1:
        return [classify(payload, workspace) for payload in payloads]
    from . import tagger
    if tagger._get_llm_provider() == "local":
        answers = tagger.derive_llm_classify_many([_request(payload) for payload in payloads], workspace, workers)
        return [classify(payload, workspace, answer) for payload, answer in zip(payloads, answers)]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(workers, len(payloads)), thread_name_prefix="imprint-tag") as pool:
        return list(pool.map(lambda payload: classify(payload, workspace), payloads))


def drain(
    workspace: str | None = None,
    *,
    project: str = "",
    should_stop: Callable[[], bool] = lambda: False,
    on_tagged: Callable[[int], None] | None = None,
) -> tuple[int, bool]:
    """Tag one workspace's backlog, newest first, or in random order while a
    paired device in the workspace tags too (ADR 0167). Returns ``(tagged, stopped)``.

    ``should_stop`` is asked before each group of memories classified at once
    (``concurrency()``); a classification once begun is written. Each
    memory's tags, type, ``tagged_at`` (ADR 0051) and the cleared marker are
    one payload update.
    """
    from . import vectorstore as vs
    client, coll = vs._ensure_collection(workspace)
    flt = _pending_filter(project)
    workers = concurrency()
    shuffled = peers_tag(workspace)
    page = _PAGE - _PAGE % workers if workers <= _PAGE else workers  # Whole groups.
    tagged = 0
    logged = 0
    seen: set = set()
    stopped = False
    try:
        while not stopped:
            points = _next_page(client, coll, flt, shuffled, page)
            if not points:
                break
            # ``workers`` memories are classified at once (parallel requests,
            # or one batch on the local model); their results are written in
            # order, one memory at a time.
            for start in range(0, len(points), workers):
                if should_stop():
                    stopped = True
                    break
                group = points[start:start + workers]
                for point in group:
                    if point.id in seen:
                        raise RuntimeError(f"memory {point.id} stayed on the tagging backlog after it was tagged")
                    seen.add(point.id)
                for point, (tags, kind) in zip(group, classify_all([p.payload or {} for p in group], workspace, workers)):
                    tagged, logged = _store(client, coll, vs, workspace, point, tags, kind, tagged, logged, on_tagged)
    finally:
        if tagged > logged:
            vs._wal_log("llm_tag", workspace=workspace, count=tagged - logged)
    return tagged, stopped


def _store(client, coll, vs, workspace, point, tags, kind, tagged, logged, on_tagged):
    """Write one memory's classification; returns the updated (tagged, logged)."""
    try:
        vs.set_classifications(client, coll, [(point.id, tags, kind)])
    except Exception:
        # A memory replaced or deleted while it was classified is no longer
        # on the backlog; anything else is a real failure.
        if client.retrieve(collection_name=coll, ids=[point.id], with_payload=False):
            raise
        return tagged, logged
    tagged += 1
    if on_tagged:
        on_tagged(tagged)
    if tagged - logged >= _WAL_EVERY:
        # Payload updates change no point count: log them so readers of the
        # data fingerprint (dashboard caches) see new tags.
        vs._wal_log("llm_tag", workspace=workspace, count=tagged - logged)
        logged = tagged
    return tagged, logged


def report(stored: int) -> None:
    """Tell whoever ran a storing command where its LLM tagging went."""
    here = deferring()
    if stored <= 0 or not (here or marking()):
        return
    try:
        waiting = pending_count()
    except Exception:
        return
    if waiting and here:
        print(f"  LLM tags: {waiting} memories wait for background tagging. The dashboard tags them "
              "while it runs; imprint retag --pending tags them now.")
    elif waiting:
        print(f"  LLM tags: {waiting} memories wait for a paired device that runs LLM tagging; "
              "sync takes them there and brings the tags back.")


# ── The dispatcher's background job ───────────────────────────


def run(job_id: str = "") -> int:
    """The background job: hold the lock as a preemptible holder, tag every
    workspace's backlog, then, with ``organize.background`` on, organize
    what changed (ADR 0132); yield when a job is queued. Returns the exit
    code."""
    from . import queue_lock
    from .progress import clear_progress
    descriptor = queue_lock.acquire(COMMAND, job_id, preemptible=True)
    if descriptor is None:
        print("Another job holds the queue; background work waits for it.", flush=True)
        return YIELDED
    try:
        code = _tag_all()
        if code == YIELDED:
            return code
        organized = _organize()
        return organized if organized == YIELDED else max(code, organized)
    finally:
        clear_progress()
        queue_lock.release(descriptor)


def _organize() -> int:
    from . import organize, queue
    if not organize.background_enabled():
        return 0

    def stop() -> bool:
        return queue.foreground_waiting() or not organize.background_enabled()

    try:
        code = organize.background(stop)
    except Exception as error:  # noqa: BLE001 - the run's log says why
        print(f"Organize failed: {error}", flush=True)
        return 1
    if code == YIELDED:
        print("Organize yielded to a queued job; it resumes when the queue is idle.", flush=True)
    return code


def _tag_all() -> int:
    from . import queue, tagger
    from .progress import write_progress
    if not deferring():
        return 0
    backlog = {name: count for name, count in pending_by_workspace().items() if count}
    total = sum(backlog.values())
    if not total:
        print("No memories wait for LLM tagging.", flush=True)
        return 0
    reason = tagger.llm_unavailable()
    if reason:
        print(f"LLM tagging cannot run: {reason}", flush=True)
        return 1
    print(f"Tagging {total} memories with {tagger._get_llm_provider()}, newest first.", flush=True)
    started = time.time()
    done = 0
    last_print = 0.0

    def stop() -> bool:
        return queue.foreground_waiting() or not deferring()

    for workspace, count in backlog.items():
        def tagged_one(n: int, workspace=workspace) -> None:
            nonlocal last_print
            processed = done + n
            write_progress(COMMAND, processed, max(total, processed), processed, 0, started, [workspace],
                           phase="llm_tagging", unit="memories")
            now = time.time()
            if now - last_print >= 30 or processed == total:
                last_print = now
                print(f"  {processed}/{total} tagged ({(now - started) / processed:.1f} s each)", flush=True)

        try:
            tagged, stopped = drain(workspace, should_stop=stop, on_tagged=tagged_one)
        except ValueError as error:
            # A workspace whose collection cannot be opened (for example
            # another embedding profile) does not hold up the others.
            print(f"  Workspace {workspace}: {error}", flush=True)
            continue
        done += tagged
        if stopped:
            if not deferring():
                print(f"LLM tagging was turned off after {done} memories.", flush=True)
                return 0
            print(f"Yielded to a queued job after {done} memories; the rest wait for the queue to empty.",
                  flush=True)
            return YIELDED
    print(f"Tagged {done} memories in {time.time() - started:.0f} s.", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    return run(args[0] if args else "")


if __name__ == "__main__":
    sys.exit(main())
