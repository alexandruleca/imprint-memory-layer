"""Qdrant-backed vector store.

Talks over HTTP to the local Qdrant server that qdrant_runner auto-spawns, so
the MCP server, hooks and ingest share one store (ADR 0063).

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
from typing import Callable, Iterable

from qdrant_client import QdrantClient, models as qm
from qdrant_client.http.exceptions import UnexpectedResponse

from . import config, embeddings, imprint_graph, qdrant_runner, device

_client: QdrantClient | None = None
_collections_ready: set[str] = set()
_id_caches: dict[str, set[str]] = {}
_cache_lock = threading.Lock()

_inserts_since_compact = 0
_COMPACT_EVERY = int(os.environ.get("IMPRINT_COMPACT_EVERY", "500"))
_SCAN_BATCH = int(os.environ.get("IMPRINT_SCAN_BATCH", "2000"))

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


def release_idle_client(after_seconds: float = 30.0) -> None:
    """Compatibility no-op for the retired embedded-client idle timer.

    HTTP clients hold no embedded database lock. A timer can expire while a
    caller retains the client across slow model loading/inference, breaking its
    subsequent read or durable write. Keep the pool until explicit cleanup or
    interpreter exit instead. ``after_seconds`` is ignored.
    """


def _resolve_collection(workspace: str | None = None) -> str:
    """Return collection name for the given (or active) workspace."""
    return config.collection_name(workspace)


def _ensure_collection(workspace: str | None = None) -> tuple[QdrantClient, str]:
    coll = _resolve_collection(workspace)
    client = _get_client()
    with _cache_lock:
        if coll in _collections_ready:
            return client, coll

    with qdrant_runner.creating_collections():
        if coll not in {c.name for c in client.get_collections().collections}:
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
                metadata={"imprint_embedding_identity": config.embedding_identity()},
            )

    info = client.get_collection(coll)
    vectors = info.config.params.vectors
    dense = vectors.get(config.QDRANT_VECTOR_NAME) if isinstance(vectors, dict) else None
    identity = (info.config.metadata or {}).get("imprint_embedding_identity")
    if dense is None or dense.size != config.EMBEDDING_DIM or (identity and identity != config.embedding_identity()):
        raise ValueError(f"Embedding profile mismatch for collection {coll!r}. Create a new workspace and reindex your sources; --force cannot change a collection's embedding profile. See docs/embeddings.md.")
    if not identity:
        if client.count(coll, exact=True).count:
            raise ValueError(f"Collection {coll!r} has unversioned embeddings. Create a new workspace and reindex, or back up and explicitly wipe this workspace before rebuilding. See docs/embeddings.md.")
        client.update_collection(coll, metadata={**(info.config.metadata or {}), "imprint_embedding_identity": config.embedding_identity()})

    # Payload indexes — server mode honors these and uses them to skip
    # full payload scans on filtered queries. Big speedup for tag-narrow
    # searches (lang/domain/etc) once the collection grows past ~10k pts.
    # create_payload_index is idempotent in Qdrant, safe to call every init.
    for field, kind in [
        ("project", qm.PayloadSchemaType.KEYWORD),
        ("type", qm.PayloadSchemaType.KEYWORD),
        ("source", qm.PayloadSchemaType.KEYWORD),
        ("source_type", qm.PayloadSchemaType.KEYWORD),
        ("source_url", qm.PayloadSchemaType.KEYWORD),
        ("source_mtime", qm.PayloadSchemaType.FLOAT),
        ("content", qm.TextIndexParams(type="text", tokenizer=qm.TokenizerType.WORD, lowercase=True)),
        ("tags.lang", qm.PayloadSchemaType.KEYWORD),
        ("tags.layer", qm.PayloadSchemaType.KEYWORD),
        ("tags.kind", qm.PayloadSchemaType.KEYWORD),
        ("tags.domain", qm.PayloadSchemaType.KEYWORD),
        ("tags.topics", qm.PayloadSchemaType.KEYWORD),
        ("timestamp", qm.PayloadSchemaType.FLOAT),
        # The background tagging backlog (ADR 0067): counted and scanned by it.
        ("tag_pending", qm.PayloadSchemaType.BOOL),
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
def _make_id(content: str, project: str = "", source: str = "", chunk_index: int | None = None) -> str:
    """Version 2 identity includes full content and optional chunk position.

    Existing IDs remain readable; replacing a source retires its legacy IDs.
    """
    key = json.dumps([project, source, content.strip(), chunk_index], ensure_ascii=False)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def relabeled_id(memory_id: str, record: dict, **changes) -> str:
    """The logical ID ``memory_id`` takes when its record's ``project`` or
    ``content`` changes (a move, a redaction). A note stored without a
    position keeps that form."""
    content, project, source = record.get("content", ""), record.get("project", ""), record.get("source", "")
    new = {"content": content, "project": project, **changes}
    if memory_id == _make_id(content, project, source):
        return _make_id(new["content"], new["project"], source)
    return _make_id(new["content"], new["project"], source, record.get("chunk_index"))


def _position(record: dict) -> tuple:
    """Where a memory sits: its versions differ only in redaction (ADR 0060)."""
    return record.get("project", ""), record.get("source", ""), int(record.get("chunk_index") or 0)


def _stored_versions(client, coll: str, records: list[dict], *, redacted_only: bool) -> dict[tuple, list]:
    """Stored memories at the records' positions, by position."""
    if not records:
        return {}
    must = [qm.FieldCondition(key="project", match=qm.MatchAny(any=sorted({r.get("project", "") for r in records}))),
            qm.FieldCondition(key="source", match=qm.MatchAny(any=sorted({r.get("source", "") for r in records})))]
    if redacted_only:
        must.append(qm.FieldCondition(key="content", match=qm.MatchText(text="REDACTED")))
    wanted, found, offset = {_position(r) for r in records}, {}, None
    while True:
        points, offset = client.scroll(collection_name=coll, scroll_filter=qm.Filter(must=must), limit=_SCAN_BATCH,
                                       offset=offset, with_payload=["content", "project", "source", "chunk_index"],
                                       with_vectors=False)
        for point in points:
            if _position(point.payload or {}) in wanted:
                found.setdefault(_position(point.payload), []).append(point)
        if offset is None:
            return found


