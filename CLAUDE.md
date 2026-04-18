# Imprint Memory Layer

Custom lightweight MCP memory server for Claude Code. Qdrant (embedded) + EmbeddingGemma-300M ONNX embeddings + Chonkie hybrid chunker + structured tag payloads.

## IMPORTANT: Use Imprint Before Reading Files

**At the start of every conversation**, call `mcp__imprint__search` with your first question.
It auto-loads session context (wake_up) on the first call — no need to call `wake_up` separately.

**Trust search results when confidence is high.** Search results include confidence guidance:
- "High-confidence results" → answer directly, do NOT read files
- No prefix → results are adequate, read files only if answer is incomplete
- "Low-confidence matches" → search was insufficient, fall back to Read/Grep

**Only fall back to Read/Grep when:**
- Search returns no results or low-confidence matches
- You need *exact current content* for making edits (not for understanding)
- The user explicitly asks you to read a file

**During conversation**, store important findings:
- `mcp__imprint__store` — decisions, patterns, findings, preferences, bugs
- `mcp__imprint__kg_add` — structured facts (subject → predicate → object)

## Iterative Exploration — Keep Going

Never stop after the first `search` call when a question is broad, incomplete, or ambiguous. The KB is large; one batch of 10 results is rarely the full picture. Loop through these tools until you have what you need:

1. **`search`** — pull the most relevant chunks. Output ends with a `Follow up if this is incomplete: ...` line that names the exact next call to make.
2. **`search` again** with `offset=` when the hint says "More may exist" — paginate until results become irrelevant.
3. **`graph_scope(scope, depth)`** — navigate the graph instead of cosine similarity:
   - `graph_scope("root")` — top projects + topics with co-occurrence edges
   - `graph_scope("project:<name>")` — topics + sources inside that project
   - `graph_scope("topic:<name>")` — every project/source that touches the topic
   - `graph_scope("source:<path>")` — chunks in order + topics per chunk
   - `graph_scope("chunk:<id>")` — semantic kin of a single chunk
4. **`neighbors(id, k)`** — KNN over embeddings for a specific memory. Great for cross-project pattern discovery when tags don't line up.
5. **`file_summary` → `file_chunks`** when you need a whole file, not isolated chunks.

Every `graph_scope` call returns a `Next steps:` line. Treat those as concrete prompts — call one of them when the current scope didn't answer the question. Budget 3–6 exploration calls for broad questions. Stop when diminishing returns, not when the first batch arrives.

## MCP Tools (13 total)

| Tool | Purpose |
|------|---------|
| `wake_up` | Load summary context at session start |
| `search` | Semantic search with optional filters and offset-based pagination |
| `graph_scope` | Navigate projects/topics/sources/chunks as a graph (drill-down) |
| `neighbors` | KNN over embeddings for a specific memory id |
| `store` | Store a memory with metadata |
| `delete` | Remove a memory by ID |
| `kg_query` | Query temporal facts |
| `kg_add` | Add a structured fact |
| `kg_invalidate` | Mark a fact as ended |
| `status` | Show overview stats |
| `list_sources` | List indexed source files with chunk counts |
| `file_summary` | Quick overview of an indexed file (chunks, tags, preview) |
| `file_chunks` | Retrieve specific chunks of a file by index range |

## File Retrieval Workflow

When you need file content, prefer the KB over filesystem reads:

1. `list_sources` — discover what files are indexed (filter by project/lang/layer)
2. `file_summary` — check if a specific file is indexed, see chunk count and preview
3. `file_chunks` — retrieve the actual content by chunk range
4. Only fall back to `Read`/`Grep` if the file is not indexed or you need exact byte-level content for edits

This avoids redundant filesystem reads for files already in the knowledge base.

## Cross-Project Knowledge

When implementing common architecture (auth, caching, DB access, API patterns), search **without** a project filter to find existing patterns from other projects. The same architectural approach often applies across languages and codebases.

Store reusable patterns with `type: "pattern"` so they surface in cross-project searches.

## Architecture

