"""Move memories between workspaces — by project or by topic.

Scrolls points from source workspace's collection, upserts into target
workspace's collection **preserving the existing vectors** (no re-embedding),
then deletes from source.  Optionally migrates knowledge-graph facts that
share the same project tag.

Usage:
    python -m imprint.cli_migrate --from WS1 --to WS2 --project NAME [--dry-run]
    python -m imprint.cli_migrate --from WS1 --to WS2 --topic TOPIC [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time

from qdrant_client import models as qm

from . import config as _config, vectorstore


_SCROLL_BATCH = 100


def _build_filter(project: str, topic: str) -> qm.Filter:
    must: list = []
    if project:
        must.append(qm.FieldCondition(
            key="project", match=qm.MatchValue(value=project),
        ))
    if topic:
        must.append(qm.FieldCondition(
            key="tags.topics", match=qm.MatchValue(value=topic),
        ))
    return qm.Filter(must=must)


def _migrate_kg_facts(
    from_workspace: str,
    to_workspace: str,
    project: str,
    dry_run: bool,
) -> int:
    """Copy KG facts whose source starts with '<project>/' from one DB to another.

    Returns count of facts copied (or would-copy if dry_run).  Source rows are
    NOT deleted — facts are usually additive and safe to keep as historical.
    """
    src = _config.graph_db_path(from_workspace)
    if not src.exists():
        return 0

    try:
        conn = sqlite3.connect(src)
        cur = conn.execute(
            "SELECT subject, predicate, object, valid_from, ended, source "
            "FROM facts WHERE source LIKE ?",
            (f"{project}/%",),
        )
        rows = cur.fetchall()
        conn.close()
    except sqlite3.OperationalError:
        return 0

    if not rows or dry_run:
        return len(rows)

    dst = _config.graph_db_path(to_workspace)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst_conn = sqlite3.connect(dst)
    dst_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            valid_from REAL NOT NULL,
            ended REAL,
            source TEXT DEFAULT ''
        )
        """
    )
    dst_conn.executemany(
        "INSERT INTO facts (subject, predicate, object, valid_from, ended, source) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    dst_conn.commit()
    dst_conn.close()
    return len(rows)


def migrate(
    from_workspace: str,
    to_workspace: str,
    project: str = "",
    topic: str = "",
    batch_size: int = 200,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    """Move points matching filter from one workspace to another.

    Returns (moved, scanned, kg_facts_copied).
    """
    if not project and not topic:
        raise ValueError("migrate requires --project or --topic")
    if from_workspace == to_workspace:
        raise ValueError("from_workspace and to_workspace must differ")

    src_client, src_coll = vectorstore._ensure_collection(from_workspace)
    tgt_client, tgt_coll = vectorstore._ensure_collection(to_workspace)
    flt = _build_filter(project, topic)

    moved = 0
    scanned = 0
    offset = None
    pending_points: list = []
    pending_ids: list = []
    t0 = time.time()

    while True:
        points, offset = src_client.scroll(
            collection_name=src_coll,
            limit=_SCROLL_BATCH,
            offset=offset,
            scroll_filter=flt,
            with_payload=True,
            with_vectors=True,
        )

        for pt in points:
            scanned += 1
            vec = pt.vector
            if isinstance(vec, dict):
                # Named vector dict — pass through verbatim.
                new_vec = vec
            else:
                new_vec = {_config.QDRANT_VECTOR_NAME: vec}
            pending_points.append(
                qm.PointStruct(id=pt.id, vector=new_vec, payload=pt.payload or {})
            )
            pending_ids.append(pt.id)

            if len(pending_points) >= batch_size:
                if not dry_run:
                    vectorstore._upsert_chunked(tgt_client, tgt_coll, pending_points)
                    src_client.delete(
                        collection_name=src_coll,
                        points_selector=qm.PointIdsList(points=pending_ids),
                    )
                moved += len(pending_points)
                elapsed = time.time() - t0
                rate = moved / elapsed if elapsed > 0 else 0
                print(
                    f"\r  Moved {moved} / {scanned} scanned  ({rate:.1f}/s)",
                    end="", file=sys.stderr, flush=True,
                )
                pending_points = []
                pending_ids = []

        if offset is None:
            break

    # Flush remaining
    if pending_points:
        if not dry_run:
            vectorstore._upsert_chunked(tgt_client, tgt_coll, pending_points)
            src_client.delete(
                collection_name=src_coll,
                points_selector=qm.PointIdsList(points=pending_ids),
            )
        moved += len(pending_points)

    # KG facts — only for project migration (topics aren't tracked in KG)
    kg_copied = 0
    if project:
        kg_copied = _migrate_kg_facts(from_workspace, to_workspace, project, dry_run)

    elapsed = time.time() - t0
    print(file=sys.stderr)
    print(
        f"  Done: {moved} moved of {scanned} scanned  "
        f"({elapsed:.1f}s) + {kg_copied} KG facts"
        f"{' [DRY RUN]' if dry_run else ''}",
        file=sys.stderr,
    )
    return moved, scanned, kg_copied


def main():
    parser = argparse.ArgumentParser(
        description="Migrate memories (by project or topic) between workspaces",
    )
    parser.add_argument("--from", dest="from_ws", required=True,
                        help="Source workspace")
    parser.add_argument("--to", dest="to_ws", required=True,
                        help="Target workspace")
    parser.add_argument("--project", default="", help="Filter by project name")
    parser.add_argument("--topic", default="", help="Filter by topic tag")
    parser.add_argument("--batch-size", type=int, default=200,
                        help="Points per upsert/delete batch")
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan but don't move anything")
    args = parser.parse_args()

    if not args.project and not args.topic:
        print("error: must provide --project or --topic", file=sys.stderr)
        sys.exit(2)

    what = f"project={args.project}" if args.project else f"topic={args.topic}"
    print(
        f"\n  Migrating {what}: {args.from_ws} → {args.to_ws}",
        file=sys.stderr,
    )

    migrate(
        from_workspace=args.from_ws,
        to_workspace=args.to_ws,
        project=args.project,
        topic=args.topic,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