def _redacted(records: list[dict], workspace: str) -> list[dict]:
    """Records with their secrets replaced, before anything derives from
    their content (ADR 0060). A record takes the content of the most redacted
    version of itself this workspace holds or receives with it, so a stricter
    device's redaction wins here too and a looser copy is never stored."""
    from .redaction import kinds, redact_all, redacted, supersedes
    records = [{**r, "content": text} for r, text in zip(records, redact_all([r["content"] for r in records]))]
    client, coll = _ensure_collection(workspace)
    versions = {position: [(p.payload or {}).get("content", "") for p in points] for position, points in
                _stored_versions(client, coll, records, redacted_only=True).items()}
    for record in records:
        if redacted(record["content"]):
            versions.setdefault(_position(record), []).append(record["content"])
    out = []
    for record in records:
        stricter = [text for text in versions.get(_position(record), []) if supersedes(text, record["content"])]
        # The maximal versions; incomparable ones are ordered by redaction count.
        stricter = [text for text in stricter if not any(supersedes(other, text) for other in stricter)]
        out.append({**record, "content": min(stricter, key=lambda text: (-sum(kinds(text).values()), text))}
                   if stricter else record)
    return out


def _retire_superseded(client, coll: str, records: list[dict], workspace: str) -> None:
    """Delete the stored versions that stored ``records`` redact further."""
    from .redaction import redacted, supersedes
    records = [r for r in records if redacted(r["content"])]
    retired = [point.id for position, points in _stored_versions(client, coll, records, redacted_only=False).items()
               for point in points
               if any(_position(r) == position and supersedes(r["content"], (point.payload or {}).get("content", ""))
                      for r in records)]
    if retired:
        client.delete(collection_name=coll, points_selector=qm.PointIdsList(points=retired), wait=True)
        with _cache_lock:
            _id_caches.pop(coll, None)
        _wal_log("supersede", workspace=workspace, count=len(retired))


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


def _live_existing_ids(client, collection: str, memory_ids: Iterable[str]) -> set[str]:
    """Check requested IDs, avoiding stale process caches and whole-index scans."""
    requested = list(dict.fromkeys(memory_ids))
    existing = set()
    for start in range(0, len(requested), 256):
        points = client.retrieve(collection_name=collection,
                                 ids=[_point_uuid(mid) for mid in requested[start:start + 256]],
                                 with_payload=["_mid"], with_vectors=False)
        existing.update(point.payload["_mid"] for point in points if point.payload and point.payload.get("_mid"))
    return existing


def _tagged_at(client, collection: str, memory_ids: Iterable[str]) -> dict[str, float]:
    """The classification stamp of each requested memory that exists (0 when
    it was never tagged after creation)."""
    requested = list(dict.fromkeys(memory_ids))
    stamps = {}
    for start in range(0, len(requested), 256):
        points = client.retrieve(collection_name=collection,
                                 ids=[_point_uuid(mid) for mid in requested[start:start + 256]],
                                 with_payload=["_mid", "tagged_at"], with_vectors=False)
        stamps.update((point.payload["_mid"], float(point.payload.get("tagged_at") or 0))
                      for point in points if point.payload and point.payload.get("_mid"))
    return stamps


def set_classifications(client, collection: str, updates: list[tuple[str, dict, str]]) -> None:
    """Store tags, and an LLM type when there is one, from tagging existing points.

    ``tagged_at`` moves past both now and the stamp it replaces, so this
    classification supersedes the previous one in every synced copy whatever
    the devices' clocks say (ADR 0051). The memory leaves the tagging backlog
    in the same write (ADR 0067).
    """
    if not updates:
        return
    previous = {}
    ids = [point_id for point_id, _, _ in updates]
    for start in range(0, len(ids), 256):
        for point in client.retrieve(collection_name=collection, ids=ids[start:start + 256],
                                     with_payload=["tagged_at"], with_vectors=False):
            previous[str(point.id)] = float((point.payload or {}).get("tagged_at") or 0)
    now = time.time()
    for point_id, tags, kind in updates:
        payload: dict = {"tags": _normalize_tags(tags), "tagged_at": max(now, previous.get(str(point_id), 0.0) + 0.001),
                         "tag_pending": False}
        if kind:
            payload["type"] = kind
            payload["llm_tagged"] = True
        client.set_payload(collection_name=collection, payload=payload, points=[point_id])


