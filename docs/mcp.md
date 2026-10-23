---
title: MCP Tools & Automatic Updates
description: The 16 MCP tools Imprint exposes to coding agents — search, graph navigation, source cleanup, inline content ingestion, and more.
---
## MCP Tools

The imprint MCP server exposes **16 tools**. `wake_up` is internal. Compact search is the default: it returns focused evidence without automatically attaching broad session context. Request `detail="full"` to include workspace context on the first such search in a session.

`purge_sources(target, workspace, kind="source", recursive=False, apply=False)` previews a file/directory cleanup. After user confirmation, repeat with `apply=True` and the explicit reviewed workspace. `kind="path"` matches recorded absolute paths; source mode also supports legacy/synced records. Apply deletes indexed copies, removes matching source-linked graph facts and excludes re-ingestion until restored. It is annotated as destructive even though preview is the default. Original files, other workspaces, logs and backups are unaffected. See [workspaces](workspaces.md).

The MCP process pins its default workspace at startup. Restart after switching the global selection, set `IMPRINT_WORKSPACE` in the client environment, or use explicit tool workspace parameters. A running client's default no longer follows another window's workspace switch.

The read-only `device_context(workspace="")` tool reports the current device, process cwd and approved roots. The handshake includes current device/cwd; retrieval labels origin and foreign-versus-local paths. Shared conversations stay searchable across devices. See [device provenance](devices.md).

### Search & retrieval

| Tool | Purpose |
|------|---------|
| `search` | Hybrid semantic/exact-term ranking (`mode="hybrid"`, or `"semantic"`). Bounded excerpts and previews, source hash/mtime, semantic score and match type. Filters: `project`, `type`, `lang`, `layer`, `kind`, `domain`, `source`. Query: 1–4,000 characters; `limit` 1–50 (default 5); `offset` 0–255. `detail="compact"` (default) selects verbatim query-relevant windows; `detail="full"` adds broad context. `max_chars` sets a 1,200–12,000-character response budget (default 4,000), not an exact token count. |
| `get_memory` | Read the full record by the public ID from search/store (legacy point UUIDs also accepted). Content defaults to 4,000 characters; `max_chars` accepts 100–12,000. `start`/`next_start` paginate the original content, with total character count and truncation status. |
| `neighbors` | KNN over embeddings for a given memory id — finds cross-project kin that share the same chunk space. |
| `graph_scope` | Navigate KB as a graph. Scopes: `root`, `project:<name>`, `topic:<name>`, `source:<path>`, `chunk:<id>`. Each response ends with concrete next-step suggestions. |
| `list_sources` | List indexed source files with chunk counts. Filters: `project`, `lang`, `layer`. |
| `file_summary` | Chunk count, tags, mtime, first-chunk preview for one source. Run before `file_chunks` to know the index range. |
| `file_chunks` | Retrieve chunks of a file by 0-based index range (`start`/`end`), at most 20 chunks and a 12,000-character content budget per call. Continuation guidance is included. Use for "show me the full content of this file". |

### Writes

| Tool | Purpose |
|------|---------|
| `store` | Persist a decision/pattern/finding/bug/architecture/milestone/preference before returning `Stored [id]`. Storage failures propagate. Deterministic tags only; supplied comma-separated domain tags are preserved. Blank type uses the local classifier. No forced LLM call. Content: 1–20,000 characters. |
| `delete` | Remove a memory by id (as returned from `search`/`store`). |
| `ingest_url` | Fetch an http(s) URL, extract content, chunk + embed + store. Skips unchanged pages (HEAD check against stored ETag/Last-Modified). |
| `ingest_content` | Chunk + embed + store an inline blob — text, markdown, CSV, JSON, or code (`format="code:python"` etc.). Dedup + replace keyed by `name`: re-sending the same `name` overwrites prior chunks. Valid inline text of 10–99 characters is kept as a single chunk rather than dropped by the file chunker's minimum. Designed for API uploads and external services pushing data into memory. |

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

- `wake_up` — internal, called only by the first `search(detail="full")` per workspace. Compact search avoids its unrelated context.
- `refresh_urls` — admin task; exposed only via CLI as `imprint refresh-urls`.

Search filters map to payload tags — see [tagging.md](./tagging.md) for the full tag schema.

## Automatic Updates

The imprint memory stays current through three mechanisms:

- **Stop hook** (async) — after each Claude response, parses the conversation transcript, extracts Q+A exchanges and decision-like statements, stores them automatically with `lang=conversation` tags. Installed into `~/.claude/settings.json` by `imprint setup`.
- **PreCompact hook** (sync) — before Claude's context window gets compressed, blocks and instructs Claude to save all important context via MCP tools.
- **`imprint refresh <dir>`** — compares complete byte hashes and paths, re-chunks changed sources, and prunes recorded files actually deleted within the selected roots. For URL sources, `imprint refresh-urls` HEAD-checks each stored URL and re-fetches only those with a new ETag or Last-Modified.

Both hooks are installed by `imprint setup` into `~/.claude/settings.json` and removed by `imprint disable`.

## Tool contract and safety

The server declares read-only, destructive and external-access annotations. These hints help clients present operations; they are not access controls. Retrieved text is evidence, not instructions. Verify live source files before edits, especially when provenance is absent or stale. Similarity is not calibrated answer confidence.

`search` reads at most 256 semantic and 256 lexical candidates before rank fusion. This is a bounded heuristic, not BM25 or a guarantee of recall. Semantic-only mode supports comparison. Explicit full-view session context is tracked separately per workspace. Compact responses preserve whole result blocks, IDs, source/device provenance and a continuation that includes any results hidden by the response budget. Expand only relevant evidence; stop once sufficient. See [ADR 0021](adr/0021-budgeted-mcp-evidence-and-selective-expansion.md). MCP calls capture their workspace at entry; queued jobs retain the workspace selected at submission. `ingest_content` accepts at most 1,000,000 characters, name 1,024 and project 128. `kg_query` caps 100 facts; `list_sources` caps 200 sources.

Source replacement prepares/upserts the new chunks before pruning the old set, scoped by project and source. Embedding failures preserve prior data; replacement is not an all-or-nothing transaction. See [indexing and retrieval](indexing-retrieval.md), [ADR 0005](adr/0005-source-lifecycle-and-hybrid-retrieval.md) and [ADR 0006](adr/0006-durable-mcp-and-preserved-client-config.md).
