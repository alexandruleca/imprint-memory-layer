"""Re-ingest a LanceDB dump JSONL into the new Qdrant store.

Usage: python -m knowledgebase.migrate_lance_import <path.jsonl>

Each line is a dict from the old schema:
  {id, content, project, type, tags, source, chunk_index, source_mtime, timestamp}

Tags are upgraded from the old comma-separated string to the new structured
payload via `tagger.build_payload_tags`. The original `source` is preserved
so follow-up `knowledge refresh` runs still deduplicate by mtime.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from knowledgebase import tagger, vectorstore as vs


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m knowledgebase.migrate_lance_import <path.jsonl>", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"file not found: {path}", file=sys.stderr)
        sys.exit(1)

    total = 0
    inserted = 0
    skipped = 0
    batch: list[dict] = []
    BATCH = 32
    t_start = time.time()
    last_print = t_start

    def flush():
        nonlocal inserted, skipped
        if not batch:
            return
        ins, sk = vs.store_batch(batch)
        inserted += ins
        skipped += sk
        batch.clear()

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            content = row.get("content", "")
            if not content:
                continue

            source = row.get("source", "")
            # Re-derive structured tags from content + path-like source. The
            # old string tag (e.g. "conversation", "auto") is kept as a domain
            # hint by feeding it through _normalize_tags if no better signal.
            rel_path = source.split("/", 1)[1] if "/" in source else source
            new_tags = tagger.build_payload_tags(content, rel_path=rel_path)
            legacy = (row.get("tags") or "").strip()
            if legacy and not any(legacy == d for d in new_tags["domain"]):
                new_tags["domain"] = new_tags["domain"] + [legacy]
            # Preserve conversation/session semantics from the source prefix
            if source.startswith("conversation/"):
                new_tags["lang"] = "conversation"
                new_tags["layer"] = "session"
                new_tags["kind"] = "qa"
            elif source == "auto-extract":
                new_tags["lang"] = "conversation"
                new_tags["layer"] = "session"
                new_tags["kind"] = "auto-extract"

            batch.append({
                "content": content,
                "project": row.get("project", ""),
                "type": row.get("type", ""),
                "tags": new_tags,
                "source": source,
                "chunk_index": row.get("chunk_index", 0),
                "source_mtime": row.get("source_mtime", 0.0),
            })
            if len(batch) >= BATCH:
                flush()

            now = time.time()
            if now - last_print > 2:
                elapsed = now - t_start
                rate = total / elapsed if elapsed else 0
                print(f"  {total} read  |  {inserted} inserted  |  {skipped} skipped  |  {rate:.0f}/s", flush=True)
                last_print = now

    flush()

    elapsed = time.time() - t_start
    print(f"\nDone: {total} rows read, {inserted} inserted, {skipped} skipped in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
