"""Qdrant-backed vector store.

Embedded mode: `QdrantClient(path=...)` uses on-disk local storage, no server.

Collection schema:
  - vector: named "dense", f32[EMBEDDING_DIM], cosine distance
  - payload: {content, project, type, tags, source, chunk_index, source_mtime,
              timestamp}
    where tags = {lang, layer, kind, domain: [...], topics: [...]}
  - payload indexes on project, type, source, source_mtime, tags.lang,
    tags.domain (keyword list), tags.topics (keyword list) for cheap filtering

Public API (kept stable for callers):
  store, store_batch, search, delete, delete_by_source, get_source_mtimes,
  recent, status, _get_existing_ids
"""

from __future__ import annotations

import atexit
import gc
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Iterable

from qdrant_client import QdrantClient, models as qm
from qdrant_client.http.exceptions import UnexpectedResponse

from . import config, embeddings, qdrant_runner

_client: QdrantClient | None = None
_id_cache: set[str] | None = None
_collection_ready = False

_inserts_since_compact = 0
_COMPACT_EVERY = int(os.environ.get("KNOWLEDGE_COMPACT_EVERY", "500"))
_SCAN_BATCH = int(os.environ.get("KNOWLEDGE_SCAN_BATCH", "2000"))

# Idle close: when set, the client is released after this many seconds of
# inactivity so other processes (`knowledge ingest`) can grab the on-disk
# lock. The MCP server enables it via release_idle_client(); CLI ingest
# leaves it disabled (long-running session, never wants to reconnect).
_idle_close_secs: float | None = None
_last_use_ts: float = 0.0
_idle_thread = None
_idle_lock = None


# ── Client + schema ─────────────────────────────────────────────
def _get_client() -> QdrantClient:
    """Connect to the local Qdrant server. The runner auto-spawns it
    (downloading the binary on first use) if nothing is listening on the
    configured host:port. HTTP server mode supports unlimited concurrent
    clients, so MCP + CLI ingest + hooks coexist without lock contention.

    To use a managed/remote server instead, set KNOWLEDGE_QDRANT_HOST and
    KNOWLEDGE_QDRANT_NO_SPAWN=1 to skip the auto-spawn.
    """
    global _client
    if _client is not None:
        return _client
    host, port = qdrant_runner.ensure_running()
    _client = QdrantClient(host=host, port=port, prefer_grpc=False, timeout=30.0)
    return _client


def _close_client() -> None:
    """Close Qdrant client deterministically at interpreter exit. Without
    this, QdrantClient.__del__ fires during shutdown when sys.meta_path is
    already None and prints a noisy ImportError traceback."""
    global _client, _collection_ready, _id_cache
    if _client is None:
        return
    try:
        _client.close()
    except Exception:
        pass
    _client = None
    _collection_ready = False
    _id_cache = None


atexit.register(_close_client)


def _touch_use() -> None:
    """Record activity. Idle thread checks this to decide when to close."""
    global _last_use_ts
    _last_use_ts = time.time()


def release_idle_client(after_seconds: float = 30.0) -> None:
    """Enable idle auto-close. After `after_seconds` of no vectorstore
    activity, release the on-disk Qdrant lock so other processes
    (e.g. `knowledge ingest`) can take it. Intended for the MCP server,
    which sits idle most of the time between user actions.

    Safe to call multiple times — only one watcher thread runs.
    """
    global _idle_close_secs, _idle_thread, _idle_lock
    import threading

    _idle_close_secs = float(after_seconds)
    if _idle_thread is not None:
        return
    _idle_lock = threading.Lock()

    def _watch():
        while True:
            time.sleep(max(1.0, _idle_close_secs / 3.0))
            if _idle_close_secs is None:
                return
            with _idle_lock:
                if _client is None:
                    continue
                if time.time() - _last_use_ts >= _idle_close_secs:
                    _close_client()

    _idle_thread = threading.Thread(target=_watch, daemon=True, name="kb-idle-close")
    _idle_thread.start()


