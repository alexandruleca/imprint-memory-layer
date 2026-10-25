"""Shared ingest pipeline for inline content blobs.

Extracted so that MCP tools (ingest_content), the browser capture worker, and
CLI paths can produce identical end-state memories without duplicating the
chunk → tag → embed → upsert wiring. The URL path in
``imprint.cli_ingest_url.ingest_one`` keeps its own loop for now because it
handles per-doc chunk_mode + HTTP-specific provenance (etag, last_modified).
"""

from __future__ import annotations

import time

from . import tagger, vectorstore as vs
from .chunker import MIN_SIZE, chunk_file
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
        replace: If True, upsert replacement chunks before pruning obsolete
            chunks, scoped to this source_key and project.
        extra_fields: Additional fields merged into each record dict.
        workspace: Target workspace. ``None`` = active workspace.

    Returns:
        ``(chunks_stored, status)`` where status is ``"stored"``,
        ``"skipped-empty"``, or ``"error: <reason>"``.
    """
    if not content or len(content.strip()) < 10:
        return 0, "skipped-empty"
    content = content.strip()

    path_for_chunk = rel_path or source_key
    # File chunking drops tiny files. Inline captures/notes legitimately contain
    # less than that threshold and already passed this API's 10-character floor.
    chunks = ([(f'[{path_for_chunk}]\n{content}', 0)] if len(content) < MIN_SIZE
              else chunk_file(content, path_for_chunk, chunk_mode=chunk_mode))
    if not chunks:
        return 0, "error: no chunks produced"

    from .tag_backlog import deferring
    pending = deferring()  # LLM tagging runs in the background (ADR 0067)
    enable_zero_shot = resolve("tagger.zero_shot")[0]

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
            llm=False,
            zero_shot=enable_zero_shot,
            neighbor_context=neighbor_ctx,
            project_hint=project,
            workspace=workspace,
        )
        tags.pop("_llm_type", None)
        records.append({
            "content": chunk_text,
            "project": project,
            "type": "architecture",
            "tags": tags,
            "source": source_key,
            "source_type": source_type,
            "chunk_index": len(records),
            "source_mtime": now,
            "tag_pending": pending,
            **extras,
        })

    if replace:
        inserted, _ = vs.replace_source(records, source_key, project, workspace=workspace)
    else:
        inserted, _ = vs.store_batch(records, workspace=workspace)
    return inserted, "stored"
