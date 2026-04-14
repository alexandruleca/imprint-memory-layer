"""Qdrant-backed vector store.

Embedded mode: `QdrantClient(path=...)` uses on-disk local storage, no server.

Collection schema:
  - vector: named "dense", f32[EMBEDDING_DIM], cosine distance
  - payload: {content, project, type, tags, source, chunk_index, source_mtime,
              timestamp}
    where tags = {lang, layer, kind, domain: [...], topics: [...]}
  - payload indexes on project, type, source, source_mtime, timestamp,
    tags.lang, tags.domain (keyword list), tags.topics (keyword list)

Public API (kept stable for callers):
  store, store_batch, search, delete, delete_by_source, get_source_mtimes,
  recent, recent_ordered, facet_counts, status, _get_existing_ids
"""

from __future__ import annotations

import atexit
import gc
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Iterable

from qdrant_client import QdrantClient, models as qm
from qdrant_client.http.exceptions import UnexpectedResponse

from . import config, embeddings, qdrant_runner

_client: QdrantClient | None = None
_collections_ready: set[str] = set()
_id_caches: dict[str, set[str]] = {}
_cache_lock = threading.Lock()

_inserts_since_compact = 0
_COMPACT_EVERY = int(os.environ.get("IMPRINT_COMPACT_EVERY", "500"))
_SCAN_BATCH = int(os.environ.get("IMPRINT_SCAN_BATCH", "2000"))

# Idle close: when set, the client is released after this many seconds of
# inactivity so other processes (`imprint ingest`) can grab the on-disk
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

    To use a managed/remote server instead, set IMPRINT_QDRANT_HOST and
    IMPRINT_QDRANT_NO_SPAWN=1 to skip the auto-spawn.
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
    global _client
    if _client is None:
        return
    try:
        _client.close()
    except Exception:
        pass
    _client = None
    _collections_ready.clear()
    _id_caches.clear()


atexit.register(_close_client)


def _touch_use() -> None:
    """Record activity. Idle thread checks this to decide when to close."""
    global _last_use_ts
    _last_use_ts = time.time()


