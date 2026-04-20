# Imprint Memory Layer

Custom lightweight MCP memory server for Claude Code. Qdrant (embedded) + EmbeddingGemma-300M ONNX embeddings + Chonkie hybrid chunker + structured tag payloads.

## Using Imprint (turn-cap rules)

Global CLAUDE.md covers "search before Read". Additional rules specific to this project:

**Specific questions** ("how does X work?", single function, one decision) → 1 `search` call, answer from results. Don't paginate, don't graph_scope, don't re-search. Each extra turn replays MCP schemas through cache.

**Broad/architectural questions** ("overview", "what touches this topic across projects") → budget 3 calls max: `search` → `graph_scope` → `neighbors`. Stop on diminishing returns.

**When search is "Low-confidence"** → dispatch `Explore` subagent for Read/Grep. Do NOT call Read/Grep directly — the subagent runs on Haiku (~10x cheaper) and keeps expensive tool output out of the Sonnet context.

**Never Read a file just to confirm a search result.** Trust the hydrated top-1. If it's wrong, re-query with different keywords or add a filter.

**Storing findings.** Use `store` for decisions/patterns/bugs. Use `kg_edit(op="add", ...)` for facts. Batch at end of turn — don't call store mid-answer.

## MCP Tools (13 total)

| Tool | Purpose |
|------|---------|
| `search` | Semantic search. Auto-hydrates top-1 (or top-3 if low-confidence), rest as index previews. |
| `graph_scope` | Navigate projects/topics/sources/chunks as a graph |
| `neighbors` | KNN over embeddings for one memory id |
| `store` | Store a single memory (decision/pattern/bug/etc) |
| `delete` | Remove a memory by id |
| `kg_query` | Query temporal facts |
| `kg_edit` | `op="add"` or `op="end"` for facts |
| `status` | Overview stats |
| `list_sources` | Indexed files + chunk counts |
| `file_summary` | One file: chunk count, tags, preview |
| `file_chunks` | Chunks of a file by index range |
| `ingest_url` | Fetch + chunk + store a URL |
| `ingest_content` | Chunk + store an inline blob (text/markdown/csv/json/`code:<lang>`). Dedup + replace by `name`. |

`wake_up` is internal — `search` auto-loads session context on first call.
`refresh_urls` is CLI-only (`imprint refresh-urls`).

## Cross-Project Knowledge

Search without `project` filter when implementing reusable patterns (auth, caching, DB access). Store cross-project findings with `type: "pattern"`.

## Architecture

- **Vector store**: Qdrant server, auto-spawned local daemon on `127.0.0.1:6333` via [`qdrant_runner.py`](imprint/qdrant_runner.py). Int8 scalar quantization, on-disk payload, payload indexes on `project`/`type`/`source`/`tags.*`
- **Embeddings**: EmbeddingGemma-300M via ONNX Runtime (768-dim, 2048 ctx). Configure via `imprint config set model.name/dim/pooling`. Auto GPU if available.
- **Chunker**: Chonkie hybrid — `CodeChunker` (tree-sitter) for code, `SemanticChunker` for prose. Sliding overlap.
- **Tagger**: `{lang, layer, kind, domain[], topics[]}`. Deterministic + keyword dict always on; zero-shot on by default; LLM tagging opt-in (`IMPRINT_LLM_TAGS=1`). Providers: anthropic/openai/ollama/vllm/gemini. Default local: Qwen3 1.7B Q4_K_M.
- **Imprint graph**: SQLite temporal facts (per workspace)
- **Command queue**: Single-slot FIFO in [`imprint/queue.py`](imprint/queue.py) backed by `data/queue.sqlite3`; a shared advisory flock on `data/queue.lock` (see [`queue_lock.py`](imprint/queue_lock.py) and Go-side [`internal/queuelock`](internal/queuelock/lock.go)) blocks concurrent ingest/refresh/retag/ingest-url starts across the CLI and the API. FastAPI dispatcher spawns subprocesses with `start_new_session=True`; cancel fires SIGTERM to the process group and escalates to SIGKILL after 3s, taking down the LLM tagger's httpx calls and llama-cpp threads alongside the parent. CLI callers exit nonzero with the current holder's PID when the lock is busy. Details: [docs/queue.md](docs/queue.md).
- **Workspaces**: isolated — own collection (`memories_{name}`), DB (`imprint_graph_{name}.sqlite3`), WAL. Config in `data/workspace.json`.
- **Data**: `data/qdrant_storage/` + `data/qdrant-bin/` + `data/imprint_graph*.sqlite3` + `data/workspace.json` (gitignored)
- **Lifecycle**: `imprint enable` / `disable` / `status`

## Configuration

`imprint config set <key> <value>` — persists to `data/config.json`. Precedence: env var > config.json > default. `imprint config` shows all.

Key settings: `model.name`, `model.dim`, `model.device`, `qdrant.host/port`, `chunker.overlap/size_code/size_prose/hard_max`, `tagger.llm/llm_provider/llm_model`. Env vars `IMPRINT_*` take priority. `IMPRINT_QDRANT_BIN` and `IMPRINT_DATA_DIR` are env-only.

## Project Detection

Canonical name from manifest files (package.json, go.mod, etc.), not path. Same project on different machines = same identity.

## CLI

```bash
imprint setup              # install deps, register MCP, configure Claude Code
imprint status             # enabled/disabled, server pid, memory stats
imprint enable [target]    # re-wire MCP + hooks + start server
imprint disable            # stop server, unregister MCP, strip hooks
imprint ingest <dir>       # index project source files
imprint learn              # index Claude Code conversations + memory files
imprint learn --desktop    # also index Claude Desktop / ChatGPT Desktop export zips
imprint refresh <dir>      # re-index changed files only
imprint refresh-urls       # re-fetch stored URLs by ETag/Last-Modified
imprint config [set|get|reset] ...
imprint server <cmd>       # Qdrant daemon: start | stop | status | log
imprint ui [start|stop|status|open|restart|log]
imprint workspace [list|switch|delete]
imprint wipe [--force|--all]
imprint sync serve --relay <host> | <host>/<id>
imprint version
```
