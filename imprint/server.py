"""Imprint MCP Server — lightweight memory for Claude Code."""

from fastmcp import FastMCP

from . import config
from . import imprint_graph as kg
from . import vectorstore as vs

mcp = FastMCP("imprint")

# Release the embedded Qdrant lock after 30s of no MCP activity so
# `imprint ingest` (a separate process) can grab it. Without this, the
# MCP server pins the lock for the lifetime of Claude Code.
vs.release_idle_client(after_seconds=float(__import__("os").environ.get("IMPRINT_MCP_IDLE_S", "30")))

# Similarity threshold (0-1 scale, higher = better). Below this = noise.
RELEVANCE_THRESHOLD = 0.2

# Token budget splits (~1100 tokens total)
L1_MAX_ENTRIES = 15
L1_MAX_CHARS = 2400   # ~600 tokens — essential decisions/patterns
RECENT_MAX_ENTRIES = 8
RECENT_MAX_CHARS = 800  # ~200 tokens — recent activity

# Search output budget
SEARCH_MAX_CONTENT_CHARS = 1500  # per-result content truncation
SEARCH_MAX_TOTAL_CHARS = 12000   # hard cap on full search output


_session_woken = False


def _validate_ws(workspace: str) -> tuple[str | None, str | None]:
    """Resolve and validate workspace parameter.
    Returns (resolved_workspace_or_None, error_message_or_None)."""
    if not workspace:
        return None, None
    err = config.validate_workspace_name(workspace)
    if err:
        return None, f"Invalid workspace name: {err}"
    if workspace not in config.get_known_workspaces():
        known = ", ".join(config.get_known_workspaces())
        return None, f"Unknown workspace '{workspace}'. Known: {known}. Create with: imprint workspace switch {workspace}"
    return workspace, None


@mcp.tool()
def wake_up(workspace: str = "") -> str:
    """Load prior context at the start of a conversation.
    Returns project overview, essential decisions/patterns, and active facts.
    Note: search() auto-calls this on its first invocation, so calling wake_up
    explicitly is optional — just go straight to search().

    Args:
        workspace: Target a specific workspace instead of the active one (optional)
    """
    global _session_woken
    _session_woken = True
    ws, err = _validate_ws(workspace)
    if err:
        return err
    facts = kg.recent(limit=10, workspace=ws)
    lines = []

    display_ws = ws or config.get_active_workspace()
    if display_ws != "default":
        label = "Workspace" if ws else "Active workspace"
        lines.append(f"{label}: {display_ws}")
        lines.append("")

    # ── Section 1: Project overview (facet-based, no scan) ──
    project_facets = vs.facet_counts("project", limit=20, workspace=ws)
    if project_facets:
        lines.append("Projects in memory:")
        for name, count in project_facets:
            lines.append(f"  {name} ({count} memories)")

    # ── Section 2: L1 Essential Story ──
    essential = vs.recent_ordered(
        limit=50,
        types=["decision", "pattern", "preference", "bug", "milestone", "architecture"],
        workspace=ws,
    )
    if essential:
        type_weight = {
            "decision": 5, "preference": 4, "pattern": 3,
            "bug": 3, "architecture": 2, "milestone": 2, "finding": 1,
        }
        scored = sorted(essential, key=lambda r: -type_weight.get(r["type"], 1))

        lines.append("\nEssential context:")
        total_chars = 0
        for r in scored[:L1_MAX_ENTRIES]:
            summary = _first_meaningful_line(r["content"], max_len=180)
            entry = f"  • [{r['type']}] {summary}"
            if r["source"]:
                entry += f"  ({r['source']})"
            if total_chars + len(entry) > L1_MAX_CHARS:
                lines.append("  ... (use search for more)")
                break
            lines.append(entry)
            total_chars += len(entry)

    # ── Section 3: Recent activity (any type, deduped by source) ──
    recent_all = vs.recent_ordered(limit=RECENT_MAX_ENTRIES * 3, workspace=ws)  # over-fetch to account for dedup
    if recent_all:
        lines.append("\nRecent activity:")
        total_chars = 0
        seen_sources: set[str] = set()
        count = 0
        for r in recent_all:
            if count >= RECENT_MAX_ENTRIES:
                break
            dedup_key = r["source"] or r["id"]
            if dedup_key in seen_sources:
                continue
            seen_sources.add(dedup_key)

            preview = _first_meaningful_line(r["content"], max_len=120)
            meta_parts = []
            if r["project"]:
                meta_parts.append(r["project"])
            if r["type"]:
                meta_parts.append(r["type"])
            meta = " | ".join(meta_parts)

            src = ""
            if r["source"]:
                src = r["source"].rsplit("/", 1)[-1]

            if src:
                entry = f"  • {src}"
                if meta:
                    entry += f"  ({meta})"
                entry += f" — {preview}"
            else:
                entry = f"  • {preview}"
                if meta:
                    entry += f"  ({meta})"

            if total_chars + len(entry) > RECENT_MAX_CHARS:
                break
            lines.append(entry)
            total_chars += len(entry)
            count += 1

    # ── Section 4: Knowledge coverage (faceted tag stats) ──
    lang_facets = vs.facet_counts("tags.lang", limit=8, workspace=ws)
    domain_facets = vs.facet_counts("tags.domain", limit=10, workspace=ws)
    layer_facets = vs.facet_counts("tags.layer", limit=6, workspace=ws)
    type_facets = vs.facet_counts("type", limit=10, workspace=ws)

    coverage_parts = []
    if lang_facets:
        langs = ", ".join(f"{v}({c})" for v, c in lang_facets if v)
        if langs:
            coverage_parts.append(f"  Languages: {langs}")
    if domain_facets:
        domains = ", ".join(f"{v}({c})" for v, c in domain_facets if v)
        if domains:
            coverage_parts.append(f"  Domains: {domains}")
    if layer_facets:
        layers = ", ".join(f"{v}({c})" for v, c in layer_facets if v)
        if layers:
            coverage_parts.append(f"  Layers: {layers}")
    if type_facets:
        types = ", ".join(f"{v}({c})" for v, c in type_facets if v)
        if types:
            coverage_parts.append(f"  Types: {types}")

    if coverage_parts:
        lines.append("\nKnowledge coverage (searchable with filters):")
        lines.extend(coverage_parts)

    # ── Section 5: Active facts ──
    if facts:
        lines.append("\nActive facts:")
        for f in facts:
            lines.append(f"  • {f['subject']} {f['predicate']} {f['object']}")

    if not lines:
        return "Imprint memory is empty. Use 'store' to add memories and 'imprint index' to index projects."

    return "\n".join(lines)


