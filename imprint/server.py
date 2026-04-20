"""Imprint MCP Server — lightweight memory for Claude Code."""

import sys
import threading
import traceback

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
SEARCH_MAX_CONTENT_CHARS = 1500  # per-result full-body truncation when hydrated
SEARCH_MAX_TOTAL_CHARS = 12000   # hard cap on full search output

# Auto-hydrate: full body for top-N, preview for rest
SEARCH_PREVIEW_CHARS = 180       # per-result preview length in index mode
SEARCH_HYDRATE_BUDGET_CHARS = 2000  # stop hydrating once cumulative exceeds this
SEARCH_HIGH_CONF_SIM = 0.60      # hydrate top-1 only above this
SEARCH_LOW_CONF_SIM = 0.35       # hydrate top-3 below this for context


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


def wake_up(workspace: str = "") -> str:
    """Internal: build session-start context. Called automatically by search()
    on first invocation; no longer exposed as an MCP tool to save schema bytes.

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
    """Semantic search across stored memories. Call BEFORE Read/Grep.
    Returns top-1 fully hydrated, rest as previews. Similarity-budgeted.

    Args:
        query: Natural-language query
        project: Filter by project (optional)
        type: Filter by type: decision, pattern, finding, bug, architecture (optional)
        lang: Filter by lang tag: python, typescript, go, markdown (optional)
        layer: Filter by layer: api, ui, tests, infra, config, docs (optional)
        kind: Filter by kind: source, test, migration, readme (optional)
        domain: Filter by domain(s), comma-separated (optional)
        limit: Max results (default 10)
        offset: Skip first N for pagination
        workspace: Target workspace (optional)
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

    # ── Confidence + hydration policy ──
    # High conf (top_sim >= HIGH): hydrate top-1 only. User can follow up by id.
    # Low conf (top_sim < LOW): hydrate top-3 for extra context (likely need more).
    # Mid: hydrate top-2. Always budget-cap at SEARCH_HYDRATE_BUDGET_CHARS cumulative.
    top_sim = relevant[0]["similarity"] if relevant else 0.0
    if top_sim >= SEARCH_HIGH_CONF_SIM:
        hydrate_target = 1
        lines.append("High-confidence — top result hydrated. Answer from it.\n")
    elif top_sim < SEARCH_LOW_CONF_SIM:
        hydrate_target = 3
        lines.append("Low-confidence matches — top 3 hydrated. Consider Read/Grep via Explore subagent if still thin.\n")
    else:
        hydrate_target = 2

    hydrated_chars = 0
    for i, r in enumerate(relevant, 1):
        meta = []
        if r["project"]:
            meta.append(r["project"])
        if r["source"]:
            meta.append(r["source"])
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

        rid = r.get("id") or ""
        header = f"[{i}] id:{rid}  {meta_str}  (sim: {r['similarity']:.3f})"
        lines.append(header)
        if r.get("type") == "pattern":
            lines.append(f"  [cross-project pattern from {r.get('project', '?')}]")

        content = r["content"]
        should_hydrate = (
            i <= hydrate_target
            and hydrated_chars + min(len(content), SEARCH_MAX_CONTENT_CHARS) <= SEARCH_HYDRATE_BUDGET_CHARS + SEARCH_MAX_CONTENT_CHARS
        )
        if should_hydrate:
            if len(content) > SEARCH_MAX_CONTENT_CHARS:
                source = r.get("source", "")
                hint = f'\n  … [truncated — file_chunks(source="{source}") for full]' if source else "\n  … [truncated]"
                content = content[:SEARCH_MAX_CONTENT_CHARS] + hint
            lines.append(content)
            hydrated_chars += len(content)
        else:
            # Index-only preview
            preview = _first_meaningful_line(r["content"], max_len=SEARCH_PREVIEW_CHARS)
            lines.append(f"  · {preview}")
        lines.append("")

    # Hint about more results when we hit the limit
    next_offset = offset + limit
    if len(relevant) == limit:
        lines.append(
            f"({len(relevant)} results shown. More may exist — call again with offset={next_offset}.)"
        )

    # Follow-up hints so the model keeps exploring instead of stopping here.
    top_projects: dict[str, int] = {}
    top_topics: dict[str, int] = {}
    for r in relevant:
        p = r.get("project") or ""
        if p:
            top_projects[p] = top_projects.get(p, 0) + 1
        for t in ((r.get("tags") or {}).get("topics") or [])[:3]:
            if t:
                top_topics[t] = top_topics.get(t, 0) + 1

    followups: list[str] = []
    if top_projects:
        proj = max(top_projects, key=top_projects.get)
        followups.append(f'graph_scope("project:{proj}")')
    if top_topics:
        topic = max(top_topics, key=top_topics.get)
        followups.append(f'graph_scope("topic:{topic}")')
    if relevant:
        first_id = relevant[0].get("id") or ""
        if first_id:
            followups.append(f'neighbors(id="{first_id}")')

    if followups:
        lines.append("")
        lines.append("Follow up if this is incomplete: " + "  |  ".join(followups))

    output = prefix + "\n".join(lines)
    if len(output) > SEARCH_MAX_TOTAL_CHARS:
        output = output[:SEARCH_MAX_TOTAL_CHARS] + (
            f"\n\n… [output truncated — {len(relevant)} results matched. "
            f"Use offset={next_offset} to see more, or add filters to narrow results]"
        )
    return output


