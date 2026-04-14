"""Imprint MCP Server — lightweight memory for Claude Code."""

from collections import defaultdict

from fastmcp import FastMCP

from . import imprint_graph as kg
from . import vectorstore as vs

mcp = FastMCP("imprint")

# Release the embedded Qdrant lock after 30s of no MCP activity so
# `imprint ingest` (a separate process) can grab it. Without this, the
# MCP server pins the lock for the lifetime of Claude Code.
vs.release_idle_client(after_seconds=float(__import__("os").environ.get("IMPRINT_MCP_IDLE_S", "30")))

# Similarity threshold (0-1 scale, higher = better). Below this = noise.
RELEVANCE_THRESHOLD = 0.2

# L1 Essential Story limits (from MemPalace's proven defaults)
L1_MAX_ENTRIES = 15
L1_MAX_CHARS = 3200  # ~800 tokens


@mcp.tool()
def wake_up() -> str:
    """Load prior context at the start of a conversation.
    Call this FIRST in every session. Returns project overview, essential decisions/patterns, and active facts."""
    stats = vs.status()
    facts = kg.recent(limit=10)
    lines = []

    # Project overview
    if stats["by_project"]:
        lines.append("Projects in imprint memory:")
        for p, c in sorted(stats["by_project"].items(), key=lambda x: -x[1]):
            lines.append(f"  {p} ({c} memories)")

    # L1 Essential Story — top decisions, patterns, preferences, bugs
    # Scored by type importance, capped at ~800 tokens
    recent = vs.recent(limit=50, types=["decision", "pattern", "preference", "bug", "milestone", "architecture"])
    if recent:
        # Score by type importance
        type_weight = {"decision": 5, "preference": 4, "pattern": 3, "bug": 3, "architecture": 2, "milestone": 2, "finding": 1}
        scored = [(type_weight.get(r["type"], 1), r) for r in recent]
        scored.sort(key=lambda x: -x[0])

        # Group by project, cap at L1 limits
        by_project = defaultdict(list)
        for _, r in scored[:L1_MAX_ENTRIES]:
            p = r["project"] or "(general)"
            by_project[p].append(r)

        lines.append("\nEssential context:")
        total_chars = 0
        for project, entries in sorted(by_project.items()):
            for r in entries:
                summary = _first_meaningful_line(r["content"], max_len=200)
                entry = f"  • [{r['type']}] {summary}"
                if r["source"]:
                    entry += f"  ({r['source']})"
                if total_chars + len(entry) > L1_MAX_CHARS:
                    lines.append("  ... (use search for more)")
                    break
                lines.append(entry)
                total_chars += len(entry)
            else:
                continue
            break

    # Active facts
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
) -> str:
    """Semantic search across stored memories.
    Check this BEFORE reading files — the answer may already be here.
    Returns relevant code chunks, decisions, and patterns.

    Args:
        query: What to search for (natural language)
        project: Filter by project name (optional)
        type: Filter by type: decision, pattern, finding, preference, bug, architecture (optional)
        lang: Filter by language tag (python, typescript, go, php, markdown, conversation, ...)
        layer: Filter by layer (api, ui, tests, infra, config, migrations, docs, scripts, cli, session)
        kind: Filter by file kind (source, test, migration, readme, types, module, qa, auto-extract)
        domain: Filter by domain tag, comma-separated for multi-match (auth, db, api, math, rendering, ui, testing, infra, ml, perf, security, build, payments)
        limit: Max results (default 10)
    """
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
        query, limit=limit, project=project, type=type,
        tag_filters=tag_filters or None,
    )

    if not results:
        return "No results. Try reading the relevant files directly."

    # Filter by similarity threshold
    relevant = [r for r in results if r["similarity"] >= RELEVANCE_THRESHOLD]
    if not relevant:
        return "No relevant matches found. Try reading the relevant files directly."

    lines = []
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
        lines.append(r["content"])
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
def store(
    content: str,
    project: str = "",
    type: str = "",
    tags: str = "",
    source: str = "",
) -> str:
    """Store a memory. Write it as a self-contained note that will make sense
    months from now without additional context. Include the WHY, not just the WHAT.

    Args:
        content: The knowledge to remember — be specific, include reasoning
        project: Project this relates to (e.g. 'commute-api', 'editor-2d')
        type: One of: decision, pattern, finding, preference, bug, architecture, milestone
        tags: Comma-separated tags (e.g. 'cors,security')
        source: Where this came from (e.g. file path, conversation topic)
    """
    # Auto-classify type if not provided
    if not type:
        from . import classifier
        type, _ = classifier.classify(content)

    memory_id = vs.store(
        content=content, project=project, type=type, tags=tags, source=source
    )
    return f"Stored [{memory_id}] as {type}"


@mcp.tool()
def delete(memory_id: str) -> str:
    """Delete a memory by its ID.

    Args:
        memory_id: The memory ID from store or search results
    """
    if vs.delete(memory_id):
        return f"Deleted {memory_id}"
    return f"Not found: {memory_id}"


@mcp.tool()
def kg_query(subject: str = "", predicate: str = "", limit: int = 20) -> str:
    """Query temporal facts from the imprint graph.

    Args:
        subject: Entity to look up (partial match)
        predicate: Relationship type (partial match)
        limit: Max results
    """
    facts = kg.query(subject=subject, predicate=predicate, limit=limit)

    if not facts:
        return "No facts found."

    lines = []
    for f in facts:
        ended = " [ENDED]" if f["ended"] else ""
        lines.append(f"  {f['subject']} → {f['predicate']} → {f['object']}{ended}")

    return "\n".join(lines)


@mcp.tool()
def kg_add(subject: str, predicate: str, object: str, source: str = "") -> str:
    """Add a structured fact. Use for relationships that may change over time.

    Args:
        subject: The entity (e.g. 'commute-api', 'Hunter')
        predicate: The relationship (e.g. 'uses', 'decided', 'prefers')
        object: The value (e.g. 'NestJS', 'wildcard CORS')
        source: Where this fact came from
    """
    fact_id = kg.add(subject=subject, predicate=predicate, object=object, source=source)
    return f"Fact [{fact_id}]: {subject} → {predicate} → {object}"


@mcp.tool()
def kg_invalidate(fact_id: int) -> str:
    """Mark a fact as ended (no longer true).

    Args:
        fact_id: The fact ID from kg_query
    """
    if kg.invalidate(fact_id):
        return f"Ended fact {fact_id}"
    return f"Not found or already ended: {fact_id}"


@mcp.tool()
def status() -> str:
    """Imprint memory overview."""
    stats = vs.status()
    facts = kg.query(limit=1000)
    active = sum(1 for f in facts if not f["ended"])

    lines = [f"Memories: {stats['total_memories']}  |  Facts: {active}"]
    if stats["by_project"]:
        projects = ", ".join(
            f"{p}({c})" for p, c in sorted(stats["by_project"].items(), key=lambda x: -x[1])
        )
        lines.append(f"Projects: {projects}")

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