@mcp.tool()
def search(
    query: str,
    project: str = "",
    type: str = "",
    lang: str = "",
    layer: str = "",
    kind: str = "",
    domain: str = "",
    limit: int = 10,
    offset: int = 0,
    workspace: str = "",
) -> str:
    """Semantic search across stored memories.
    Check this BEFORE reading files — the answer may already be here.
    Returns relevant code chunks, decisions, and patterns.
    Automatically loads session context (wake_up) on the first call.

    Args:
        query: What to search for (natural language)
        project: Filter by project name (optional)
        type: Filter by memory type (e.g. decision, pattern, finding, bug, architecture). Dynamic — use wake_up to see available types. (optional)
        lang: Filter by language tag (e.g. python, typescript, go, markdown, conversation)
        layer: Filter by layer (e.g. api, ui, tests, infra, config, docs, cli, session)
        kind: Filter by file kind (e.g. source, test, migration, readme, types, module, auto-extract)
        domain: Filter by domain tag, comma-separated for multi-match (e.g. auth, db, api, ml). Dynamic — use wake_up to see available domains. (optional)
        limit: Max results (default 10)
        offset: Skip first N results for pagination (default 0). Use when previous search indicated more results available.
        workspace: Target a specific workspace instead of the active one (optional)
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err

    limit = max(1, min(limit, 50))

    tag_filters: dict = {}
    if lang:
        tag_filters["lang"] = lang
    if layer:
        tag_filters["layer"] = layer
    if kind:
        tag_filters["kind"] = kind
    if domain:
        doms = [d.strip() for d in domain.split(",") if d.strip()]
        if doms:
            tag_filters["domain"] = doms

    results = vs.search(
        query, limit=limit, offset=offset, project=project, type=type,
        tag_filters=tag_filters or None, workspace=ws,
    )

    # ── Auto-wake: prepend session context on first search call ──
    prefix = ""
    if not _session_woken:
        prefix = wake_up(workspace=workspace) + "\n\n---\n\n"

    if not results:
        return prefix + "No results. Try reading the relevant files directly."

    # Filter by similarity threshold
    relevant = [r for r in results if r["similarity"] >= RELEVANCE_THRESHOLD]
    if not relevant:
        return prefix + "No relevant matches found. Try reading the relevant files directly."

    lines = []

    if offset > 0:
        lines.append(f"(Showing results {offset + 1}–{offset + len(relevant)})\n")

    # ── Confidence-based guidance ──
    avg_sim = sum(r["similarity"] for r in relevant) / len(relevant)
    if avg_sim >= 0.6:
        lines.append("High-confidence results — answer from these without reading files.\n")
    elif avg_sim < 0.35:
        lines.append("Low-confidence matches — consider reading files for accuracy.\n")

    for i, r in enumerate(relevant, 1):
        meta = []
        if r["project"]:
            meta.append(r["project"])
        if r["source"]:
            meta.append(r["source"])
        # Surface structured tags so the model can use them for follow-up
        # filtering without calling search again.
        tags = r.get("tags") or {}
        tag_bits = []
        if isinstance(tags, dict):
            if tags.get("lang"):
                tag_bits.append(tags["lang"])
            if tags.get("layer"):
                tag_bits.append(tags["layer"])
            for d in (tags.get("domain") or [])[:3]:
                tag_bits.append(d)
        if tag_bits:
            meta.append("#" + " #".join(tag_bits))
        meta_str = " | ".join(meta) if meta else ""

        lines.append(f"[{i}] {meta_str}  (similarity: {r['similarity']:.3f})")
        if r.get("type") == "pattern":
            lines.append(f"  [cross-project pattern from {r.get('project', '?')}]")
        content = r["content"]
        if len(content) > SEARCH_MAX_CONTENT_CHARS:
            source = r.get("source", "")
            hint = f'\n  … [truncated — use file_chunks(source="{source}") for full content]' if source else "\n  … [truncated]"
            content = content[:SEARCH_MAX_CONTENT_CHARS] + hint
        lines.append(content)
        lines.append("")

    # Hint about more results when we hit the limit
    if len(relevant) == limit:
        next_offset = offset + limit
        lines.append(f"({len(relevant)} results shown. More may exist — use offset={next_offset} to continue.)")

    output = prefix + "\n".join(lines)
    if len(output) > SEARCH_MAX_TOTAL_CHARS:
        next_offset = offset + limit
        output = output[:SEARCH_MAX_TOTAL_CHARS] + (
            f"\n\n… [output truncated — {len(relevant)} results matched. "
            f"Use offset={next_offset} to see more, or add filters to narrow results]"
        )
    return output


@mcp.tool()
def store(
    content: str,
    project: str = "",
    type: str = "",
    tags: str = "",
    source: str = "",
    workspace: str = "",
) -> str:
    """Store a memory. Write it as a self-contained note that will make sense
    months from now without additional context. Include the WHY, not just the WHAT.

    Args:
        content: The memory to store — be specific, include reasoning
        project: Project this relates to (e.g. 'my-web-app', 'api-server')
        type: Memory type (e.g. decision, pattern, finding, bug, architecture, milestone). Auto-classified if omitted.
        tags: Comma-separated tags (e.g. 'cors,security')
        source: Where this came from (e.g. file path, conversation topic)
        workspace: Target a specific workspace instead of the active one (optional)
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err

    # Auto-classify type if not provided
    if not type:
        from . import classifier, tagger
        tags = tagger.build_payload_tags(content, workspace=ws)
        llm_type = tags.pop("_llm_type", "")
        if llm_type:
            type = llm_type
        else:
            type, _ = classifier.classify(content)

    memory_id = vs.store(
        content=content, project=project, type=type, tags=tags, source=source,
        workspace=ws,
    )
    return f"Stored [{memory_id}] as {type}"