def _store_background(
    content: str,
    project: str,
    type_hint: str,
    source: str,
    workspace: str | None,
) -> None:
    """Run the slow parts of store (embed + LLM tag + upsert) off-thread.

    Runs the full LLM tagging pipeline (same as refresh's phase 2) and stamps
    ``llm_tagged: True`` so a subsequent ``retag`` won't re-run the LLM on
    this point.  Exceptions are logged to stderr — the MCP tool already
    returned, so we can't surface the error to the caller.
    """
    try:
        from . import tagger
        new_tags = tagger.build_payload_tags(
            content,
            rel_path=source,
            llm=True,
            project_hint=project,
            workspace=workspace,
        )
        llm_type = new_tags.pop("_llm_type", "")
        mem_type = type_hint or llm_type or "architecture"

        vs.store(
            content=content,
            project=project,
            type=mem_type,
            tags=new_tags,
            source=source,
            workspace=workspace,
        )

        if llm_type:
            client, coll = vs._ensure_collection(workspace)
            memory_id = vs._make_id(content, project, source)
            client.set_payload(
                collection_name=coll,
                payload={"llm_tagged": True},
                points=[vs._point_uuid(memory_id)],
            )
    except Exception as e:
        print(f"imprint store background error: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)


@mcp.tool()
def store(
    content: str,
    project: str = "",
    type: str = "",
    tags: str = "",
    source: str = "",
    workspace: str = "",
) -> str:
    """Store a memory (decision/pattern/finding/bug). Include the WHY.
    Returns immediately; embed + tag run in background.

    Args:
        content: The note — specific, reasoning included
        project: Project name (optional)
        type: decision, pattern, finding, bug, architecture, milestone (auto if blank)
        tags: ignored (LLM tagger derives topics)
        source: File path or conversation topic (optional)
        workspace: Target workspace (optional)
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err

    memory_id = vs._make_id(content, project, source)

    threading.Thread(
        target=_store_background,
        args=(content, project, type, source, ws),
        daemon=True,
    ).start()

    suffix = f" as {type}" if type else " — tagging in background"
    return f"Queued [{memory_id}]{suffix}"


@mcp.tool()
def delete(memory_id: str, workspace: str = "") -> str:
    """Delete a memory by ID.

    Args:
        memory_id: Memory id from search or store output
        workspace: Target workspace (optional)
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err
    if vs.delete(memory_id, workspace=ws):
        return f"Deleted {memory_id}"
    return f"Not found: {memory_id}"


@mcp.tool()
def kg_query(subject: str = "", predicate: str = "", limit: int = 20, workspace: str = "") -> str:
    """Query temporal facts (subject → predicate → object).

    Args:
        subject: Entity (partial match)
        predicate: Relationship (partial match)
        limit: Max results
        workspace: Target workspace (optional)
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
def kg_edit(
    op: str = "add",
    subject: str = "",
    predicate: str = "",
    object: str = "",
    fact_id: int = 0,
    source: str = "",
    workspace: str = "",
) -> str:
    """Add or end a structured fact.

    Args:
        op: "add" (default) or "end"
        subject/predicate/object: Required for op=add
        fact_id: Required for op=end (from kg_query)
        source: Where fact came from (op=add only)
        workspace: Target workspace (optional)
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err
    if op == "end":
        if kg.invalidate(fact_id, workspace=ws):
            return f"Ended fact {fact_id}"
        return f"Not found or already ended: {fact_id}"
    if not (subject and predicate and object):
        return "op=add requires subject, predicate, object"
    fid = kg.add(subject=subject, predicate=predicate, object=object, source=source, workspace=ws)
    return f"Fact [{fid}]: {subject} → {predicate} → {object}"


@mcp.tool()
def ingest_url(url: str, project: str = "urls", force: bool = False, workspace: str = "") -> str:
    """Fetch a URL, extract/chunk/store as memories. Skips unchanged ETag.

    Args:
        url: http(s) URL
        project: Project label (default "urls")
        force: Re-fetch even if unchanged
        workspace: Target workspace (optional)
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


# Extension map for format="code:<lang>" → chunker dispatch hint. The
# chunker reads rel_path's extension to pick the tree-sitter grammar, so
# we synthesize a fake file name with the matching ext.
_CODE_FORMAT_EXT = {
    "python": ".py", "typescript": ".ts", "javascript": ".js",
    "go": ".go", "rust": ".rs", "java": ".java", "php": ".php",
    "swift": ".swift", "sql": ".sql", "vue": ".vue", "svelte": ".svelte",
    "c": ".c", "cpp": ".cpp", "csharp": ".cs", "ruby": ".rb",
    "kotlin": ".kt", "scala": ".scala", "html": ".html", "css": ".css",
}


@mcp.tool()
def ingest_content(
    content: str,
    name: str,
    format: str = "",
    project: str = "ingested",
    workspace: str = "",
) -> str:
    """Ingest an inline content blob (text, markdown, CSV, JSON, code) as memories.

    Unlike ``store`` (which creates a single memory) or ``ingest_url`` (which
    fetches bytes over HTTP), this chunks and embeds a blob you pass in —
    useful for API uploads, pasted transcripts, log snippets, or anything
    your agent already has in memory. Chunk + dedup semantics match
    ``ingest_url``: re-sending the same ``name`` replaces prior chunks.

    Args:
        content: Raw content to ingest.
        name: Logical source id used for dedup + replace. Stable across
              re-ingests of the same logical document.
        format: Content format hint. One of: "" (auto → text), "text",
                "markdown", "csv", "json", or "code:<lang>" (e.g.
                "code:python", "code:typescript"). Picks the chunker mode
                and file-extension hint.
        project: Project label (default "ingested").
        workspace: Target workspace (optional).
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err
    if not content or not content.strip():
        return "Empty content"
    if not name or not name.strip():
        return "name is required (used for dedup + replace)"

    fmt = (format or "").lower().strip()
    chunk_mode = "prose"
    rel_path = name
    source_type = "content"
    prepared = content

    if fmt.startswith("code:"):
        lang = fmt.split(":", 1)[1] or "python"
        ext = _CODE_FORMAT_EXT.get(lang, ".py")
        rel_path = f"{name}{ext}"
        chunk_mode = "code"
        source_type = "code"
    elif fmt == "json":
        # Pretty-print so semantic chunker sees structural boundaries.
        try:
            import json as _json
            prepared = _json.dumps(_json.loads(content), indent=2, ensure_ascii=False)
        except Exception:
            pass  # not valid JSON — ingest as-is
        rel_path = f"{name}.json"
        source_type = "json"
    elif fmt == "csv":
        rel_path = f"{name}.csv"
        source_type = "csv"
    elif fmt == "markdown":
        rel_path = f"{name}.md"
    elif fmt in ("", "text"):
        rel_path = f"{name}.txt"
    else:
        # Unknown format — fall back to prose over the raw bytes.
        pass

    from .ingest_core import store_inline_content
    n, status = store_inline_content(
        prepared,
        source_key=name,
        project=project,
        chunk_mode=chunk_mode,
        source_type=source_type,
        rel_path=rel_path,
        workspace=ws,
    )

    if status == "stored":
        return f"Stored {n} chunks from '{name}' (project={project}, format={fmt or 'text'})"
    if status == "skipped-empty":
        return "Empty content"
    return f"Failed: {status}"


def refresh_urls(project: str = "", workspace: str = "") -> str:
    """Internal: re-check stored URLs via HEAD, refetch changed.
    Exposed via CLI (imprint refresh-urls), not MCP — admin task."""
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
    """Memory overview: total count + per-project breakdown.

    Args:
        workspace: Target workspace (optional)
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
    """List indexed source files with chunk counts.

    Args:
        project: Filter by project (optional)
        lang: Filter by lang (python/typescript/go) (optional)
        layer: Filter by layer (api/ui/tests/infra/config/docs) (optional)
        limit: Max sources (default 50)
        workspace: Target workspace (optional)
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
    """Overview of an indexed file: chunk count, tags, mtime, preview.

    Args:
        source: Source path as stored in KB
        project: Disambiguate by project (optional)
        workspace: Target workspace (optional)
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
def graph_scope(scope: str = "root", depth: int = 1, workspace: str = "") -> str:
    """Navigate KB as graph by project/topic/source/chunk. Use when search
    is too narrow or for overview.

    Args:
        scope: root, project:<name>, topic:<name>, source:<path>, chunk:<id>
        depth: 1–3, higher = more nodes
        workspace: Target workspace (optional)
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err
    from .cli_viz import build_graph_scope

    depth = max(1, min(int(depth or 1), 3))
    data = build_graph_scope(scope or "root", depth=depth, workspace=ws)
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    by_kind: dict[str, list] = {"project": [], "topic": [], "source": [], "chunk": []}
    for n in nodes:
        by_kind.setdefault(n.get("kind", ""), []).append(n)

    center = data.get("center", "") or ""
    scope_str = data.get("scope", scope or "root")
    lines = [
        f"Scope: {scope_str} (depth={depth})",
        f"Nodes: {len(nodes)}  |  Edges: {len(edges)}"
        + (f"  |  Center: {center}" if center else ""),
    ]

    # Adjacency index (weight-sorted) for short edge hints
    adj: dict[str, list[tuple[str, int, str]]] = {}
    for e in edges:
        adj.setdefault(e["source"], []).append((e["target"], e["weight"], e["kind"]))
        adj.setdefault(e["target"], []).append((e["source"], e["weight"], e["kind"]))
    for k in adj:
        adj[k].sort(key=lambda t: -t[1])

    def _peers(node_id: str, limit: int = 3) -> str:
        peers = adj.get(node_id, [])[:limit]
        return ", ".join(p.split(":", 1)[-1] for p, _, _ in peers) if peers else ""

    for kind_label, items in [
        ("Projects", by_kind.get("project", [])),
        ("Topics", by_kind.get("topic", [])),
        ("Sources", by_kind.get("source", [])),
        ("Chunks", by_kind.get("chunk", [])),
    ]:
        if not items:
            continue
        shown = items[:20]
        lines.append(f"\n{kind_label} ({len(shown)} of {len(items)}):")
        for n in shown:
            link = _peers(n["id"])
            suffix = f"  → {link}" if link else ""
            lines.append(f"  [{n['id']}] {n['label']}  ({n.get('count', 0)}){suffix}")
        if len(items) > len(shown):
            lines.append(f"  … +{len(items) - len(shown)} more (increase depth to see them)")

    # Follow-up hint — always present so the model keeps exploring
    suggestions: list[str] = []
    if scope_str == "root" or scope_str.startswith("root"):
        sample_proj = by_kind.get("project", [])
        sample_topic = by_kind.get("topic", [])
        if sample_proj:
            suggestions.append(f'graph_scope("project:{sample_proj[0]["label"]}")')
        if sample_topic:
            suggestions.append(f'graph_scope("topic:{sample_topic[0]["label"]}")')
    elif scope_str.startswith("project:"):
        for t in by_kind.get("topic", [])[:2]:
            suggestions.append(f'graph_scope("topic:{t["label"]}")')
        for s in by_kind.get("source", [])[:1]:
            full = s.get("fullPath") or s["label"]
            suggestions.append(f'graph_scope("source:{full}")')
    elif scope_str.startswith("topic:"):
        for p in by_kind.get("project", [])[:2]:
            suggestions.append(f'graph_scope("project:{p["label"]}")')
        for s in by_kind.get("source", [])[:1]:
            full = s.get("fullPath") or s["label"]
            suggestions.append(f'graph_scope("source:{full}")')
    elif scope_str.startswith("source:"):
        for c in by_kind.get("chunk", [])[:1]:
            mid = c["id"].split(":", 1)[-1]
            suggestions.append(f'graph_scope("chunk:{mid}")')
            suggestions.append(f"file_chunks(source=\"{scope_str.split(':', 1)[-1]}\", start=0, end=5)")
    elif scope_str.startswith("chunk:"):
        mid = scope_str.split(":", 1)[-1]
        suggestions.append(f'neighbors(id="{mid}", k=10)')
        suggestions.append(f'get_memory("{mid}")')

    if suggestions:
        lines.append("\nNext steps: " + "  |  ".join(suggestions))
    lines.append(
        "Don't stop here — iterate. Every node id is callable as a new scope."
    )
    return "\n".join(lines)


@mcp.tool()
def neighbors(id: str, k: int = 10, workspace: str = "") -> str:
    """KNN semantic neighbors of a memory. Use after search for cross-project kin.

    Args:
        id: Memory id from search/graph_scope
        k: Max neighbors (default 10)
        workspace: Target workspace (optional)
    """
    ws, err = _validate_ws(workspace)
    if err:
        return err
    from .cli_viz import get_neighbors

    mid = id.split(":", 1)[-1] if id.startswith("chunk:") else id
    data = get_neighbors(mid, k=max(1, min(int(k or 10), 30)), workspace=ws)
    nbs = (data or {}).get("neighbors", []) if isinstance(data, dict) else []
    if not nbs:
        return f"No neighbors for {mid}. Try `get_memory` to confirm the id exists."

    lines = [f"Neighbors of {mid} (top {len(nbs)}):"]
    for n in nbs:
        meta = []
        if n.get("project"):
            meta.append(n["project"])
        if n.get("type"):
            meta.append(n["type"])
        if n.get("source"):
            meta.append(n["source"])
        meta_str = " | ".join(meta)
        lines.append(
            f"[{n.get('similarity', 0):.3f}] {n.get('id', '')}  {meta_str}"
        )
        content = (n.get("content") or "").strip().splitlines()
        preview = next((ln.strip() for ln in content if ln.strip()), "")
        if preview:
            lines.append(f"  {preview[:200]}")
    lines.append(
        "\nNext: `graph_scope(\"chunk:<id>\")` to see the cluster, "
        "or `search(query, offset=N)` to keep paginating."
    )
    return "\n".join(lines)


@mcp.tool()
def file_chunks(
    source: str,
    start: int = 0,
    end: int = -1,
    project: str = "",
    workspace: str = "",
) -> str:
    """Retrieve indexed chunks of a file by index range. Run file_summary first for count.

    Args:
        source: Source path as stored in KB
        start: First chunk index (0-based, inclusive)
        end: Last chunk index (inclusive, -1 = rest)
        project: Filter by project (optional)
        workspace: Target workspace (optional)
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