def _classification(record: dict) -> dict:
    """The synced classification of a received record (ADR 0051). A memory
    another device classified leaves this device's tagging backlog (ADR 0067)."""
    return {"tags": _normalize_tags(record.get("tags")), "type": record.get("type", ""),
            "llm_tagged": bool(record.get("llm_tagged", False)), "tagged_at": float(record["tagged_at"]),
            "tag_pending": False}


# ── store / store_batch ─────────────────────────────────────────
def store(
    content: str,
    project: str = "",
    type: str = "",
    tags="",
    source: str = "",
    chunk_index: int = 0,
    source_mtime: float = 0.0,
    source_type: str = "",
    source_url: str = "",
    doc_metadata: dict | None = None,
    etag: str = "",
    last_modified: str = "",
    source_path: str = "",
    workspace: str | None = None,
    tag_pending: bool = False,
) -> str:
    """Store a single memory. Returns the 16-hex logical id.

    ``tag_pending`` puts the memory on the background LLM tagging backlog
    (ADR 0067); storing paths that defer LLM tagging set it.

    New optional fields for document/URL ingestion:
        source_type: "file" | "url" | "ocr" | "" (legacy)
        source_url:  original URL when ingested from http(s)
        doc_metadata: flat dict (title/author/page_count/etc)
        etag / last_modified: HTTP cache headers for URL refresh
    """
    from .workspace_policy import check_records, workspace_name
    workspace = workspace_name(workspace)
    check_records([{'source': source, 'source_path': source_path}], workspace)
    from .projects import place
    placed = place([{'project': project, 'source': source}], workspace)[0]
    project = placed['project']
    note = {"content": content, "project": project, "source": source, "chunk_index": chunk_index}
    content = _redacted([note], workspace)[0]["content"]
    client, coll = _ensure_collection(workspace)
    memory_id = _make_id(content, project, source)
    if memory_id in _live_existing_ids(client, coll, [memory_id]):
        _retire_superseded(client, coll, [{**note, "content": content}], workspace)
        return memory_id

    _wal_log("store", workspace=workspace, id=memory_id, project=project, source=source, type=type)

    vector = embeddings.embed_document(content)
    payload = {
        "_mid": memory_id,
        "content": content,
        "project": project,
        "origin_project": placed["origin_project"],
        "type": type,
        "tags": _normalize_tags(tags),
        "source": source,
        "source_path": source_path,
        "source_type": source_type,
        "source_url": source_url,
        "doc_metadata": dict(doc_metadata) if doc_metadata else {},
        "etag": etag,
        "last_modified": last_modified,
        "chunk_index": chunk_index,
        "source_mtime": source_mtime,
        "timestamp": time.time(),
        **({"tag_pending": True} if tag_pending else {}),
    }
    from . import device
    payload = device.prepare(payload)
    _persist_points(client, coll, [
            qm.PointStruct(
                id=_point_uuid(memory_id),
                vector={config.QDRANT_VECTOR_NAME: vector},
                payload=payload,
            )
        ], workspace)
    with _cache_lock:
        _id_caches.pop(coll, None)
    _retire_superseded(client, coll, [payload], workspace)
    return memory_id


# Qdrant caps inbound JSON at 32 MB by default. Target ~14 MB so we have
# generous headroom for JSON-encoding overhead (floats serialize to ~16B
# each; content gets escape-char bloat; metadata dict keys repeat per point).
_MAX_UPSERT_BYTES = 14 * 1024 * 1024


def _point_size_estimate(p: qm.PointStruct) -> int:
    payload = getattr(p, "payload", None) or {}
    content = payload.get("content") or ""
    # JSON-escape inflates UTF-8 content by ~20%.
    content_bytes = int(len(content.encode("utf-8", errors="ignore")) * 1.2)
    # Each float serializes to ~16 bytes in JSON (digits + comma).
    vec_bytes = 0
    try:
        vec_map = p.vector if isinstance(p.vector, dict) else {"_": p.vector}
        for v in vec_map.values():
            vec_bytes += len(v) * 16
    except Exception:
        pass
    # Payload keys + doc_metadata + tags dict + timestamps.
    meta_bytes = 3072
    return content_bytes + vec_bytes + meta_bytes


def _upsert_chunked(client, coll: str, points: list) -> None:
    """Upsert points in size-bounded sub-batches so we stay under Qdrant's
    32 MB per-request payload limit."""
    if not points:
        return
    batch: list = []
    size = 0
    for p in points:
        est = _point_size_estimate(p)
        if batch and (size + est) > _MAX_UPSERT_BYTES:
            client.upsert(collection_name=coll, points=batch, wait=True)
            batch = []
            size = 0
        batch.append(p)
        size += est
    if batch:
        client.upsert(collection_name=coll, points=batch, wait=True)


def _persist_points(client, coll, points, workspace):
    from .workspace_policy import write_guard
    with write_guard([point.payload or {} for point in points], workspace):
        _upsert_chunked(client, coll, points)


def store_batch(records: list[dict], workspace: str | None = None, *, force: bool = False, imported: bool = False) -> tuple[int, int]:
    """Store many records with one embed pass. Skips duplicates by logical id.

    Each record: {content, project?, type?, tags?, source?, chunk_index?,
                  source_mtime?}. tags may be dict or comma-string.
    Secrets in the content are redacted first (ADR 0060).
    Returns (inserted, skipped).
    """
    from .workspace_policy import workspace_name
    from .projects import place
    workspace = workspace_name(workspace)
    return _store_redacted(_redacted(place(records, workspace), workspace), workspace, force=force, imported=imported)


