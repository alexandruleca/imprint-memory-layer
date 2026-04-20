"""Shared ingest pipeline for inline content blobs.

Extracted so that MCP tools (ingest_content), future API endpoints, and CLI
paths can all produce identical end-state memories without duplicating the
chunk → tag → embed → upsert wiring. The URL path in
``imprint.cli_ingest_url.ingest_one`` keeps its own loop for now because it
handles per-doc chunk_mode + HTTP-specific provenance (etag, last_modified).
"""

from __future__ import annotations

import time

from . import tagger, vectorstore as vs
from .chunker import chunk_file
from .config_schema import resolve


def store_inline_content(
    content: str,
    source_key: str,
    project: str = "ingested",
    chunk_mode: str = "prose",
    source_type: str = "content",
    rel_path: str | None = None,
    replace: bool = True,
    extra_fields: dict | None = None,
    workspace: str | None = None,
) -> tuple[int, str]:
    """Chunk, tag, embed, and upsert an inline content blob.

    Mirrors the end state of ``ingest_url`` / ``imprint ingest`` so memories
    produced from inline bytes are indistinguishable from memories produced
    from files or URLs.

    Args:
        content: Raw text to ingest.
        source_key: Logical source id, used for dedup + replace. Re-sending
            the same source_key (with ``replace=True``) overwrites prior
            chunks for that source.
        project: Project label (default ``"ingested"``).
        chunk_mode: ``"prose"`` or ``"code"``. Default ``"prose"``.
        source_type: Payload ``source_type`` tag (default ``"content"``).
        rel_path: Path-like hint used by the chunker for extension-based
            dispatch. If ``None``, uses ``source_key``. For code content,
            include an extension (e.g. ``rel_path="upload.py"``) so the
            tree-sitter chunker picks the right language.
        replace: If True, delete existing chunks with this source_key
            before inserting — same semantics as ``ingest_url``.
        extra_fields: Additional fields merged into each record dict.
        workspace: Target workspace. ``None`` = active workspace.

    Returns:
        ``(chunks_stored, status)`` where status is ``"stored"``,
        ``"skipped-empty"``, or ``"error: <reason>"``.
    """
    if not content or len(content.strip()) < 10:
        return 0, "skipped-empty"

    path_for_chunk = rel_path or source_key
    chunks = chunk_file(content, path_for_chunk, chunk_mode=chunk_mode)
    if not chunks:
        return 0, "error: no chunks produced"

    if replace:
        vs.delete_by_source(source_key, workspace=workspace)

    enable_llm = resolve("tagger.llm")[0]
    if not enable_llm and tagger._get_llm_provider() == "local":
        _, _llm_source = resolve("tagger.llm")
        if _llm_source == "default":
            enable_llm = True
    enable_zero_shot = resolve("tagger.zero_shot")[0] and not enable_llm

    records: list[dict] = []
    now = time.time()
    extras = extra_fields or {}
    for i, (chunk_text, chunk_idx) in enumerate(chunks):
        prev_text = chunks[i - 1][0][-200:] if i > 0 else ""
        next_text = chunks[i + 1][0][:200] if i < len(chunks) - 1 else ""
        neighbor_ctx = prev_text + ("\n...\n" if prev_text and next_text else "") + next_text
        tags = tagger.build_payload_tags(
            chunk_text,
            rel_path=path_for_chunk,
            llm=None,
            zero_shot=enable_zero_shot,
            neighbor_context=neighbor_ctx,
            project_hint=project,
            workspace=workspace,
        )
        llm_type = tags.pop("_llm_type", "")
        mem_type = llm_type or "architecture"
        records.append({
            "content": chunk_text,
            "project": project,
            "type": mem_type,
            "tags": tags,
            "source": source_key,
            "source_type": source_type,
            "chunk_index": chunk_idx,
            "source_mtime": now,
            **extras,
        })

    inserted, _ = vs.store_batch(records, workspace=workspace)
    return inserted, "stored"