def _ensure_collection() -> QdrantClient:
    global _collection_ready
    client = _get_client()
    _touch_use()
    if _collection_ready:
        return client

    collections = {c.name for c in client.get_collections().collections}
    if config.QDRANT_COLLECTION not in collections:
        try:
            client.create_collection(
                collection_name=config.QDRANT_COLLECTION,
                vectors_config={
                    config.QDRANT_VECTOR_NAME: qm.VectorParams(
                        size=config.EMBEDDING_DIM,
                        distance=qm.Distance.COSINE,
                        on_disk=True,
                    )
                },
                quantization_config=qm.ScalarQuantization(
                    scalar=qm.ScalarQuantizationConfig(
                        type=qm.ScalarType.INT8,
                        always_ram=True,
                    )
                ),
                on_disk_payload=True,
                hnsw_config=qm.HnswConfigDiff(m=16, ef_construct=128),
            )
        except UnexpectedResponse as e:
            # 409 = another concurrent process won the create race. Fine —
            # the collection now exists either way.
            if getattr(e, "status_code", None) != 409 and "already exists" not in str(e):
                raise

    # Payload indexes — server mode honors these and uses them to skip
    # full payload scans on filtered queries. Big speedup for tag-narrow
    # searches (lang/domain/etc) once the collection grows past ~10k pts.
    # create_payload_index is idempotent in Qdrant, safe to call every init.
    for field, kind in [
        ("project", qm.PayloadSchemaType.KEYWORD),
        ("type", qm.PayloadSchemaType.KEYWORD),
        ("source", qm.PayloadSchemaType.KEYWORD),
        ("source_mtime", qm.PayloadSchemaType.FLOAT),
        ("tags.lang", qm.PayloadSchemaType.KEYWORD),
        ("tags.layer", qm.PayloadSchemaType.KEYWORD),
        ("tags.kind", qm.PayloadSchemaType.KEYWORD),
        ("tags.domain", qm.PayloadSchemaType.KEYWORD),
        ("tags.topics", qm.PayloadSchemaType.KEYWORD),
    ]:
        try:
            client.create_payload_index(
                collection_name=config.QDRANT_COLLECTION,
                field_name=field,
                field_schema=kind,
            )
        except Exception:
            pass

    _collection_ready = True
    return client