- **Vector store**: Qdrant server, auto-spawned local daemon on `127.0.0.1:6333` via [`qdrant_runner.py`](imprint/qdrant_runner.py). HTTP client = unlimited concurrent readers/writers. Int8 scalar quantization, on-disk payload, payload indexes on `project`/`type`/`source`/`tags.*`
- **Embeddings**: EmbeddingGemma-300M via ONNX Runtime (768-dim, 2048 ctx). Any HF ONNX model supported — configure via `imprint config set model.name/dim/pooling`. Auto-picks GPU if `CUDAExecutionProvider` available, else CPU
- **Chunker**: Chonkie hybrid — `CodeChunker` (tree-sitter, language-aware) for code, `SemanticChunker` for prose. Sliding overlap on top
- **Tagger**: structured payload `{lang, layer, kind, domain[], topics[]}`. Always-on deterministic + keyword dict; zero-shot on by default (opt-out `IMPRINT_ZERO_SHOT_TAGS=0`), now also used as fallback when LLM topics are empty; LLM tagging opt-in (`IMPRINT_LLM_TAGS=1`, replaces zero-shot). Context-aware: LLM tagger receives neighboring chunk text + project name for better topic accuracy. LLM providers: anthropic, openai, ollama, vllm, gemini — set via `IMPRINT_LLM_TAGGER_PROVIDER`. Default local model: Qwen3 1.7B (Q4_K_M, 8K ctx)
- **Imprint graph**: SQLite with temporal facts (per workspace)
- **Workspaces**: isolated memory environments — each gets its own Qdrant collection, SQLite DB, and WAL. Config in `data/workspace.json`. Default workspace uses `memories` collection + `imprint_graph.sqlite3` (backward compat). Named workspaces use `memories_{name}` + `imprint_graph_{name}.sqlite3` + `wal_{name}.jsonl`
- **MCP framework**: FastMCP
- **Data**: `data/qdrant_storage/` + `data/qdrant-bin/` + `data/imprint_graph*.sqlite3` + `data/workspace.json` (gitignored)
- **Lifecycle**: `imprint enable` / `disable` / `status` toggle MCP + hooks + server in one shot

## Configuration

All settings persistable via `imprint config set <key> <value>`. Stored in `data/config.json`. Precedence: env var > config.json > default. Run `imprint config` to see all settings + current values.

Key settings (full list via `imprint config`):

| Key | Default | Notes |
|---|---|---|
| `model.name` | `onnx-community/embeddinggemma-300m-ONNX` | HF embedding model repo (any ONNX model) |
| `model.dim` | `768` | Embedding dimension (must match model) |
| `model.seq_length` | `2048` | Token cap per embed call |
| `model.device` | `auto` | `cpu` / `gpu` / `auto` |
| `model.threads` | `4` | CPU intra-op threads |
| `model.gpu_mem_mb` | `2048` | VRAM cap for CUDA arena |
| `model.pooling` | `auto` | Pooling: auto / cls / mean / last |
| `qdrant.host` | `127.0.0.1` | Qdrant bind/connect host |
| `qdrant.port` | `6333` | Qdrant HTTP port |
| `qdrant.no_spawn` | `false` | Skip auto-spawn (BYO server) |
| `chunker.overlap` | `400` | Sliding overlap chars |
| `chunker.size_code` | `4000` | Soft target for code chunks |
| `chunker.size_prose` | `6000` | Soft target for prose chunks |
| `chunker.hard_max` | `8000` | Absolute max chunk size |
| `tagger.zero_shot` | `true` | Zero-shot topic tagging (opt-out) |
| `tagger.llm` | `false` | LLM tagging (replaces zero-shot) |
| `tagger.llm_provider` | `anthropic` | `anthropic` / `openai` / `ollama` / `vllm` / `gemini` |
| `tagger.llm_model` | per-provider | Override model name |

Env vars (`IMPRINT_*`) still work and take priority. `IMPRINT_QDRANT_BIN` and `IMPRINT_DATA_DIR` remain env-only.

## Project Detection

Projects are identified by canonical name from manifest files (package.json, go.mod, etc.), not by path. This means the same project on different machines gets the same identity.

## CLI

```bash
imprint setup              # install deps, register MCP, configure Claude Code
imprint status             # enabled/disabled? server pid? memory stats?
imprint enable [target]    # re-wire MCP + hooks + start server
imprint disable            # stop server, unregister MCP, strip hooks
imprint ingest <dir>       # index project source files
imprint learn              # index Claude Code conversations + memory files
imprint refresh <dir>      # re-index only changed files
imprint config             # show all settings with current values
imprint config set <k> <v> # persist a setting
imprint config get <key>   # show one setting
imprint config reset <key> # remove override, revert to default
imprint server <cmd>       # Qdrant daemon: start | stop | status | log
imprint ui                 # foreground dashboard (Ctrl+C to stop)
imprint ui start [--port N]  # detached background daemon
imprint ui stop            # stop background UI server
imprint ui status          # pid + reachability
imprint ui open [--port N]   # start if stopped, then open a browser window
imprint ui restart         # stop + start
imprint ui log             # UI log file path
imprint workspace          # list workspaces and show active
imprint workspace switch <name>  # switch to workspace (creates if new)
imprint workspace delete <name>  # delete a workspace and its data
imprint wipe [--force]     # wipe active workspace
imprint wipe --all         # wipe everything (all workspaces)
imprint sync serve --relay <host>  # expose KB for syncing
imprint sync <host>/<id>   # bidirectional sync with peer
imprint version            # print version
```
