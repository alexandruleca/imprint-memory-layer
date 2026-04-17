"""Re-tag existing memories with the current tagger pipeline.

Scrolls all chunks (or filtered by project/workspace), runs
``build_payload_tags`` with LLM tagging, and updates ``tags`` + ``topics``
in-place via Qdrant ``set_payload``.

By default skips chunks already marked ``llm_tagged: True`` — re-running
retag is idempotent.  Use ``--all`` to force re-tagging of everything.

Usage:
    python -m imprint.cli_retag [--project NAME] [--workspace NAME]
                                [--batch-size N] [--dry-run] [--all]
"""

from __future__ import annotations

import argparse
import sys
import time

from qdrant_client import models as qm

from . import tagger, vectorstore
from .config_schema import resolve


_SCROLL_BATCH = 100


def _build_scroll_filter(
    project: str = "",
    include_tagged: bool = False,
) -> qm.Filter | None:
    must: list = []
    must_not: list = []
    if project:
        must.append(qm.FieldCondition(
            key="project", match=qm.MatchValue(value=project),
        ))
    if not include_tagged:
        must_not.append(qm.FieldCondition(
            key="llm_tagged", match=qm.MatchValue(value=True),
        ))
    if not must and not must_not:
        return None
    return qm.Filter(must=must or None, must_not=must_not or None)


def retag(
    project: str = "",
    workspace: str | None = None,
    batch_size: int = 50,
    dry_run: bool = False,
    all_tagged: bool = False,
) -> tuple[int, int]:
    """Re-tag memories.  Returns (updated, total_scanned).

    When ``all_tagged`` is False (default), skips chunks already marked
    ``llm_tagged: True`` so retag is idempotent across runs.
    """
    client, coll = vectorstore._ensure_collection(workspace)
    scroll_filter = _build_scroll_filter(project, include_tagged=all_tagged)

    updated = 0
    scanned = 0
    offset = None
    batch_updates: list[tuple[str, dict, str]] = []  # (point_id, new_tags, new_type)

    t0 = time.time()

    while True:
        points, offset = client.scroll(
            collection_name=coll,
            limit=_SCROLL_BATCH,
            offset=offset,
            scroll_filter=scroll_filter,
            with_payload=["content", "source", "tags"],
            with_vectors=False,
        )

        for pt in points:
            scanned += 1
            content = pt.payload.get("content", "")
            source = pt.payload.get("source", "")

            # Re-derive tags with LLM enabled (unified classification)
            proj_hint = source.split("/", 1)[0] if "/" in source else ""
            new_tags = tagger.build_payload_tags(
                content,
                rel_path=source,
                llm=True,
                project_hint=proj_hint,
            )
            new_type = new_tags.pop("_llm_type", "")

            batch_updates.append((pt.id, new_tags, new_type))

            if len(batch_updates) >= batch_size:
                if not dry_run:
                    _flush_updates(client, coll, batch_updates)
                updated += len(batch_updates)
                elapsed = time.time() - t0
                rate = updated / elapsed if elapsed > 0 else 0
                print(
                    f"\r  Retagged {updated} / {scanned} scanned"
                    f"  ({rate:.1f}/s)",
                    end="", file=sys.stderr, flush=True,
                )
                batch_updates = []

        if offset is None:
            break

    # Flush remaining
    if batch_updates:
        if not dry_run:
            _flush_updates(client, coll, batch_updates)
        updated += len(batch_updates)

    elapsed = time.time() - t0
    print(file=sys.stderr)  # newline
    mode = " [--all]" if all_tagged else ""
    print(
        f"  Done: {updated} retagged out of {scanned} scanned"
        f"  ({elapsed:.1f}s){mode}{' [DRY RUN]' if dry_run else ''}",
        file=sys.stderr,
    )
    return updated, scanned


def _flush_updates(
    client, coll: str, updates: list[tuple[str, dict, str]]
) -> None:
    """Batch-update tags + type payloads via set_payload.

    Stamps ``llm_tagged: True`` whenever the LLM produced a type (i.e., the
    LLM classification succeeded) so future retag runs can skip this point.
    """
    for point_id, new_tags, new_type in updates:
        payload: dict = {"tags": new_tags}
        if new_type:
            payload["type"] = new_type
            payload["llm_tagged"] = True
        client.set_payload(
            collection_name=coll,
            payload=payload,
            points=[point_id],
        )


def main():
    parser = argparse.ArgumentParser(
        description="Re-tag existing memories with current tagger pipeline",
    )
    parser.add_argument("--project", default="", help="Filter by project name")
    parser.add_argument("--workspace", default=None, help="Target workspace")
    parser.add_argument("--batch-size", type=int, default=50, help="Flush every N updates")
    parser.add_argument("--dry-run", action="store_true", help="Scan and tag but don't write")
    parser.add_argument(
        "--all", dest="all_tagged", action="store_true",
        help="Re-tag everything, even chunks already marked llm_tagged",
    )
    args = parser.parse_args()

    provider = tagger._get_llm_provider()
    mode = "all chunks" if args.all_tagged else "untagged chunks only"
    print(f"\n  Retag using provider: {provider} ({mode})", file=sys.stderr)

    retag(
        project=args.project,
        workspace=args.workspace,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        all_tagged=args.all_tagged,
    )


if __name__ == "__main__":
    main()