def release_idle_client(after_seconds: float = 30.0) -> None:
    """Enable idle auto-close. After `after_seconds` of no vectorstore
    activity, release the on-disk Qdrant lock so other processes
    (e.g. `imprint ingest`) can take it. Intended for the MCP server,
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


def _resolve_collection(workspace: str | None = None) -> str:
    """Return collection name for the given (or active) workspace."""
    return config.collection_name(workspace)


def _ensure_collection(workspace: str | None = None) -> tuple[QdrantClient, str]:
    coll = _resolve_collection(workspace)
    client = _get_client()
    _touch_use()
    with _cache_lock:
        if coll in _collections_ready:
            return client, coll

    collections = {c.name for c in client.get_collections().collections}
    if coll not in collections:
        try:
            client.create_collection(
                collection_name=coll,
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
        ("timestamp", qm.PayloadSchemaType.FLOAT),
    ]:
        try:
            client.create_payload_index(
                collection_name=coll,
                field_name=field,
                field_schema=kind,
            )
        except Exception:
            pass

    with _cache_lock:
        _collections_ready.add(coll)
    return client, coll


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
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"imprint::{memory_id}"))


def _wal_log(operation: str, workspace: str | None = None, **kwargs) -> None:
    wal_path = config.wal_path(workspace)
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
def _get_existing_ids(workspace: str | None = None) -> set[str]:
    coll = _resolve_collection(workspace)
    with _cache_lock:
        if coll in _id_caches:
            return _id_caches[coll]
    # Build locally, then atomically swap in to avoid partial visibility.
    client, coll = _ensure_collection(workspace)
    local_cache: set[str] = set()
    offset = None
    try:
        while True:
            points, offset = client.scroll(
                collection_name=coll,
                limit=_SCAN_BATCH,
                offset=offset,
                with_payload=["_mid"],
                with_vectors=False,
            )
            for p in points:
                mid = (p.payload or {}).get("_mid")
                if mid:
                    local_cache.add(mid)
            if offset is None:
                break
    except Exception:
        pass
    with _cache_lock:
        # Another thread may have populated it already — use first writer wins.
        if coll not in _id_caches:
            _id_caches[coll] = local_cache
        return _id_caches[coll]


# ── store / store_batch ─────────────────────────────────────────
def store(
    content: str,
    project: str = "",
    type: str = "",
    tags="",
    source: str = "",
    chunk_index: int = 0,
    source_mtime: float = 0.0,
    workspace: str | None = None,
) -> str:
    """Store a single memory. Returns the 16-hex logical id."""
    client, coll = _ensure_collection(workspace)
    memory_id = _make_id(content, project, source)
    if memory_id in _get_existing_ids(workspace):
        return memory_id

    _wal_log("store", workspace=workspace, id=memory_id, project=project, source=source, type=type)

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
        collection_name=coll,
        points=[
            qm.PointStruct(
                id=_point_uuid(memory_id),
                vector={config.QDRANT_VECTOR_NAME: vector},
                payload=payload,
            )
        ],
    )
    _get_existing_ids(workspace).add(memory_id)
    return memory_id


def store_batch(records: list[dict], workspace: str | None = None) -> tuple[int, int]:
    """Store many records with one embed pass. Skips duplicates by logical id.

    Each record: {content, project?, type?, tags?, source?, chunk_index?,
                  source_mtime?}. tags may be dict or comma-string.
    Returns (inserted, skipped).
    """
    client, coll = _ensure_collection(workspace)
    existing = _get_existing_ids(workspace)

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

    client.upsert(collection_name=coll, points=points)

    for r in new_records:
        existing.add(r["_mid"])

    _wal_log("store_batch", workspace=workspace, count=len(points))

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
    workspace: str | None = None,
) -> list[dict]:
    """Semantic search with optional metadata filters.

    tag_filters example:
        {"lang": "python", "domain": ["auth", "db"]}
    """
    client, coll = _ensure_collection(workspace)
    info = client.get_collection(coll)
    if info.points_count == 0:
        return []

    vector = embeddings.embed_query(query)
    flt = _build_filter(project=project, type=type, tag_filters=tag_filters)

    hits = client.query_points(
        collection_name=coll,
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
def delete(memory_id: str, workspace: str | None = None) -> bool:
    client, coll = _ensure_collection(workspace)
    try:
        _wal_log("delete", workspace=workspace, id=memory_id)
        client.delete(
            collection_name=coll,
            points_selector=qm.PointIdsList(points=[_point_uuid(memory_id)]),
        )
        _get_existing_ids(workspace).discard(memory_id)
        return True
    except Exception:
        return False


def delete_by_source(source: str, workspace: str | None = None) -> bool:
    client, coll = _ensure_collection(workspace)
    try:
        _wal_log("delete_by_source", workspace=workspace, source=source)
        client.delete(
            collection_name=coll,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(must=[
                    qm.FieldCondition(key="source", match=qm.MatchValue(value=source))
                ])
            ),
        )
        # Invalidate id cache for this collection.
        with _cache_lock:
            _id_caches.pop(coll, None)
        return True
    except Exception:
        return False


# ── bulk reads ─────────────────────────────────────────────────
def _scroll_all(fields: list[str], workspace: str | None = None) -> Iterable[dict]:
    client, coll = _ensure_collection(workspace)
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=coll,
            limit=_SCAN_BATCH,
            offset=offset,
            with_payload=fields,
            with_vectors=False,
        )
        for p in points:
            yield p.payload or {}
        if offset is None:
            break


def get_source_mtimes(workspace: str | None = None) -> dict[str, float]:
    """Return {source: source_mtime} for refresh deduplication."""
    out: dict[str, float] = {}
    try:
        for pl in _scroll_all(["source", "source_mtime"], workspace=workspace):
            s = pl.get("source")
            m = pl.get("source_mtime")
            if s and m:
                out[s] = m
    except Exception:
        pass
    return out


def recent(limit: int = 15, types: list[str] | None = None, workspace: str | None = None) -> list[dict]:
    """Top-K most recent memories, optionally filtered by type."""
    import heapq

    client, coll = _ensure_collection(workspace)
    info = client.get_collection(coll)
    if info.points_count == 0:
        return []

    type_set = set(types) if types else None
    heap: list[tuple[float, int, dict]] = []
    counter = 0

    try:
        for pl in _scroll_all([
            "_mid", "content", "project", "type", "tags", "source", "timestamp"
        ], workspace=workspace):
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


def status(workspace: str | None = None) -> dict:
    client, coll = _ensure_collection(workspace)
    try:
        info = client.get_collection(coll)
        total = info.points_count or 0
    except (UnexpectedResponse, ValueError):
        return {"total_memories": 0, "by_project": {}}

    by_project: dict[str, int] = {}
    if total:
        facets = facet_counts("project", limit=50, workspace=workspace)
        if facets:
            by_project = {v: c for v, c in facets}
        else:
            by_project = {"(unknown)": total}

    return {"total_memories": total, "by_project": by_project}


def facet_counts(key: str, limit: int = 10, workspace: str | None = None) -> list[tuple[str, int]]:
    """Top (value, count) pairs for a payload key via Qdrant facet API.
    Uses keyword indexes — no full scan."""
    client, coll = _ensure_collection(workspace)
    try:
        resp = client.facet(
            collection_name=coll,
            key=key,
            limit=limit,
        )
        return [(hit.value, hit.count) for hit in resp.hits]
    except Exception:
        return []


def recent_ordered(limit: int = 15, types: list[str] | None = None, workspace: str | None = None) -> list[dict]:
    """Top-K most recent memories via indexed order_by. No full scan."""
    client, coll = _ensure_collection(workspace)
    try:
        info = client.get_collection(coll)
        if info.points_count == 0:
            return []
    except Exception:
        return []

    scroll_filter = None
    if types:
        scroll_filter = qm.Filter(must=[
            qm.FieldCondition(key="type", match=qm.MatchAny(any=types))
        ])

    try:
        points, _ = client.scroll(
            collection_name=coll,
            scroll_filter=scroll_filter,
            limit=limit,
            order_by=qm.OrderBy(key="timestamp", direction=qm.Direction.DESC),
            with_payload=["_mid", "content", "project", "type", "tags", "source", "timestamp"],
            with_vectors=False,
        )
    except Exception:
        return []

    return [
        {
            "id": (p.payload or {}).get("_mid", ""),
            "content": (p.payload or {}).get("content", ""),
            "project": (p.payload or {}).get("project", ""),
            "type": (p.payload or {}).get("type", ""),
            "tags": (p.payload or {}).get("tags", {}),
            "source": (p.payload or {}).get("source", ""),
            "timestamp": (p.payload or {}).get("timestamp", 0),
        }
        for p in points
    ]


def _get_table():
    """Legacy compat. Some sync scripts and cli_viz imported this; now a
    thin shim that returns the Qdrant client so callers can scroll via
    _scroll_all or directly."""
    return _ensure_collection()