@mcp.tool()
def delete(memory_id: str, workspace: str = "") -> str:
    """Delete a memory by its ID.

    Args:
        memory_id: The memory ID from store or search results
        workspace: Target a specific workspace instead of the active one (optional)
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err
    if vs.delete(memory_id, workspace=ws):
        return f"Deleted {memory_id}"
    return f"Not found: {memory_id}"


@mcp.tool()
def kg_query(subject: str = "", predicate: str = "", limit: int = 20, workspace: str = "") -> str:
    """Query temporal facts from the imprint graph.

    Args:
        subject: Entity to look up (partial match)
        predicate: Relationship type (partial match)
        limit: Max results
        workspace: Target a specific workspace instead of the active one (optional)
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err
    facts = kg.query(subject=subject, predicate=predicate, limit=limit, workspace=ws)

    if not facts:
        return "No facts found."

    lines = []
    for f in facts:
        ended = " [ENDED]" if f["ended"] else ""
        lines.append(f"  {f['subject']} → {f['predicate']} → {f['object']}{ended}")

    return "\n".join(lines)


@mcp.tool()
def kg_add(subject: str, predicate: str, object: str, source: str = "", workspace: str = "") -> str:
    """Add a structured fact. Use for relationships that may change over time.

    Args:
        subject: The entity (e.g. 'api-server', 'auth-service')
        predicate: The relationship (e.g. 'uses', 'decided', 'prefers')
        object: The value (e.g. 'NestJS', 'wildcard CORS')
        source: Where this fact came from
        workspace: Target a specific workspace instead of the active one (optional)
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err
    fact_id = kg.add(subject=subject, predicate=predicate, object=object, source=source, workspace=ws)
    return f"Fact [{fact_id}]: {subject} → {predicate} → {object}"


@mcp.tool()
def kg_invalidate(fact_id: int, workspace: str = "") -> str:
    """Mark a fact as ended (no longer true).

    Args:
        fact_id: The fact ID from kg_query
        workspace: Target a specific workspace instead of the active one (optional)
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err
    if kg.invalidate(fact_id, workspace=ws):
        return f"Ended fact {fact_id}"
    return f"Not found or already ended: {fact_id}"


