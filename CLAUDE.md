# Imprint Memory Layer

Custom lightweight MCP memory server for Claude Code. Qdrant (embedded) + BGE-M3 ONNX embeddings + Chonkie hybrid chunker + structured tag payloads.

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
- **Embeddings**: BGE-M3 via ONNX Runtime (1024-dim, 8192 ctx, MTEB ~67). Auto-picks GPU if `CUDAExecutionProvider` available, else CPU. Variant: `model_int8.onnx` (CPU) or `model_fp16.onnx` (GPU)
- **Chunker**: Chonkie hybrid — `CodeChunker` (tree-sitter, language-aware) for code, `SemanticChunker` for prose. Sliding overlap on top
- **Tagger**: structured payload `{lang, layer, kind, domain[], topics[]}`. Always-on deterministic + keyword dict; opt-in zero-shot (`IMPRINT_ZERO_SHOT_TAGS=1`) + LLM (`IMPRINT_LLM_TAGS=1`, needs `ANTHROPIC_API_KEY`)
- **Imprint graph**: SQLite with temporal facts
- **MCP framework**: FastMCP
- **Data**: `data/qdrant_storage/` + `data/qdrant-bin/` + `data/imprint_graph.sqlite3` (gitignored)
- **Lifecycle**: `imprint enable` / `disable` / `status` toggle MCP + hooks + server in one shot

## Tunables

| Env var | Default | Notes |
|---|---|---|
| `IMPRINT_DEVICE` | `auto` | `cpu` / `gpu` / `auto` |
| `IMPRINT_GPU_MEM_MB` | `2048` | VRAM cap for ORT CUDA arena (conservative for WSL2; raise on dedicated GPUs) |
| `IMPRINT_ONNX_THREADS` | `4` | CPU intra-op threads |
| `IMPRINT_MAX_SEQ_LENGTH` | `2048` | Token cap per embed call |
| `IMPRINT_MODEL_NAME` | `Xenova/bge-m3` | HF repo |
| `IMPRINT_MODEL_FILE` | auto | `onnx/model_int8.onnx` (CPU) / `onnx/model_fp16.onnx` (GPU) |
| `IMPRINT_CHUNK_OVERLAP` | `150` | Sliding overlap chars (prose only; code paths skip) |
| `IMPRINT_CHUNK_SIZE_CODE` | `800` | Target chunk size for code (favors method-per-chunk) |
| `IMPRINT_CHUNK_SIZE_PROSE` | `1500` | Target chunk size for prose (SemanticChunker decides boundary) |
| `IMPRINT_CHUNK_HARD_MAX` | `6000` | Absolute max chunk size |
| `IMPRINT_SEMANTIC_THRESHOLD` | `0.5` | SemanticChunker topic-shift threshold (lower = sharper splits) |
| `IMPRINT_QDRANT_HOST` | `127.0.0.1` | Qdrant bind/connect host |
| `IMPRINT_QDRANT_PORT` | `6333` | Qdrant HTTP port |
| `IMPRINT_QDRANT_VERSION` | `v1.17.1` | Pinned release for auto-download |
| `IMPRINT_QDRANT_BIN` | (auto) | Override Qdrant binary path |
| `IMPRINT_QDRANT_NO_SPAWN` | `0` | Set `1` to skip auto-spawn (BYO server) |

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
imprint server <cmd>       # daemon control: start | stop | status | log
imprint sync serve --relay <host>  # expose KB for syncing
imprint sync <host>/<id>   # bidirectional sync with peer
imprint viz                # graph visualization of memory clusters
imprint version            # print version
```