def _store_redacted(records: list[dict], workspace: str | None, *, force: bool = False,
                    imported: bool = False) -> tuple[int, int]:
    from .workspace_policy import check_records, workspace_name
    workspace = workspace_name(workspace)
    from . import device
    records = [device.prepare(record, imported=imported) for record in records]
    check_records(records, workspace)
    from .projects import place
    records = place(records, workspace)
    client, coll = _ensure_collection(workspace)
    existing = set() if force else _live_existing_ids(client, coll, (
        _make_id(r["content"], r.get("project", ""), r.get("source", ""), r.get("chunk_index"))
        for r in records))

    new_records = []
    pending_ids = set()
    for r in records:
        mid = _make_id(r["content"], r.get("project", ""), r.get("source", ""), r.get("chunk_index"))
        if mid not in existing and mid not in pending_ids:
            r = {**r, "_mid": mid}
            pending_ids.add(mid)
            new_records.append(r)

    if not new_records:
        _retire_superseded(client, coll, records, workspace)
        return 0, len(records)

    texts = [r["content"] for r in new_records]
    vectors = embeddings.embed_documents_batch(texts)
    if len(vectors) != len(new_records):
        raise ValueError("Embedding batch returned an incomplete result; existing source preserved")

    # Zero-shot fallback: backfill empty topics using computed vectors.
    from . import tagger as _tagger
    from .config_schema import resolve
    zero_shot_enabled = resolve("tagger.zero_shot")[0]

    points: list[qm.PointStruct] = []
    now = time.time()
    for r, vec in zip(new_records, vectors):
        tags = _normalize_tags(r.get("tags"))
        # If topics are empty and zero-shot is available, fill from vector.
        if zero_shot_enabled and isinstance(tags, dict) and not tags.get("topics"):
            try:
                zs = _tagger.derive_zero_shot(vec)
                if zs:
                    tags["topics"] = zs
            except Exception:
                pass
        payload = {
            "_mid": r["_mid"],
            "content": r["content"],
            "project": r.get("project", ""),
            "type": r.get("type", ""),
            "tags": tags,
            "source": r.get("source", ""),
            "source_type": r.get("source_type", ""),
            "source_url": r.get("source_url", ""),
            "doc_metadata": dict(r.get("doc_metadata") or {}),
            "etag": r.get("etag", ""),
            "last_modified": r.get("last_modified", ""),
            "chunk_index": r.get("chunk_index", 0),
            "source_mtime": r.get("source_mtime", 0.0),
            "source_hash": r.get("source_hash", ""),
            "source_size": r.get("source_size", 0),
            "source_path": r.get("source_path", ""),
            "timestamp": now,
            "llm_tagged": bool(r.get("llm_tagged", False)),
            **({"tag_pending": True} if r.get("tag_pending") else {}),
            **device.provenance(r),
        }
        points.append(
            qm.PointStruct(
                id=_point_uuid(r["_mid"]),
                vector={config.QDRANT_VECTOR_NAME: vec},
                payload=payload,
            )
        )

    _persist_points(client, coll, points, workspace)

    with _cache_lock:
        _id_caches.pop(coll, None)

    _wal_log("store_batch", workspace=workspace, count=len(points))
    _retire_superseded(client, coll, records, workspace)

    inserted = len(points)
    del points, vectors, texts, new_records
    gc.collect()

    _maybe_compact(inserted)
    return inserted, len(records) - inserted


def replace_source(records: list[dict], source: str, project: str,
                   workspace: str | None = None, *, force: bool = False, batch_size: int | None = None,
                   progress: Callable[[int, int], None] | None = None) -> tuple[int, int]:
    """Upsert a complete source before pruning old chunks; failures keep old data.

    Readers can briefly see old and new chunks. This is not a multi-point
    database transaction. Destructive cleanup is scoped to source AND project.
    Chunks already stored keep their embedding and tags. ``batch_size``
    stores the chunks that many at a time, calling ``progress(done, total)``
    after each batch.
    """
    if not records:
        raise ValueError("Refusing to replace a source with no prepared chunks")
    if any(r.get("source") != source or r.get("project", "") != project for r in records):
        raise ValueError("Replacement records must belong to the specified source and project")
    from .workspace_policy import workspace_name
    workspace = workspace_name(workspace)
    from .projects import place
    records = _redacted(place(records, workspace), workspace)
    project = records[0]["project"]
    inserted = skipped = 0
    step = batch_size or len(records)
    for start in range(0, len(records), step):
        added, kept = _store_redacted(records[start:start + step], workspace, force=force)
        inserted, skipped = inserted + added, skipped + kept
        if progress:
            progress(min(start + step, len(records)), len(records))
    client, coll = _ensure_collection(workspace)
    ids = list({_point_uuid(_make_id(r["content"], project, source, r.get("chunk_index"))) for r in records})
    # Refresh provenance even when unchanged chunks reused their embeddings.
    provenance = {key: records[0][key] for key in
                  ("source_hash", "source_size", "source_path", "source_mtime", "etag", "last_modified",
                   "source_device", "source_path_local", "source_relative_path")
                  if key in records[0]}
    if provenance:
        client.set_payload(collection_name=coll, payload=provenance, points=ids, wait=True)
    client.delete(collection_name=coll, points_selector=qm.FilterSelector(filter=qm.Filter(
        must=[qm.FieldCondition(key="source", match=qm.MatchValue(value=source)),
              qm.FieldCondition(key="project", match=qm.MatchValue(value=project))],
        must_not=[qm.HasIdCondition(has_id=ids)],
    )), wait=True)
    with _cache_lock:
        _id_caches.pop(coll, None)
    _wal_log("replace_source", workspace=workspace, source=source, project=project, count=len(ids))
    return inserted, skipped


