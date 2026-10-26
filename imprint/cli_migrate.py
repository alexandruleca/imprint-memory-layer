"""Move or copy memories between workspaces — by project, topic, or source.

Scrolls points from source workspace's collection, upserts into target
workspace's collection **preserving the existing vectors** (no re-embedding),
then deletes from source unless ``--copy`` is given.  Copying curates a
workspace (for example one to share with ``imprint sync serve --workspace
WS2 --send-only``) without taking anything away from the source.  Also
copies the knowledge-graph facts about the migrated memories' sources.

Usage:
    python -m imprint.cli_migrate --from WS1 --to WS2 --project NAME [--copy] [--dry-run]
    python -m imprint.cli_migrate --from WS1 --to WS2 --topic TOPIC [--copy] [--dry-run]
    python -m imprint.cli_migrate --from WS1 --to WS2 --source KEY [--copy] [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time

from qdrant_client import models as qm

from . import config as _config, vectorstore


_SCROLL_BATCH = 100


def _build_filter(project: str, topic: str, source: str) -> qm.Filter:
    must: list = []
    if project:
        must.append(qm.FieldCondition(
            key="project", match=qm.MatchValue(value=project),
        ))
    if topic:
        must.append(qm.FieldCondition(
            key="tags.topics", match=qm.MatchValue(value=topic),
        ))
    if source:
        must.append(qm.FieldCondition(
            key="source", match=qm.MatchValue(value=source),
        ))
    return qm.Filter(must=must)


def _migrate_kg_facts(
    from_workspace: str,
    to_workspace: str,
    sources: set[str],
    prefix: str,
    dry_run: bool,
) -> int:
    """Copy the KG facts about the migrated memories from one DB to another.

    A fact goes along when its source is one of the migrated memories' source
    keys or, for a project, starts with '<project>/' (facts recorded under the
    project's name). Memories moved into a project keep the source key they
    were indexed under (ADR 0036), so the name prefix alone misses theirs.

    Facts the target already holds — same (subject, predicate, object,
    valid_from), the key sync dedups on — are skipped, so repeating a copy
    to refresh a curated workspace does not duplicate them.

    Returns count of facts copied (or would-copy if dry_run).  Source rows are
    NOT deleted — facts are usually additive and safe to keep as historical.
    """
    src_db = _config.graph_db_path(from_workspace)
    if not src_db.exists() or not (sources or prefix):
        return 0

    try:
        conn = sqlite3.connect(src_db)
        origin = 'recorded_device' if 'recorded_device' in {row[1] for row in conn.execute('PRAGMA table_info(facts)')} else "'{}'"
        cur = conn.execute(
            f"SELECT subject, predicate, object, valid_from, ended, source, {origin} "
            f"FROM facts WHERE source != ''",
        )
        rows = [row for row in cur.fetchall()
                if row[5] in sources or (prefix and row[5].startswith(prefix))]
        conn.close()
    except sqlite3.OperationalError:
        return 0

    if not rows:
        return 0

    dst = _config.graph_db_path(to_workspace)
    if dry_run and not dst.exists():
        return len(rows)
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
    dst_conn = sqlite3.connect(dst)
    try:
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
        if 'recorded_device' not in {row[1] for row in dst_conn.execute('PRAGMA table_info(facts)')}:
            dst_conn.execute("ALTER TABLE facts ADD COLUMN recorded_device TEXT NOT NULL DEFAULT '{}'")
        new_rows = [
            row for row in rows
            if dst_conn.execute(
                "SELECT 1 FROM facts WHERE subject=? AND predicate=? AND object=? AND valid_from=?",
                row[:4],
            ).fetchone() is None
        ]
        if not dry_run:
            dst_conn.executemany(
                "INSERT INTO facts (subject, predicate, object, valid_from, ended, source, recorded_device) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                new_rows,
            )
            dst_conn.commit()
    finally:
        dst_conn.close()
    return len(new_rows)


def migrate(
    from_workspace: str,
    to_workspace: str,
    project: str = "",
    topic: str = "",
    source: str = "",
    batch_size: int = 200,
    dry_run: bool = False,
    copy: bool = False,
) -> tuple[int, int, int]:
    """Move points matching filter from one workspace to another, or copy
    them (the source keeps its points) when ``copy`` is set.

    Returns (transferred, scanned, kg_facts_copied).
    """
    if not project and not topic and not source:
        raise ValueError("migrate requires --project, --topic, or --source")
    if from_workspace == to_workspace:
        raise ValueError("from_workspace and to_workspace must differ")
    for name in (from_workspace, to_workspace):
        error = _config.validate_workspace_name(name)
        if error:
            raise ValueError(f"invalid workspace {name!r}: {error}")
    if from_workspace not in _config.get_known_workspaces():
        raise ValueError(f"source workspace {from_workspace!r} does not exist")

    src_client, src_coll = vectorstore._ensure_collection(from_workspace)
    # A dry run changes nothing, not even the target. A real run makes the
    # target a listed workspace, so it can be served or switched to.
    tgt_client = tgt_coll = None
    if not dry_run:
        if _config.register_workspace(to_workspace):
            print(f"  Created workspace: {to_workspace}", file=sys.stderr)
        tgt_client, tgt_coll = vectorstore._ensure_collection(to_workspace)
    flt = _build_filter(project, topic, source)
    verb = "Copied" if copy else "Moved"

    moved = 0
    scanned = 0
    offset = None
    pending_points: list = []
    pending_ids: list = []
    sources: set[str] = {source} if source else set()
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
            if (pt.payload or {}).get("source"):
                sources.add(pt.payload["source"])

            if len(pending_points) >= batch_size:
                if not dry_run:
                    vectorstore._upsert_chunked(tgt_client, tgt_coll, pending_points)
                    if not copy:
                        src_client.delete(
                            collection_name=src_coll,
                            points_selector=qm.PointIdsList(points=pending_ids),
                        )
                moved += len(pending_points)
                elapsed = time.time() - t0
                rate = moved / elapsed if elapsed > 0 else 0
                print(
                    f"\r  {verb} {moved} / {scanned} scanned  ({rate:.1f}/s)",
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
            if not copy:
                src_client.delete(
                    collection_name=src_coll,
                    points_selector=qm.PointIdsList(points=pending_ids),
                )
        moved += len(pending_points)

    if not dry_run:
        from . import source_snapshots
        for metadata in source_snapshots.list_metadata(from_workspace):
            if metadata['source'] not in sources:
                continue
            snapshot = source_snapshots.get(metadata['source'], metadata['project'], from_workspace)
            status = source_snapshots.integrity(snapshot, to_workspace)
            # A topic/subset copy must never reveal the unselected remainder
            # of a conversation/file through its full-source snapshot.
            if status['missing_chunks'] == 0 and status['changed_chunks'] == 0:
                source_snapshots.publish(snapshot, to_workspace)
            if not copy:
                source_snapshots.remove(metadata['source'], metadata['project'], from_workspace)

    # KG facts — for project or source migration (topics aren't tracked in KG)
    kg_copied = 0
    if project or source:
        kg_copied = _migrate_kg_facts(
            from_workspace, to_workspace, sources, f"{project}/" if project else "", dry_run,
        )

    elapsed = time.time() - t0
    print(file=sys.stderr)
    if dry_run:
        summary = (f"  Dry run: would {'copy' if copy else 'move'} {moved} of {scanned} scanned "
                   f"+ {kg_copied} new KG facts; nothing was changed [DRY RUN]")
    else:
        kept = "source unchanged" if copy else "removed from source"
        summary = (f"  Done: {moved} {verb.lower()} of {scanned} scanned ({kept}) "
                   f"({elapsed:.1f}s) + {kg_copied} new KG facts")
    print(summary, file=sys.stderr)
    return moved, scanned, kg_copied


def main():
    parser = argparse.ArgumentParser(
        description="Move or copy memories (by project, topic or source) between workspaces",
    )
    parser.add_argument("--from", dest="from_ws", required=True,
                        help="Source workspace")
    parser.add_argument("--to", dest="to_ws", required=True,
                        help="Target workspace")
    parser.add_argument("--project", default="", help="Filter by project name")
    parser.add_argument("--topic", default="", help="Filter by topic tag")
    parser.add_argument("--source", default="", help="Filter by exact source key")
    parser.add_argument("--batch-size", type=int, default=200,
                        help="Points per upsert/delete batch")
    parser.add_argument("--copy", action="store_true",
                        help="Copy instead of move: the source workspace keeps its memories")
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan but don't change anything")
    args = parser.parse_args()

    if not args.project and not args.topic and not args.source:
        print("error: must provide --project, --topic, or --source", file=sys.stderr)
        sys.exit(2)

    if args.project:
        what = f"project={args.project}"
    elif args.topic:
        what = f"topic={args.topic}"
    else:
        what = f"source={args.source}"
    print(
        f"\n  {'Copying' if args.copy else 'Moving'} {what}: {args.from_ws} → {args.to_ws}",
        file=sys.stderr,
    )

    try:
        migrate(
            from_workspace=args.from_ws,
            to_workspace=args.to_ws,
            project=args.project,
            topic=args.topic,
            source=args.source,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            copy=args.copy,
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
