"""``imprint scrub``: redact secrets already stored in a workspace (ADR 0060).

Memories and facts stored before redaction existed, restored from a snapshot
bundle, or redacted under fewer rules (an older version, Privacy Filter off)
can still hold secrets. Scrub runs today's redaction over every memory and
fact of one workspace. A dry run, the default, counts what it would redact by
kind; ``--apply`` rewrites them. A memory takes the ID of its redacted content
and a new embedding and keeps the rest of its payload: project, source, tags,
timestamps, ``tagged_at`` and device provenance. Secret values are never
printed.

Usage:
    python -m imprint.cli_scrub [--workspace NAME] [--apply]
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter

from qdrant_client import models as qm

from . import config, embeddings, imprint_graph as kg, vectorstore as vs
from ._cli_signals import install as _install_signals
from .progress import clear_progress, write_progress
from .redaction import kinds, redact_all

_BATCH = 64


def _added(before: list[str], after: list[str]) -> Counter:
    return sum((kinds(new) - kinds(old) for old, new in zip(before, after)), Counter())


def _scan_memories(client, coll: str, progress) -> tuple[list[tuple], Counter, int]:
    """Every memory the redaction changes, as (point id, redacted content),
    the kinds it redacts, and how many memories were scanned."""
    found, redacted, scanned, offset = [], Counter(), 0, None
    while True:
        points, offset = client.scroll(collection_name=coll, limit=_BATCH, offset=offset,
                                       with_payload=["content"], with_vectors=False)
        before = [(point.payload or {}).get("content", "") for point in points]
        after = redact_all(before)
        found.extend((point.id, new) for point, old, new in zip(points, before, after) if new != old)
        redacted += _added(before, after)
        scanned += len(points)
        progress(scanned, len(found))
        if offset is None:
            return found, redacted, scanned


def _rewrite_memories(client, coll: str, found: list[tuple], workspace: str) -> None:
    """Store each memory under its redacted content, then drop the old point.
    Interrupted, the old point stays and the next scrub finishes it."""
    for start in range(0, len(found), _BATCH):
        contents = dict(found[start:start + _BATCH])
        points = client.retrieve(collection_name=coll, ids=list(contents), with_payload=True, with_vectors=False)
        vectors = embeddings.embed_documents_batch([contents[point.id] for point in points])
        if len(vectors) != len(points):
            raise ValueError("Embedding batch returned an incomplete result; those memories were left as they were")
        rewritten = []
        for point, vector in zip(points, vectors):
            payload, content = point.payload or {}, contents[point.id]
            memory_id = vs.relabeled_id(payload.get("_mid") or "", payload, content=content)
            rewritten.append(qm.PointStruct(id=vs._point_uuid(memory_id), vector={config.QDRANT_VECTOR_NAME: vector},
                                            payload={**payload, "_mid": memory_id, "content": content}))
        vs._persist_points(client, coll, rewritten, workspace)
        kept = {point.id for point in rewritten}
        client.delete(collection_name=coll, wait=True, points_selector=qm.PointIdsList(
            points=[point.id for point in points if point.id not in kept]))


def _scrub_facts(workspace: str, apply: bool) -> tuple[int, int, Counter]:
    conn = kg._get_conn(workspace)
    rows = conn.execute("SELECT id, subject, predicate, object FROM facts").fetchall()
    before = [text for row in rows for text in (row["subject"], row["predicate"], row["object"])]
    after = redact_all(before)
    changed = [(*after[3 * i:3 * i + 3], row["id"]) for i, row in enumerate(rows)
               if after[3 * i:3 * i + 3] != before[3 * i:3 * i + 3]]
    if apply and changed:
        with conn:
            conn.executemany("UPDATE facts SET subject=?, predicate=?, object=? WHERE id=?", changed)
    return len(rows), len(changed), _added(before, after)


def scrub(workspace: str | None = None, apply: bool = False) -> dict:
    """Count, and with ``apply`` redact, the secrets stored in ``workspace``."""
    from .workspace_policy import workspace_name
    ws = workspace_name(workspace)
    client, coll = vs._ensure_collection(ws)
    total = client.count(collection_name=coll, exact=False).count
    started = last = time.time()

    def progress(scanned: int, found: int) -> None:
        nonlocal last
        if time.time() - last >= 1.0 or scanned >= total:
            last = time.time()
            write_progress("scrub", scanned, max(total, scanned), found, 0, started, [], phase="scanning")

    try:
        found, memory_kinds, scanned = _scan_memories(client, coll, progress)
        if apply and found:
            write_progress("scrub", scanned, scanned, len(found), 0, started, [], phase="embedding")
            _rewrite_memories(client, coll, found, ws)
        facts, changed_facts, fact_kinds = _scrub_facts(ws, apply)
    finally:
        clear_progress()
    if apply:
        with vs._cache_lock:
            vs._id_caches.pop(coll, None)
        vs._wal_log("scrub", workspace=ws, memories=len(found), facts=changed_facts)
    return {"workspace": ws, "applied": apply,
            "memories": {"scanned": scanned, "redacted": len(found)},
            "facts": {"scanned": facts, "redacted": changed_facts},
            "kinds": dict(sorted((memory_kinds + fact_kinds).items()))}


def summary(result: dict) -> str:
    memories, facts = result["memories"], result["facts"]
    lines = [f"  Workspace {result['workspace']}: {memories['redacted']} of {memories['scanned']} memories "
             f"and {facts['redacted']} of {facts['scanned']} facts hold secrets."]
    width = max((len(kind) for kind in result["kinds"]), default=0)
    lines += [f"    {kind:<{width}}  {count}" for kind, count in result["kinds"].items()]
    if not memories["redacted"] and not facts["redacted"]:
        return lines[0]
    lines.append("  Redacted; changed memories were embedded again." if result["applied"] else
                 "  Dry run: nothing changed. Run 'imprint scrub --apply' to redact them.")
    return "\n".join(lines)


def main() -> None:
    _install_signals()
    parser = argparse.ArgumentParser(prog="imprint scrub",
                                     description="Find secrets stored in a workspace's memories and facts. "
                                                 "Previews unless --apply.")
    parser.add_argument("--workspace", default=None, help="Workspace to scrub (default: the active one)")
    parser.add_argument("--apply", action="store_true", help="Redact them and embed changed memories again")
    args = parser.parse_args()
    print(summary(scrub(args.workspace, apply=args.apply)))


if __name__ == "__main__":
    main()