def _redacted_received(records: list[dict], workspace: str) -> list[dict]:
    """Received records after this device's redaction. A record it changes
    takes the ID of its redacted content and drops the sender's vector."""
    out = []
    for record, redacted in zip(records, _redacted(records, workspace)):
        if redacted["content"] != record["content"]:
            redacted["vector"] = None
            if record.get("id"):
                redacted["id"] = relabeled_id(record["id"], record, content=redacted["content"])
        out.append(redacted)
    return out


def store_batch_precomputed(records: list[dict], workspace: str | None = None) -> tuple[int, int, int]:
    """Store records that carry their vectors; only redacted ones are embedded.

    Each record: {content, vector, project?, type?, tags?, source?,
                  chunk_index?, source_mtime?, llm_tagged?, tagged_at?}.
    A record this workspace already has updates only its classification, and
    only when its ``tagged_at`` is newer (ADR 0051).
    Content is redacted here as in every write, whatever the sender did: a
    record it changes takes the ID of its redacted content, and its vector,
    computed from the secret, is replaced by an embedding of the redacted
    text (ADR 0060).
    Returns (inserted, retagged, skipped).
    """
    from . import device
    records = [device.prepare(record, imported=True) for record in records]
    if any(r.get("embedding_identity") != config.embedding_identity() for r in records):
        raise ValueError("Precomputed vector profile mismatch or missing identity. Reindex on the source with the same embedding settings, then export/sync again.")
    import math
    if any(len(r.get("vector", [])) != config.EMBEDDING_DIM or
           not all(math.isfinite(value) for value in r["vector"]) for r in records):
        raise ValueError("Precomputed vectors must have the configured dimension and finite values")
    def record_id(record):
        content, project, source = record["content"], record.get("project", ""), record.get("source", "")
        positioned = _make_id(content, project, source, record.get("chunk_index"))
        supplied = record.get("id")
        if supplied:
            if supplied not in (positioned, _make_id(content, project, source)):
                raise ValueError("Synced logical ID does not match record content and provenance")
            return supplied
        return positioned

    # Validate every ID before touching storage. Notes and positioned chunks
    # have different identities even when their payload chunk_index is zero.
    for record in records:
        record_id(record)
    from .workspace_policy import check_records, workspace_name
    workspace = workspace_name(workspace)
    check_records(records, workspace)
    # After the sender's IDs check out: this workspace's rules place them,
    # and its redaction rewrites them.
    from .projects import place
    records = _redacted_received(place(records, workspace), workspace)
    client, coll = _ensure_collection(workspace)
    stamps = _tagged_at(client, coll, (record_id(r) for r in records))

    new_records = []
    retagged: dict[str, dict] = {}
    pending_ids = set()
    for r in records:
        mid = record_id(r)
        if mid in stamps:
            # Last tagged wins; ties keep the local classification.
            if float(r.get("tagged_at") or 0) > stamps[mid]:
                stamps[mid] = float(r["tagged_at"])
                retagged[mid] = r
        elif mid not in pending_ids:
            pending_ids.add(mid)
            new_records.append({**r, "_mid": mid})

    for mid, r in retagged.items():
        client.set_payload(collection_name=coll, payload=_classification(r), points=[_point_uuid(mid)])
    if retagged:
        _wal_log("sync_retag", workspace=workspace, count=len(retagged))

    if not new_records:
        _retire_superseded(client, coll, records, workspace)
        return 0, len(retagged), len(records) - len(retagged)

    redacted = [r for r in new_records if r["vector"] is None]
    if redacted:
        vectors = embeddings.embed_documents_batch([r["content"] for r in redacted])
        if len(vectors) != len(redacted):
            raise ValueError("Embedding batch returned an incomplete result; no new memories were stored")
        for r, vector in zip(redacted, vectors):
            r["vector"] = vector

    points: list[qm.PointStruct] = []
    now = time.time()
    for r in new_records:
        payload = {
            "_mid": r["_mid"],
            "content": r["content"],
            "project": r.get("project", ""),
            "type": r.get("type", ""),
            "tags": _normalize_tags(r.get("tags")),
            "source": r.get("source", ""),
            "source_type": r.get("source_type", ""),
            "source_url": r.get("source_url", ""),
            "doc_metadata": dict(r.get("doc_metadata") or {}),
            "etag": r.get("etag", ""),
            "last_modified": r.get("last_modified", ""),
            "chunk_index": r.get("chunk_index", 0),
            "source_mtime": r.get("source_mtime", 0.0),
            "source_hash": r.get("source_hash", ""),
            "source_size": r.get("source_size", 0),
            "source_path": r.get("source_path", ""),
            "timestamp": now,
            "llm_tagged": bool(r.get("llm_tagged", False)),
            **({"tagged_at": float(r["tagged_at"])} if r.get("tagged_at") else {}),
            **device.provenance(r),
        }
        points.append(
            qm.PointStruct(
                id=_point_uuid(r["_mid"]),
                vector={config.QDRANT_VECTOR_NAME: r["vector"]},
                payload=payload,
            )
        )

    _persist_points(client, coll, points, workspace)

    with _cache_lock:
        _id_caches.pop(coll, None)

    _wal_log("store_batch_precomputed", workspace=workspace, count=len(points))
    _retire_superseded(client, coll, records, workspace)

    inserted = len(points)
    del points, new_records
    gc.collect()

    _maybe_compact(inserted)
    return inserted, len(retagged), len(records) - inserted - len(retagged)


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
    # The Qdrant server compacts on its own; kept to preserve call-site parity.


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
    query: str, limit: int = 10, offset: int = 0, project: str = "", type: str = "",
    tag_filters: dict | None = None, workspace: str | None = None,
    mode: str = "hybrid", source: str = "",
) -> list[dict]:
    """Bounded semantic/lexical search. Similarity is not answer confidence."""
    from .retrieval import CANDIDATE_LIMIT, query_terms, fuse
    from . import device
    workspace = workspace or config.get_active_workspace()
    query = query.strip()
    if not query:
        return []
    if len(query) > 4000:
        raise ValueError("Search query exceeds 4000 characters")
    if mode not in {"hybrid", "semantic"}:
        raise ValueError("Search mode must be hybrid or semantic")
    limit = max(1, min(int(limit), 50))
    offset = max(0, min(int(offset), CANDIDATE_LIMIT - 1))
    client, coll = _ensure_collection(workspace)
    if client.get_collection(coll).points_count == 0:
        return []
    flt = _build_filter(project=project, type=type, tag_filters=tag_filters)
    conditions = list(flt.must or []) if flt else []
    if source:
        conditions.append(qm.FieldCondition(key="source", match=qm.MatchValue(value=source)))
    flt = qm.Filter(must=conditions) if conditions else None
    vector = embeddings.embed_query(query)
    hits = client.query_points(collection_name=coll, query=vector, using=config.QDRANT_VECTOR_NAME,
                               query_filter=flt, limit=CANDIDATE_LIMIT, with_payload=True).points

    def result(point, similarity=0.0):
        payload = point.payload or {}
        return {**payload, "id": payload.get("_mid") or str(point.id),
                "content": payload.get("content", ""), "project": payload.get("project", ""),
                "source": payload.get("source", ""), "type": payload.get("type", ""),
                "tags": _normalize_tags(payload.get("tags")),
                "similarity": round(max(0., float(similarity)), 3)}

    semantic = [result(point, point.score) for point in hits]
    def present(rows):
        return [{**row, 'path_context': device.context(row, workspace)} for row in rows]
    if mode == "semantic":
        return present([{**row, "match_type": "semantic"} for row in semantic[offset:offset + limit]])
    terms = query_terms(query)
    lexical = []
    if terms:
        points, _ = client.scroll(collection_name=coll, scroll_filter=qm.Filter(
            must=conditions, should=[qm.FieldCondition(key="content", match=qm.MatchText(text=term)) for term in terms]),
            limit=CANDIDATE_LIMIT, with_payload=True, with_vectors=False)
        lexical = [result(point) for point in points]
    return present(fuse(query, semantic, lexical)[offset:offset + limit])


