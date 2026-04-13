import gc
import hashlib
import json
import os
import time
from pathlib import Path

import lancedb
import pyarrow as pa

from . import config, embeddings

_db = None
_table = None
_id_cache: set[str] | None = None

# Coalesce small LanceDB fragments every N inserts. Each store_batch() call
# creates a fragment; after thousands of small batches the table holds many
# tiny files that pyarrow buffers in memory on every scan. Compacting keeps
# both peak RSS and search latency in check.
_COMPACT_EVERY = int(os.environ.get("KNOWLEDGE_COMPACT_EVERY", "200"))
_inserts_since_compact = 0
# Stream scans in arrow batches of this size instead of materializing the
# whole table — used by recent(), get_source_mtimes(), status(), etc.
_SCAN_BATCH = int(os.environ.get("KNOWLEDGE_SCAN_BATCH", "2000"))


def _get_table():
    global _db, _table
    if _table is not None:
        return _table

    data_dir = config.get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    _db = lancedb.connect(str(data_dir / "lance"))

    schema = pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("content", pa.string()),
            pa.field("project", pa.string()),
            pa.field("type", pa.string()),
            pa.field("tags", pa.string()),
            pa.field("source", pa.string()),
            pa.field("chunk_index", pa.int32()),
            pa.field("source_mtime", pa.float64()),
            pa.field("timestamp", pa.float64()),
            pa.field("vector", pa.list_(pa.float32(), config.EMBEDDING_DIM)),
        ]
    )

    if config.MEMORIES_TABLE in _db.table_names():
        _table = _db.open_table(config.MEMORIES_TABLE)
    else:
        _table = _db.create_table(config.MEMORIES_TABLE, schema=schema)

    return _table


def _stream_batches(columns: list[str]):
    """Yield pyarrow record batches for the given columns without materializing
    the whole table. Uses LanceQueryBuilder.to_batches() which returns a
    streaming RecordBatchReader (verified to peak at ~0.1MB for 8k rows)."""
    table = _get_table()
    if table.count_rows() == 0:
        return
    reader = table.search().select(columns).to_batches(batch_size=_SCAN_BATCH)
    for batch in reader:
        yield batch
        del batch


def _get_existing_ids() -> set[str]:
    """Load all existing IDs into memory once. Fast duplicate check.

    Streams arrow batches so we never hold the full id column twice in memory
    (raw arrow + python set). The set itself is small (16-byte ids).
    """
    global _id_cache
    if _id_cache is not None:
        return _id_cache
    _id_cache = set()
    try:
        for batch in _stream_batches(["id"]):
            _id_cache.update(batch.column("id").to_pylist())
    except Exception:
        pass
    return _id_cache


