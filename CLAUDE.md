# Imprint Memory Layer

Custom lightweight MCP memory server for Claude Code. Qdrant (embedded) + EmbeddingGemma-300M ONNX embeddings + Chonkie hybrid chunker + structured tag payloads.

## IMPORTANT: Use Imprint Before Reading Files

**At the start of every conversation**, call `mcp__imprint__wake_up` to load prior context.

**Before reading files for context**, call `mcp__imprint__search` first. If the imprint memory has the answer, use it — don't re-read source files. Only fall back to Read/Grep when:
- The imprint memory returns no results
- You need the *current* content of a specific file
- The user explicitly asks you to read a file

**During conversation**, store important findings:
- `mcp__imprint__store` — decisions, patterns, findings, preferences, bugs
- `mcp__imprint__kg_add` — structured facts (subject → predicate → object)

## MCP Tools (8 total)

| Tool | Purpose |
|------|---------|
| `wake_up` | Load summary context at session start |
| `search` | Semantic search with optional project/type/lang/layer/kind/domain filters |
| `store` | Store a memory with metadata |
| `delete` | Remove a memory by ID |
| `kg_query` | Query temporal facts |
| `kg_add` | Add a structured fact |
| `kg_invalidate` | Mark a fact as ended |
| `status` | Show overview stats |

## Architecture

- **Vector store**: Qdrant server, auto-spawned local daemon on `127.0.0.1:6333` via [`qdrant_runner.py`](imprint/qdrant_runner.py). HTTP client = unlimited concurrent readers/writers. Int8 scalar quantization, on-disk payload, payload indexes on `project`/`type`/`source`/`tags.*`
- **Embeddings**: EmbeddingGemma-300M via ONNX Runtime (768-dim, 2048 ctx). Any HF ONNX model supported — configure via `imprint config set model.name/dim/pooling`. Auto-picks GPU if `CUDAExecutionProvider` available, else CPU
- **Chunker**: Chonkie hybrid — `CodeChunker` (tree-sitter, language-aware) for code, `SemanticChunker` for prose. Sliding overlap on top
- **Tagger**: structured payload `{lang, layer, kind, domain[], topics[]}`. Always-on deterministic + keyword dict; zero-shot on by default (opt-out `IMPRINT_ZERO_SHOT_TAGS=0`); LLM tagging opt-in (`IMPRINT_LLM_TAGS=1`, replaces zero-shot). LLM providers: anthropic, openai, ollama, vllm, gemini — set via `IMPRINT_LLM_TAGGER_PROVIDER`
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
imprint ingest <dir>       # detect projects + ingest into imprint memory
imprint refresh <dir>      # re-index only changed files
imprint config             # show all settings with current values
imprint config set <k> <v> # persist a setting
imprint config get <key>   # show one setting
imprint config reset <key> # remove override, revert to default
imprint server <cmd>       # daemon control: start | stop | status | log
imprint workspace          # list workspaces and show active
imprint workspace switch <name>  # switch to workspace (creates if new)
imprint workspace delete <name>  # delete a workspace and its data
imprint wipe [--force]     # wipe active workspace
imprint wipe --all         # wipe everything (all workspaces)
imprint sync serve --relay <host>  # expose KB for syncing
imprint sync <host>/<id>   # bidirectional sync with peer
imprint viz                # graph visualization of memory clusters
imprint version            # print version
```