def resolve_point_id(memory_id: str) -> str:
    """Accept public logical IDs and legacy point UUIDs without confusing them."""
    try:
        return str(uuid.UUID(memory_id))
    except ValueError:
        if len(memory_id) == 16 and all(c in "0123456789abcdef" for c in memory_id.lower()):
            return _point_uuid(memory_id.lower())
        raise ValueError("Expected a memory ID or point UUID")


# ── delete ─────────────────────────────────────────────────────
def delete(memory_id: str, workspace: str | None = None) -> bool:
    client, coll = _ensure_collection(workspace)
    try:
        _wal_log("delete", workspace=workspace, id=memory_id)
        client.delete(
            collection_name=coll,
            points_selector=qm.PointIdsList(points=[_point_uuid(memory_id)]),
        )
        with _cache_lock:
            _id_caches.pop(coll, None)
        return True
    except Exception:
        return False


def delete_by_source(source: str, workspace: str | None = None, project: str | None = None) -> bool:
    client, coll = _ensure_collection(workspace)
    conditions = [qm.FieldCondition(key="source", match=qm.MatchValue(value=source))]
    if project is not None:
        conditions.append(qm.FieldCondition(key="project", match=qm.MatchValue(value=project)))
    _wal_log("delete_by_source", workspace=workspace, source=source)
    client.delete(
        collection_name=coll,
        points_selector=qm.FilterSelector(
            filter=qm.Filter(must=conditions)
        ), wait=True,
    )
    # Invalidate id cache for this collection.
    with _cache_lock:
        _id_caches.pop(coll, None)
    return True