def _make_id(content: str, project: str = "", source: str = "") -> str:
    """Deterministic ID from project + source + content prefix.
    Same content in different projects gets different IDs."""
    key = f"{project}:{source}:{content.strip()[:200]}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _wal_log(operation: str, **kwargs):
    """Write-ahead log — append operation to wal.jsonl before executing."""
    wal_path = config.get_data_dir() / "wal.jsonl"
    entry = {"ts": time.time(), "op": operation, **kwargs}
    try:
        with open(wal_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # WAL failure shouldn't block operations


def store(
    content: str,
    project: str = "",
    type: str = "",
    tags: str = "",
    source: str = "",
    chunk_index: int = 0,
    source_mtime: float = 0.0,
) -> str:
    """Store a memory. Returns the ID."""
    table = _get_table()
    memory_id = _make_id(content, project, source)

    # Check for duplicate using cached ID set
    if memory_id in _get_existing_ids():
        return memory_id  # already exists — no insert

    _wal_log("store", id=memory_id, project=project, source=source, type=type)

    vector = embeddings.embed_document(content)

    table.add(
        [
            {
                "id": memory_id,
                "content": content,
                "project": project,
                "type": type,
                "tags": tags,
                "source": source,
                "chunk_index": chunk_index,
                "source_mtime": source_mtime,
                "timestamp": time.time(),
                "vector": vector,
            }
        ]
    )
    _get_existing_ids().add(memory_id)
    return memory_id


def store_batch(records: list[dict]) -> tuple[int, int]:
    """Store multiple records in one batch. Much faster than individual store() calls.

    Each record: {content, project, type, tags, source, chunk_index, source_mtime}
    Returns (inserted, skipped).
    """
    table = _get_table()
    existing = _get_existing_ids()

    # Filter out duplicates
    new_records = []
    for r in records:
        mid = _make_id(r["content"], r.get("project", ""), r.get("source", ""))
        if mid not in existing:
            r["_id"] = mid
            new_records.append(r)

    if not new_records:
        return 0, len(records)

    # Batch embed all at once
    texts = [r["content"] for r in new_records]
    vectors = embeddings.embed_documents_batch(texts)

    # Build rows for LanceDB
    rows = []
    for r, vec in zip(new_records, vectors):
        rows.append({
            "id": r["_id"],
            "content": r["content"],
            "project": r.get("project", ""),
            "type": r.get("type", ""),
            "tags": r.get("tags", ""),
            "source": r.get("source", ""),
            "chunk_index": r.get("chunk_index", 0),
            "source_mtime": r.get("source_mtime", 0.0),
            "timestamp": time.time(),
            "vector": vec,
        })

    # Batch insert
    table.add(rows)

    # Update cache
    for r in rows:
        existing.add(r["id"])

    _wal_log("store_batch", count=len(rows))

    # Free the per-batch vectors + row dicts before returning. These hold
    # 768 floats × N rows; on a long ingest letting them linger across calls
    # adds up.
    inserted = len(rows)
    del rows, vectors, texts, new_records
    gc.collect()

    _maybe_compact(inserted)

    return inserted, len(records) - inserted


def _maybe_compact(just_inserted: int) -> None:
    """Periodically coalesce LanceDB fragments to keep memory + scans cheap.

    LanceDB writes one fragment per `table.add` call. After many small
    batches, every scan has to open and merge hundreds of fragments — that
    work happens in pyarrow buffers and inflates RSS even when we're only
    reading a few columns. compact_files() rewrites them into larger files.
    """
    global _inserts_since_compact
    if just_inserted <= 0:
        return
    _inserts_since_compact += just_inserted
    if _inserts_since_compact < _COMPACT_EVERY:
        return
    _inserts_since_compact = 0
    try:
        _get_table().compact_files()
    except Exception:
        # Old lancedb versions don't have compact_files; safe to skip.
        pass


def search(
    query: str,
    limit: int = 10,
    project: str = "",
    type: str = "",
) -> list[dict]:
    """Semantic search. Returns results with normalized 0-1 similarity (higher = better)."""
    table = _get_table()

    if table.count_rows() == 0:
        return []

    vector = embeddings.embed_query(query)
    q = table.search(vector).limit(limit)

    filters = []
    if project:
        filters.append(f"project = '{project}'")
    if type:
        filters.append(f"type = '{type}'")
    if filters:
        q = q.where(" AND ".join(filters))

    results = q.to_list()
    return [
        {
            "id": r["id"],
            "content": r["content"],
            "project": r["project"],
            "type": r["type"],
            "tags": r["tags"],
            "source": r["source"],
            "chunk_index": r.get("chunk_index", 0),
            "similarity": round(max(0, 1 - float(r.get("_distance", 1))), 3),
        }
        for r in results
    ]


def delete(memory_id: str) -> bool:
    """Delete a memory by ID."""
    table = _get_table()
    try:
        _wal_log("delete", id=memory_id)
        table.delete(f"id = '{memory_id}'")
        return True
    except Exception:
        return False


def delete_by_source(source: str) -> bool:
    """Delete all memories with a given source."""
    table = _get_table()
    try:
        _wal_log("delete_by_source", source=source)
        table.delete(f"source = '{source}'")
        return True
    except Exception:
        return False


def get_source_mtimes() -> dict[str, float]:
    """Get stored source paths and their mtimes. Used by refresh.

    Streams in arrow batches — never materializes the full table.
    """
    table = _get_table()
    if table.count_rows() == 0:
        return {}
    stored: dict[str, float] = {}
    try:
        for batch in _stream_batches(["source", "source_mtime"]):
            sources = batch.column("source").to_pylist()
            mtimes = batch.column("source_mtime").to_pylist()
            for source, mtime in zip(sources, mtimes):
                if source and mtime:
                    stored[source] = mtime
            del sources, mtimes
    except Exception:
        pass
    return stored


def recent(limit: int = 15, types: list[str] | None = None) -> list[dict]:
    """Get most recent memories, optionally filtered by type. For L1 wake_up.

    Streams arrow batches and keeps only a top-K heap of (timestamp, row).
    Memory stays bounded at ~limit rows regardless of table size.
    """
    import heapq

    table = _get_table()
    if table.count_rows() == 0:
        return []

    cols = ["id", "content", "project", "type", "tags", "source", "timestamp"]
    type_set = set(types) if types else None
    heap: list[tuple[float, int, dict]] = []  # min-heap by timestamp
    counter = 0

    try:
        for batch in _stream_batches(cols):
            rows = batch.to_pylist()
            for r in rows:
                if type_set and r.get("type", "") not in type_set:
                    continue
                ts = r.get("timestamp") or 0
                counter += 1
                if len(heap) < limit:
                    heapq.heappush(heap, (ts, counter, r))
                elif ts > heap[0][0]:
                    heapq.heapreplace(heap, (ts, counter, r))
            del rows
    except Exception:
        return []

    heap.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "id": r["id"],
            "content": r["content"],
            "project": r["project"],
            "type": r["type"],
            "tags": r["tags"],
            "source": r["source"],
        }
        for _, _, r in heap
    ]


def status() -> dict:
    """Return storage statistics. Streams the project column instead of
    materializing the whole table — this used to be the OOM trigger at the
    end of `knowledge ingest`."""
    table = _get_table()
    total = table.count_rows()

    by_project: dict[str, int] = {}
    if total > 0:
        try:
            for batch in _stream_batches(["project"]):
                for p in batch.column("project").to_pylist():
                    key = p or "(none)"
                    by_project[key] = by_project.get(key, 0) + 1
        except Exception:
            by_project = {"(unknown)": total}

    return {
        "total_memories": total,
        "by_project": by_project,
    }