@mcp.tool()
def ingest_url(url: str, project: str = "urls", force: bool = False, workspace: str = "") -> str:
    """Fetch a URL, extract its content (html/pdf/image OCR/etc), chunk,
    and store as memories. Re-run on the same URL skips when ETag /
    Last-Modified hasn't changed (unless ``force=True``).

    Args:
        url: http(s) URL to ingest
        project: Project label for the resulting memories (default: "urls")
        force: Re-fetch even if HEAD says content is unchanged
        workspace: Target a specific workspace instead of the active one
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err

    from .cli_ingest_url import ingest_one
    known = vs.get_url_sources(workspace=ws)
    n, status = ingest_one(url, project, known, force=force)
    if status == "stored":
        return f"Stored {n} chunks from {url} (project={project})"
    if status == "skipped-unchanged":
        return f"Unchanged — skipped {url}"
    return f"Failed {url}: {status}"


@mcp.tool()
def refresh_urls(project: str = "", workspace: str = "") -> str:
    """Re-check every stored URL via HEAD request. Re-fetches and re-indexes
    any whose ETag or Last-Modified header has changed.

    Args:
        project: Restrict to one project (optional)
        workspace: Target a specific workspace (optional)
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err

    from .cli_ingest_url import ingest_one
    from .extractors import url as url_ext
    from . import extractors as _ext

    known = vs.get_url_sources(workspace=ws)
    if project:
        known = {u: v for u, v in known.items() if v.get("project") == project}
    if not known:
        return "No URL sources stored yet."

    updated = unchanged = errors = 0
    for url, info in known.items():
        try:
            head = url_ext.head_check(url)
        except _ext.ExtractorUnavailable as e:
            return f"URL refresh needs httpx: {e}"
        same_etag = head.get("etag") and head["etag"] == info.get("etag")
        same_mod = head.get("last_modified") and head["last_modified"] == info.get("last_modified")
        if head and (same_etag or same_mod):
            unchanged += 1
            continue
        proj = info.get("project") or "urls"
        _, status = ingest_one(url, proj, known, force=True)
        if status == "stored":
            updated += 1
        else:
            errors += 1
    return f"URL refresh: updated={updated} unchanged={unchanged} errors={errors}"


