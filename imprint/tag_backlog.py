"""The background LLM tagging backlog (ADR 0067).

The paths that store memories in bulk (learn, ingest, refresh, ingest-url,
desktop learn, the Stop hook's import and extraction, inline content) store
them with deterministic, keyword and zero-shot tags only. While ``tagger.llm``
is on they also mark each memory ``tag_pending`` instead of calling the LLM,
so they finish once the memories are embedded and searchable.

This module drains that backlog: newest memories first, one classification
at a time, each written in the same payload update that clears its marker
(``vectorstore.set_classifications``). A crash, a cancel or a stop loses at
most the memory being classified.

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
    """Whether storing paths leave LLM tagging to the backlog (``tagger.llm``)."""
    from .config_schema import resolve
    return bool(resolve("tagger.llm")[0])


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


def classify(payload: dict, workspace: str | None = None) -> tuple[dict, str]:
    """LLM classification of a stored memory: its new tags and type.

    The prompt is redacted inside the tagger before any provider sees it
    (ADR 0060). Deterministic fields the memory got when it was stored stay
    when the classification leaves them blank: conversations carry
    lang=conversation, layer=session, kind=qa that no path rule derives.
    """
    from . import tagger
    content = payload.get("content", "")
    source = payload.get("source", "")
    existing = payload.get("tags") or {}
    tags = tagger.build_payload_tags(
        content,
        rel_path=source,
        llm=True,
        zero_shot=False,
        project_hint=source.split("/", 1)[0] if "/" in source else "",
        workspace=workspace,
    )
    kind = tags.pop("_llm_type", "")
    for key in ("lang", "layer", "kind"):
        if existing.get(key) and not tags.get(key):
            tags[key] = existing[key]
    return tags, kind


def _next_page(client, coll: str, flt) -> list:
    """The newest memories still on the backlog. Ordering is a preference:
    a memory without a timestamp, which ordering would skip, comes after."""
    from qdrant_client import models as qm
    fields = ["content", "source", "tags"]
    points, _ = client.scroll(collection_name=coll, scroll_filter=flt, limit=_PAGE, with_payload=fields,
                              with_vectors=False,
                              order_by=qm.OrderBy(key="timestamp", direction=qm.Direction.DESC))
    if not points:
        points, _ = client.scroll(collection_name=coll, scroll_filter=flt, limit=_PAGE, with_payload=fields,
                                  with_vectors=False)
    return points


def drain(
    workspace: str | None = None,
    *,
    project: str = "",
    should_stop: Callable[[], bool] = lambda: False,
    on_tagged: Callable[[int], None] | None = None,
) -> tuple[int, bool]:
    """Tag one workspace's backlog, newest first. Returns ``(tagged, stopped)``.

    ``should_stop`` is asked before each memory; a classification once begun
    is written. Each memory's tags, type, ``tagged_at`` (ADR 0051) and the
    cleared marker are one payload update.
    """
    from . import vectorstore as vs
    client, coll = vs._ensure_collection(workspace)
    flt = _pending_filter(project)
    tagged = 0
    logged = 0
    seen: set = set()
    stopped = False
    try:
        while not stopped:
            points = _next_page(client, coll, flt)
            if not points:
                break
            for point in points:
                if should_stop():
                    stopped = True
                    break
                if point.id in seen:
                    raise RuntimeError(f"memory {point.id} stayed on the tagging backlog after it was tagged")
                seen.add(point.id)
                tags, kind = classify(point.payload or {}, workspace)
                try:
                    vs.set_classifications(client, coll, [(point.id, tags, kind)])
                except Exception:
                    # A memory replaced or deleted while it was classified is
                    # no longer on the backlog; anything else is a real failure.
                    if client.retrieve(collection_name=coll, ids=[point.id], with_payload=False):
                        raise
                    continue
                tagged += 1
                if on_tagged:
                    on_tagged(tagged)
                if tagged - logged >= _WAL_EVERY:
                    # Payload updates change no point count: log them so readers
                    # of the data fingerprint (dashboard caches) see new tags.
                    vs._wal_log("llm_tag", workspace=workspace, count=tagged - logged)
                    logged = tagged
    finally:
        if tagged > logged:
            vs._wal_log("llm_tag", workspace=workspace, count=tagged - logged)
    return tagged, stopped


def report(stored: int) -> None:
    """Tell whoever ran a storing command where its LLM tagging went."""
    if stored <= 0 or not deferring():
        return
    try:
        waiting = pending_count()
    except Exception:
        return
    if waiting:
        print(f"  LLM tags: {waiting} memories wait for background tagging. The dashboard tags them "
              "while it runs; imprint retag --pending tags them now.")


# ── The dispatcher's background job ───────────────────────────


def run(job_id: str = "") -> int:
    """The background job: hold the lock as a preemptible holder, tag every
    workspace's backlog, yield when a job is queued. Returns the exit code."""
    from . import queue_lock
    from .progress import clear_progress
    descriptor = queue_lock.acquire(COMMAND, job_id, preemptible=True)
    if descriptor is None:
        print("Another job holds the queue; background tagging waits for it.", flush=True)
        return YIELDED
    try:
        return _tag_all()
    finally:
        clear_progress()
        queue_lock.release(descriptor)


def _tag_all() -> int:
    from . import queue, tagger
    from .progress import write_progress
    if not deferring():
        print("LLM tagging is off (tagger.llm); nothing to tag.", flush=True)
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