# ── bulk reads ─────────────────────────────────────────────────
def _scroll_all(fields: list[str], workspace: str | None = None, with_vectors: bool = False) -> Iterable[dict]:
    client, coll = _ensure_collection(workspace)
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=coll,
            limit=_SCAN_BATCH,
            offset=offset,
            with_payload=fields,
            with_vectors=with_vectors,
        )
        for p in points:
            rec = p.payload or {}
            if with_vectors and p.vector:
                vec = p.vector
                if isinstance(vec, dict):
                    vec = vec.get(config.QDRANT_VECTOR_NAME, next(iter(vec.values()), None))
                rec["_vector"] = vec
            yield rec
        if offset is None:
            break


def get_source_states(workspace: str | None = None) -> dict[str, dict]:
    """Fail visibly if provenance cannot be read; never infer an empty index."""
    states = {}
    fields = ["source", "project", "source_hash", "source_size", "source_path", "source_mtime", "source_type", "source_device", "source_path_local", "source_relative_path"]
    for payload in _scroll_all(fields, workspace=workspace):
        if payload.get("source") and payload.get("source_type") in ("file", "ocr"):
            source = payload["source"]
            previous = states.get(source)
            if previous and previous.get("source_hash") != payload.get("source_hash"):
                # Incomplete replacement: force a repair rather than calling it unchanged.
                payload = {**payload, "source_hash": ""}
            states[source] = payload
    return states


def complete_conversation(source: str, source_mtime: float, learner_version: int,
                          workspace: str | None = None) -> None:
    """Publish a transcript's watermark only after every batch succeeded: the
    modification time it was read at and the learner version that read it."""
    if not source.startswith("conversation/"):
        raise ValueError("Expected a conversation source")
    client, coll = _ensure_collection(workspace)
    client.set_payload(collection_name=coll, payload={"source_mtime": source_mtime, "learner_version": learner_version},
                       points=qm.Filter(must=[qm.FieldCondition(key="source", match=qm.MatchValue(value=source))]),
                       wait=True)


def get_source_mtimes(workspace: str | None = None, learner_version: int | None = None) -> dict[str, float]:
    """Return {source: watermark}: the oldest source_mtime of a source's
    memories, 0 when one has none or, given ``learner_version``, when another
    learner version wrote it."""
    out: dict[str, float] = {}
    try:
        for pl in _scroll_all(["source", "source_mtime", "learner_version"], workspace=workspace):
            s = pl.get("source")
            m = pl.get("source_mtime")
            if learner_version is not None and pl.get("learner_version") != learner_version:
                m = 0.0
            if s:
                out[s] = min(out.get(s, float("inf")), m or 0.0)
    except Exception:
        pass
    return out


def get_url_sources(workspace: str | None = None) -> dict[str, dict]:
    """Return {source_url: {etag, last_modified, source, project}} for URL refresh.

    Only includes records with source_type == "url" that this device
    recorded: an imported record (from sync) is evidence, not authority to
    fetch on this machine. Deduplicates on source_url (picks first hit per
    URL — all chunks of one URL share the same etag/last_modified).
    """
    from .device import recorded_here
    out: dict[str, dict] = {}
    try:
        for pl in _scroll_all(
            ["source", "source_url", "source_type", "etag", "last_modified", "project", "recorded_device"],
            workspace=workspace,
        ):
            if pl.get("source_type") != "url" or not recorded_here(pl):
                continue
            url = pl.get("source_url") or pl.get("source")
            if not url or url in out:
                continue
            out[url] = {
                "etag": pl.get("etag", "") or "",
                "last_modified": pl.get("last_modified", "") or "",
                "source": pl.get("source", "") or url,
                "project": pl.get("project", "") or "",
            }
    except Exception:
        pass
    return out


# ── file retrieval ────────────────────────────────────────────
def list_sources(
    project: str = "",
    lang: str = "",
    layer: str = "",
    limit: int = 50,
    workspace: str | None = None,
) -> list[tuple[str, int]]:
    """List indexed source files with chunk counts via Qdrant facet API.

    Returns [(source_path, chunk_count)] sorted by count descending.
    """
    client, coll = _ensure_collection(workspace)
    tag_filters: dict = {}
    if lang:
        tag_filters["lang"] = lang
    if layer:
        tag_filters["layer"] = layer
    flt = _build_filter(project=project, tag_filters=tag_filters or None)
    try:
        resp = client.facet(
            collection_name=coll,
            key="source",
            facet_filter=flt,
            limit=limit,
        )
        return [(hit.value, hit.count) for hit in resp.hits]
    except Exception:
        return []