@mcp.tool()
def status(workspace: str = "") -> str:
    """Imprint memory overview.

    Args:
        workspace: Target a specific workspace instead of the active one (optional)
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err
    display_ws = ws or config.get_active_workspace()
    stats = vs.status(workspace=ws)
    facts = kg.query(limit=1000, workspace=ws)
    active = sum(1 for f in facts if not f["ended"])

    lines = []
    if display_ws != "default":
        lines.append(f"Workspace: {display_ws}")
    lines.append(f"Memories: {stats['total_memories']}  |  Facts: {active}")
    if stats["by_project"]:
        projects = ", ".join(
            f"{p}({c})" for p, c in sorted(stats["by_project"].items(), key=lambda x: -x[1])
        )
        lines.append(f"Projects: {projects}")

    return "\n".join(lines)


# ── file retrieval tools ──────────────────────────────────────

@mcp.tool()
def list_sources(
    project: str = "",
    lang: str = "",
    layer: str = "",
    limit: int = 50,
    workspace: str = "",
) -> str:
    """List all indexed source files in the KB with chunk counts.
    Helps discover what's available before using file_summary or file_chunks.

    Args:
        project: Filter by project name (optional)
        lang: Filter by language tag (python, typescript, go, etc.)
        layer: Filter by layer (api, ui, tests, infra, config, docs, etc.)
        limit: Max number of sources to return (default 50)
        workspace: Target a specific workspace (optional)
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err

    sources = vs.list_sources(
        project=project, lang=lang, layer=layer, limit=limit, workspace=ws,
    )

    if not sources:
        return "No indexed sources found. Use 'imprint ingest <dir>' to index a project."

    lines = [f"Indexed sources ({len(sources)} shown):"]
    lines.append("")
    for path, count in sources:
        lines.append(f"  {count:>4} chunks  {path}")

    return "\n".join(lines)


@mcp.tool()
def file_summary(
    source: str,
    project: str = "",
    workspace: str = "",
) -> str:
    """Quick overview of an indexed file — call BEFORE deciding to Read a file.
    Returns chunk count, tags, modification time, and a preview of the first chunk.

    Args:
        source: Source file path as stored in the KB (usually project/relative-path)
        project: Filter by project name (optional, for disambiguation)
        workspace: Target a specific workspace (optional)
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err

    summary = vs.get_source_summary(source, project=project, workspace=ws)
    if summary is None:
        return f"Source not found in KB: {source}\nUse list_sources to discover indexed files."

    from datetime import datetime
    tags = summary.get("tags") or {}
    mtime = summary.get("source_mtime", 0)
    mtime_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "unknown"

    lines = [
        f"Source: {summary['source']}",
        f"Project: {summary.get('project', '?')}  |  Chunks: {summary['chunk_count']}  |  Modified: {mtime_str}",
    ]

    tag_parts = []
    if tags.get("lang"):
        tag_parts.append(f"lang={tags['lang']}")
    if tags.get("layer"):
        tag_parts.append(f"layer={tags['layer']}")
    if tags.get("kind"):
        tag_parts.append(f"kind={tags['kind']}")
    if tags.get("domain"):
        tag_parts.append(f"domains={tags['domain']}")
    if tags.get("topics"):
        tag_parts.append(f"topics={tags['topics']}")
    if tag_parts:
        lines.append(f"Tags: {', '.join(tag_parts)}")

    preview = summary.get("first_chunk_preview", "")
    if preview:
        lines.append(f"\nPreview (chunk 0):\n{preview}...")

    return "\n".join(lines)


@mcp.tool()
def file_chunks(
    source: str,
    start: int = 0,
    end: int = -1,
    project: str = "",
    workspace: str = "",
) -> str:
    """Retrieve indexed chunks of a file by chunk index range.
    Use file_summary first to see how many chunks a file has.

    Args:
        source: Source file path as stored in the KB
        start: First chunk index (0-based, inclusive, default 0)
        end: Last chunk index (inclusive, -1 = all remaining chunks)
        project: Filter by project (optional)
        workspace: Target a specific workspace (optional)
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err

    end_val = None if end < 0 else end

    chunks = vs.get_chunks_by_source(
        source, start=start, end=end_val, project=project, workspace=ws,
    )

    if not chunks:
        return f"No chunks found for: {source}\nUse list_sources to discover indexed files."

    first_idx = chunks[0]["chunk_index"]
    last_idx = chunks[-1]["chunk_index"]
    lines = [f"[{source}] — chunks {first_idx}-{last_idx} ({len(chunks)} total)"]
    lines.append("")

    for c in chunks:
        lines.append(f"--- chunk {c['chunk_index']} ---")
        lines.append(c["content"])
        lines.append("")

    return "\n".join(lines)


def _first_meaningful_line(text: str, max_len: int = 200) -> str:
    """Extract the first meaningful line, skipping headers/frontmatter/code markers."""
    for line in text.split("\n"):
        line = line.strip()
        if not line or len(line) < 10:
            continue
        if line.startswith(("---", "#", "[", "```", "import ", "from ", "const ", "export ")):
            continue
        if line.startswith(("name:", "description:", "type:")):
            continue
        if len(line) > max_len:
            cut = line[:max_len].rfind(" ")
            return line[:cut] + "..." if cut > 0 else line[:max_len] + "..."
        return line
    return text[:max_len].strip()