# ── ID + WAL helpers ────────────────────────────────────────────
def _make_id(content: str, project: str = "", source: str = "") -> str:
    """Deterministic 16-hex id from project+source+content prefix. Same content
    in different projects gets different ids."""
    key = f"{project}:{source}:{content.strip()[:200]}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _point_uuid(memory_id: str) -> str:
    """Qdrant point ids must be uuid or unsigned int. Convert our 16-hex id
    deterministically to a uuid5 so the same logical id always maps to the
    same point."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"knowledge::{memory_id}"))


def _wal_log(operation: str, **kwargs) -> None:
    wal_path = config.get_data_dir() / "wal.jsonl"
    entry = {"ts": time.time(), "op": operation, **kwargs}
    try:
        with open(wal_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _normalize_tags(tags) -> dict:
    """Accept either legacy comma-string or new structured dict. Return the
    canonical dict shape so payload filters work regardless of caller."""
    if isinstance(tags, dict):
        out = {
            "lang": tags.get("lang", "") or "",
            "layer": tags.get("layer", "") or "",
            "kind": tags.get("kind", "") or "",
            "domain": list(tags.get("domain") or []),
            "topics": list(tags.get("topics") or []),
        }
        return out
    if isinstance(tags, str) and tags:
        return {
            "lang": "",
            "layer": "",
            "kind": "",
            "domain": [t.strip() for t in tags.split(",") if t.strip()],
            "topics": [],
        }
    return {"lang": "", "layer": "", "kind": "", "domain": [], "topics": []}


# ── Existing-id cache ───────────────────────────────────────────
def _get_existing_ids() -> set[str]:
    global _id_cache
    if _id_cache is not None:
        return _id_cache
    client = _ensure_collection()
    _id_cache = set()
    offset = None
    try:
        while True:
            points, offset = client.scroll(
                collection_name=config.QDRANT_COLLECTION,
                limit=_SCAN_BATCH,
                offset=offset,
                with_payload=["_mid"],
                with_vectors=False,
            )
            for p in points:
                mid = (p.payload or {}).get("_mid")
                if mid:
                    _id_cache.add(mid)
            if offset is None:
                break
    except Exception:
        pass
    return _id_cache


# ── store / store_batch ─────────────────────────────────────────
def store(
    content: str,
    project: str = "",
    type: str = "",
    tags="",
    source: str = "",
    chunk_index: int = 0,
    source_mtime: float = 0.0,
) -> str:
    """Store a single memory. Returns the 16-hex logical id."""
    client = _ensure_collection()
    memory_id = _make_id(content, project, source)
    if memory_id in _get_existing_ids():
        return memory_id

    _wal_log("store", id=memory_id, project=project, source=source, type=type)

    vector = embeddings.embed_document(content)
    payload = {
        "_mid": memory_id,
        "content": content,
        "project": project,
        "type": type,
        "tags": _normalize_tags(tags),
        "source": source,
        "chunk_index": chunk_index,
        "source_mtime": source_mtime,
        "timestamp": time.time(),
    }
    client.upsert(
        collection_name=config.QDRANT_COLLECTION,
        points=[
            qm.PointStruct(
                id=_point_uuid(memory_id),
                vector={config.QDRANT_VECTOR_NAME: vector},
                payload=payload,
            )
        ],
    )
    _get_existing_ids().add(memory_id)
    return memory_id


def store_batch(records: list[dict]) -> tuple[int, int]:
    """Store many records with one embed pass. Skips duplicates by logical id.

    Each record: {content, project?, type?, tags?, source?, chunk_index?,
                  source_mtime?}. tags may be dict or comma-string.
    Returns (inserted, skipped).
    """
    client = _ensure_collection()
    existing = _get_existing_ids()

    new_records = []
    for r in records:
        mid = _make_id(r["content"], r.get("project", ""), r.get("source", ""))
        if mid not in existing:
            r["_mid"] = mid
            new_records.append(r)

    if not new_records:
        return 0, len(records)

    texts = [r["content"] for r in new_records]
    vectors = embeddings.embed_documents_batch(texts)

    points: list[qm.PointStruct] = []
    now = time.time()
    for r, vec in zip(new_records, vectors):
        payload = {
            "_mid": r["_mid"],
            "content": r["content"],
            "project": r.get("project", ""),
            "type": r.get("type", ""),
            "tags": _normalize_tags(r.get("tags")),
            "source": r.get("source", ""),
            "chunk_index": r.get("chunk_index", 0),
            "source_mtime": r.get("source_mtime", 0.0),
            "timestamp": now,
        }
        points.append(
            qm.PointStruct(
                id=_point_uuid(r["_mid"]),
                vector={config.QDRANT_VECTOR_NAME: vec},
                payload=payload,
            )
        )

    client.upsert(collection_name=config.QDRANT_COLLECTION, points=points)

    for r in new_records:
        existing.add(r["_mid"])

    _wal_log("store_batch", count=len(points))

    inserted = len(points)
    del points, vectors, texts, new_records
    gc.collect()

    _maybe_compact(inserted)
    return inserted, len(records) - inserted


def _maybe_compact(just_inserted: int) -> None:
    """Qdrant has its own background compaction but we can nudge it by
    flushing when many writes accumulate. Local mode flushes automatically
    on close, so this is a no-op placeholder for parity with the old LanceDB
    compact_files call."""
    global _inserts_since_compact
    _inserts_since_compact += just_inserted
    if _inserts_since_compact < _COMPACT_EVERY:
        return
    _inserts_since_compact = 0
    # Nothing to do for embedded qdrant; kept to preserve call-site parity.


# ── search ─────────────────────────────────────────────────────
def _build_filter(
    project: str = "",
    type: str = "",
    tag_filters: dict | None = None,
) -> qm.Filter | None:
    must: list[qm.FieldCondition] = []
    if project:
        must.append(qm.FieldCondition(key="project", match=qm.MatchValue(value=project)))
    if type:
        must.append(qm.FieldCondition(key="type", match=qm.MatchValue(value=type)))
    if tag_filters:
        for k, v in tag_filters.items():
            if not v:
                continue
            key = f"tags.{k}"
            if isinstance(v, list):
                # multi-value: match any
                must.append(qm.FieldCondition(key=key, match=qm.MatchAny(any=v)))
            else:
                must.append(qm.FieldCondition(key=key, match=qm.MatchValue(value=v)))
    if not must:
        return None
    return qm.Filter(must=must)


def search(
    query: str,
    limit: int = 10,
    project: str = "",
    type: str = "",
    tag_filters: dict | None = None,
) -> list[dict]:
    """Semantic search with optional metadata filters.

    tag_filters example:
        {"lang": "python", "domain": ["auth", "db"]}
    """
    client = _ensure_collection()
    info = client.get_collection(config.QDRANT_COLLECTION)
    if info.points_count == 0:
        return []

    vector = embeddings.embed_query(query)
    flt = _build_filter(project=project, type=type, tag_filters=tag_filters)

    hits = client.query_points(
        collection_name=config.QDRANT_COLLECTION,
        query=vector,
        using=config.QDRANT_VECTOR_NAME,
        query_filter=flt,
        limit=limit,
        with_payload=True,
    ).points

    out = []
    for h in hits:
        pl = h.payload or {}
        out.append({
            "id": pl.get("_mid", ""),
            "content": pl.get("content", ""),
            "project": pl.get("project", ""),
            "type": pl.get("type", ""),
            "tags": pl.get("tags", {}),
            "source": pl.get("source", ""),
            "chunk_index": pl.get("chunk_index", 0),
            # cosine distance → similarity is already the score in Qdrant
            "similarity": round(max(0.0, float(h.score)), 3),
        })
    return out


# ── delete ─────────────────────────────────────────────────────
def delete(memory_id: str) -> bool:
    client = _ensure_collection()
    try:
        _wal_log("delete", id=memory_id)
        client.delete(
            collection_name=config.QDRANT_COLLECTION,
            points_selector=qm.PointIdsList(points=[_point_uuid(memory_id)]),
        )
        _get_existing_ids().discard(memory_id)
        return True
    except Exception:
        return False


def delete_by_source(source: str) -> bool:
    client = _ensure_collection()
    try:
        _wal_log("delete_by_source", source=source)
        client.delete(
            collection_name=config.QDRANT_COLLECTION,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(must=[
                    qm.FieldCondition(key="source", match=qm.MatchValue(value=source))
                ])
            ),
        )
        # Invalidate id cache — couldn't know which ids matched without a scan.
        global _id_cache
        _id_cache = None
        return True
    except Exception:
        return False


# ── bulk reads ─────────────────────────────────────────────────
def _scroll_all(fields: list[str]) -> Iterable[dict]:
    client = _ensure_collection()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=config.QDRANT_COLLECTION,
            limit=_SCAN_BATCH,
            offset=offset,
            with_payload=fields,
            with_vectors=False,
        )
        for p in points:
            yield p.payload or {}
        if offset is None:
            break


def get_source_mtimes() -> dict[str, float]:
    """Return {source: source_mtime} for refresh deduplication."""
    out: dict[str, float] = {}
    try:
        for pl in _scroll_all(["source", "source_mtime"]):
            s = pl.get("source")
            m = pl.get("source_mtime")
            if s and m:
                out[s] = m
    except Exception:
        pass
    return out


def recent(limit: int = 15, types: list[str] | None = None) -> list[dict]:
    """Top-K most recent memories, optionally filtered by type."""
    import heapq

    client = _ensure_collection()
    info = client.get_collection(config.QDRANT_COLLECTION)
    if info.points_count == 0:
        return []

    type_set = set(types) if types else None
    heap: list[tuple[float, int, dict]] = []
    counter = 0

    try:
        for pl in _scroll_all([
            "_mid", "content", "project", "type", "tags", "source", "timestamp"
        ]):
            if type_set and pl.get("type", "") not in type_set:
                continue
            ts = pl.get("timestamp") or 0
            counter += 1
            if len(heap) < limit:
                heapq.heappush(heap, (ts, counter, pl))
            elif ts > heap[0][0]:
                heapq.heapreplace(heap, (ts, counter, pl))
    except Exception:
        return []

    heap.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "id": pl.get("_mid", ""),
            "content": pl.get("content", ""),
            "project": pl.get("project", ""),
            "type": pl.get("type", ""),
            "tags": pl.get("tags", {}),
            "source": pl.get("source", ""),
        }
        for _, _, pl in heap
    ]


def status() -> dict:
    client = _ensure_collection()
    try:
        info = client.get_collection(config.QDRANT_COLLECTION)
        total = info.points_count or 0
    except (UnexpectedResponse, ValueError):
        return {"total_memories": 0, "by_project": {}}

    by_project: dict[str, int] = {}
    if total:
        try:
            for pl in _scroll_all(["project"]):
                key = pl.get("project") or "(none)"
                by_project[key] = by_project.get(key, 0) + 1
        except Exception:
            by_project = {"(unknown)": total}

    return {"total_memories": total, "by_project": by_project}


def _get_table():
    """Legacy compat. Some sync scripts and cli_viz imported this; now a
    thin shim that returns the Qdrant client so callers can scroll via
    _scroll_all or directly."""
    return _ensure_collection()
