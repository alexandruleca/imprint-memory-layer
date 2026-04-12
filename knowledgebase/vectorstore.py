import hashlib
import json
import time
from pathlib import Path

import lancedb
import pyarrow as pa

from . import config, embeddings

_db = None
_table = None


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

    # Check for duplicate
    try:
        existing = table.search().where(f"id = '{memory_id}'").limit(1).to_list()
        if existing:
            return memory_id
    except Exception:
        pass

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
    return memory_id


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
    """Get stored source paths and their mtimes. Used by refresh."""
    table = _get_table()
    if table.count_rows() == 0:
        return {}
    stored = {}
    try:
        rows = (
            table.search()
            .limit(table.count_rows())
            .select(["source", "source_mtime"])
            .to_list()
        )
        for r in rows:
            source = r.get("source", "")
            mtime = r.get("source_mtime", 0)
            if source and mtime:
                stored[source] = mtime
    except Exception:
        pass
    return stored


def recent(limit: int = 15, types: list[str] | None = None) -> list[dict]:
    """Get most recent memories, optionally filtered by type. For L1 wake_up."""
    table = _get_table()
    if table.count_rows() == 0:
        return []

    try:
        fetch_limit = min(table.count_rows(), limit * 10)
        rows = table.search().limit(fetch_limit).select(
            ["id", "content", "project", "type", "tags", "source", "timestamp"]
        ).to_list()

        if types:
            rows = [r for r in rows if r.get("type", "") in types]

        rows.sort(key=lambda r: r.get("timestamp", 0), reverse=True)

        return [
            {
                "id": r["id"],
                "content": r["content"],
                "project": r["project"],
                "type": r["type"],
                "tags": r["tags"],
                "source": r["source"],
            }
            for r in rows[:limit]
        ]
    except Exception:
        return []


def status() -> dict:
    """Return storage statistics."""
    table = _get_table()
    total = table.count_rows()

    by_project = {}
    if total > 0:
        try:
            rows = table.search().limit(total).select(["project"]).to_list()
            for r in rows:
                p = r.get("project", "") or "(none)"
                by_project[p] = by_project.get(p, 0) + 1
        except Exception:
            by_project = {"(unknown)": total}

    return {
        "total_memories": total,
        "by_project": by_project,
    }