def get_source_summary(
    source: str,
    project: str = "",
    workspace: str | None = None,
) -> dict | None:
    """Return metadata summary for a single source file.

    Returns dict with keys: source, project, type, tags, source_type,
    source_mtime, chunk_count, first_chunk_preview.
    Returns None if source not found.
    """
    client, coll = _ensure_collection(workspace)
    must = [qm.FieldCondition(key="source", match=qm.MatchValue(value=source))]
    if project:
        must.append(qm.FieldCondition(key="project", match=qm.MatchValue(value=project)))
    flt = qm.Filter(must=must)

    all_points = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=coll,
            scroll_filter=flt,
            limit=_SCAN_BATCH,
            offset=offset,
            with_payload=["content", "project", "type", "tags", "source_type", "source",
                          "source_mtime", "chunk_index", "source_path", "recorded_device", "recorded_at",
                          "source_device", "source_relative_path", "source_path_local", "origin_project"],
            with_vectors=False,
        )
        all_points.extend(points)
        if offset is None:
            break

    if not all_points:
        return None

    # Sort by chunk_index to find first chunk
    all_points.sort(key=lambda p: (p.payload or {}).get("chunk_index", 0))
    first = all_points[0].payload or {}

    # Aggregate tags from all chunks
    all_domains: list[str] = []
    all_topics: list[str] = []
    max_mtime = 0.0
    for p in all_points:
        pl = p.payload or {}
        tags = pl.get("tags") or {}
        if isinstance(tags, dict):
            all_domains.extend(tags.get("domain") or [])
            all_topics.extend(tags.get("topics") or [])
        mt = pl.get("source_mtime") or 0.0
        if mt > max_mtime:
            max_mtime = mt

    first_tags = first.get("tags") or {}
    # Union of domains/topics, deduped
    seen_d: set[str] = set()
    domains = [d for d in all_domains if not (d in seen_d or seen_d.add(d))]
    seen_t: set[str] = set()
    topics = [t for t in all_topics if not (t in seen_t or seen_t.add(t))]

    # First chunk preview: strip [path] prefix, truncate
    preview = first.get("content", "")
    if preview.startswith("["):
        nl = preview.find("\n")
        if nl != -1:
            preview = preview[nl + 1:]
    preview = preview[:200]

    return {
        "source": source,
        "project": first.get("project", ""),
        "type": first.get("type", ""),
        "source_type": first.get("source_type", ""),
        "source_mtime": max_mtime,
        "chunk_count": len(all_points),
        "tags": {
            "lang": first_tags.get("lang", "") if isinstance(first_tags, dict) else "",
            "layer": first_tags.get("layer", "") if isinstance(first_tags, dict) else "",
            "kind": first_tags.get("kind", "") if isinstance(first_tags, dict) else "",
            "domain": domains[:10],
            "topics": topics[:15],
        },
        "first_chunk_preview": preview,
        "path_context": device.context(first, workspace),
        "recorded_device": first.get('recorded_device'),
    }


def get_chunks_by_source(
    source: str,
    start: int = 0,
    end: int | None = None,
    project: str = "",
    workspace: str | None = None,
) -> list[dict]:
    """Return chunks for a source file, ordered by chunk_index.

    Args:
        source: Source file path (exact match).
        start: First chunk index to include (0-based, inclusive).
        end: Last chunk index to include (inclusive). None = all from start.
        project: Filter by project (optional).

    Returns list of dicts: {id, project, source, chunk_index, content, tags,
    recorded_device, path_context}.
    """
    client, coll = _ensure_collection(workspace)
    must = [qm.FieldCondition(key="source", match=qm.MatchValue(value=source))]
    if project:
        must.append(qm.FieldCondition(key="project", match=qm.MatchValue(value=project)))
    flt = qm.Filter(must=must)

    all_points = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=coll,
            scroll_filter=flt,
            limit=_SCAN_BATCH,
            offset=offset,
            with_payload=["_mid", "content", "chunk_index", "tags", "source", "project", "source_type", "source_path", "recorded_device", "recorded_at", "source_device", "source_relative_path", "source_path_local", "origin_project"],
            with_vectors=False,
        )
        all_points.extend(points)
        if offset is None:
            break

    if not all_points:
        return []

    # Sort by chunk_index
    all_points.sort(key=lambda p: (p.payload or {}).get("chunk_index", 0))

    results = []
    for p in all_points:
        pl = p.payload or {}
        idx = pl.get("chunk_index", 0)
        if idx < start:
            continue
        if end is not None and idx > end:
            continue
        results.append({
            "id": pl.get("_mid") or str(p.id),
            "project": pl.get("project", ""),
            "source": pl.get("source", ""),
            "chunk_index": idx,
            "content": pl.get("content", ""),
            "tags": pl.get("tags", {}),
            "recorded_device": pl.get('recorded_device'),
            "path_context": device.context(pl, workspace),
        })

    return results


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


def count_memories_by_project(
    limit: int = 200, workspace: str | None = None
) -> dict[str, int]:
    """Return {project_name: count} for every project in the collection.

    Uses the Qdrant facet API against the indexed ``project`` keyword field —
    no full scan required.  *limit* caps how many distinct projects are
    returned; 200 is high enough to cover any realistic deployment.
    """
    pairs = facet_counts("project", limit=limit, workspace=workspace)
    return dict(pairs)


def workspace_stats(workspace: str) -> dict:
    """Return summary stats for *workspace*.

    Returns:
        {
            "memories": int,        # total vector-store points
            "facts":    int,        # active facts in the knowledge graph
            "projects": list[str],  # sorted project names with at least one memory
        }
    """
    stat = status(workspace=workspace)
    return {
        "memories": stat["total_memories"],
        "facts": imprint_graph.count(workspace=workspace),
        "projects": sorted(stat["by_project"]),
    }


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
            with_payload=["_mid", "content", "project", "type", "tags", "source", "timestamp", "recorded_device"],
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
            "recorded_device": (p.payload or {}).get('recorded_device'),
        }
        for p in points
    ]


def _get_table():
    """Legacy compat. Some sync scripts and cli_viz imported this; now a
    thin shim that returns the Qdrant client so callers can scroll via
    _scroll_all or directly."""
    return _ensure_collection()
