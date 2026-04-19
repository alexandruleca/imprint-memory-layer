---
title: MCP Tools & Automatic Updates
---

# MCP Tools & Automatic Updates

## MCP Tools

The imprint MCP server exposes **12 tools**. `wake_up` is internal now — `search` auto-calls it on the first invocation of a session so you get project overview + essential context + recent activity for free, without burning a second schema slot.

### Search & retrieval

| Tool | Purpose |
|------|---------|
| `search` | Semantic search. Hydrates top-1 fully when confidence is high (sim ≥ 0.60), top-3 when low (< 0.35), otherwise top-2. Rest are index-only previews. Auto-loads wake-up context on first call. Filters: `project`, `type`, `lang`, `layer`, `kind`, `domain`. Pagination via `offset`. |
| `neighbors` | KNN over embeddings for a given memory id — finds cross-project kin that share the same chunk space. |
| `graph_scope` | Navigate KB as a graph. Scopes: `root`, `project:<name>`, `topic:<name>`, `source:<path>`, `chunk:<id>`. Each response ends with concrete next-step suggestions. |
| `list_sources` | List indexed source files with chunk counts. Filters: `project`, `lang`, `layer`. |
| `file_summary` | Chunk count, tags, mtime, first-chunk preview for one source. Run before `file_chunks` to know the index range. |
| `file_chunks` | Retrieve chunks of a file by 0-based index range (`start`/`end`). Use for "show me the full content of this file". |

### Writes

| Tool | Purpose |
|------|---------|
| `store` | Save a decision/pattern/finding/bug/architecture/milestone. Returns immediately; embed + LLM tagging + upsert run on a background thread. Stores from MCP always use the LLM tagger regardless of `tagger.llm` config — the assumption is that an explicit save deserves good topic tags. |
| `delete` | Remove a memory by id (as returned from `search`/`store`). |
| `ingest_url` | Fetch an http(s) URL, extract content, chunk + embed + store. Skips unchanged pages (HEAD check against stored ETag/Last-Modified). |

### Knowledge graph

| Tool | Purpose |
|------|---------|
| `kg_query` | Query temporal facts. Partial-matches `subject` and/or `predicate`. |
| `kg_edit` | Mutate a fact. `op="add"` requires `subject`/`predicate`/`object`. `op="end"` marks a fact as no longer valid (requires `fact_id`). |

### Status

| Tool | Purpose |
|------|---------|
| `status` | Memory count, active-facts count, per-project breakdown. |

Every tool accepts an optional `workspace` argument — omit it to target the active workspace (`data/workspace.json`).

### Not exposed as MCP

- `wake_up` — auto-called by the first `search` in a session, no longer a separate tool (saves schema bytes in Claude's context).
- `refresh_urls` — admin task; exposed only via CLI as `imprint refresh-urls`.

Search filters map to payload tags — see [tagging.md](./tagging.md) for the full tag schema.

## Automatic Updates

The imprint memory stays current through three mechanisms:

- **Stop hook** (async) — after each Claude response, parses the conversation transcript, extracts Q+A exchanges and decision-like statements, stores them automatically with `lang=conversation` tags. Installed into `~/.claude/settings.json` by `imprint setup`.
- **PreCompact hook** (sync) — before Claude's context window gets compressed, blocks and instructs Claude to save all important context via MCP tools.
- **`imprint refresh <dir>`** — compares file mtimes via the indexed `source_mtime` payload, re-chunks + re-embeds only what changed. For URL sources, `imprint refresh-urls` HEAD-checks each stored URL and re-fetches only those with a new ETag or Last-Modified.

Both hooks are installed by `imprint setup` into `~/.claude/settings.json` and removed by `imprint disable`.
