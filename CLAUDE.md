# Knowledge Base

Custom lightweight MCP memory server for Claude Code. Qdrant (embedded) + BGE-M3 ONNX embeddings + Chonkie hybrid chunker + structured tag payloads.

## IMPORTANT: Use Knowledge Before Reading Files

**At the start of every conversation**, call `mcp__knowledge__wake_up` to load prior context.

**Before reading files for context**, call `mcp__knowledge__search` first. If the knowledge base has the answer, use it — don't re-read source files. Only fall back to Read/Grep when:
- The knowledge base returns no results
- You need the *current* content of a specific file
- The user explicitly asks you to read a file

**During conversation**, store important findings:
- `mcp__knowledge__store` — decisions, patterns, findings, preferences, bugs
- `mcp__knowledge__kg_add` — structured facts (subject → predicate → object)

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

- **Vector store**: Qdrant server, auto-spawned local daemon on `127.0.0.1:6333` via [`qdrant_runner.py`](knowledgebase/qdrant_runner.py). HTTP client = unlimited concurrent readers/writers. Int8 scalar quantization, on-disk payload, payload indexes on `project`/`type`/`source`/`tags.*`
- **Embeddings**: BGE-M3 via ONNX Runtime (1024-dim, 8192 ctx, MTEB ~67). Auto-picks GPU if `CUDAExecutionProvider` available, else CPU. Variant: `model_int8.onnx` (CPU) or `model_fp16.onnx` (GPU)
- **Chunker**: Chonkie hybrid — `CodeChunker` (tree-sitter, language-aware) for code, `SemanticChunker` for prose. Sliding overlap on top
- **Tagger**: structured payload `{lang, layer, kind, domain[], topics[]}`. Always-on deterministic + keyword dict; opt-in zero-shot (`KNOWLEDGE_ZERO_SHOT_TAGS=1`) + LLM (`KNOWLEDGE_LLM_TAGS=1`, needs `ANTHROPIC_API_KEY`)
- **Knowledge graph**: SQLite with temporal facts
- **MCP framework**: FastMCP
- **Data**: `data/qdrant_storage/` + `data/qdrant-bin/` + `data/knowledge_graph.sqlite3` (gitignored)
- **Lifecycle**: `knowledge enable` / `disable` / `status` toggle MCP + hooks + server in one shot

## Tunables

| Env var | Default | Notes |
|---|---|---|
| `KNOWLEDGE_DEVICE` | `auto` | `cpu` / `gpu` / `auto` |
| `KNOWLEDGE_GPU_MEM_MB` | `6144` | VRAM cap for ORT CUDA arena |
| `KNOWLEDGE_ONNX_THREADS` | `4` | CPU intra-op threads |
| `KNOWLEDGE_MAX_SEQ_LENGTH` | `2048` | Token cap per embed call |
| `KNOWLEDGE_MODEL_NAME` | `Xenova/bge-m3` | HF repo |
| `KNOWLEDGE_MODEL_FILE` | auto | `onnx/model_int8.onnx` (CPU) / `onnx/model_fp16.onnx` (GPU) |
| `KNOWLEDGE_CHUNK_OVERLAP` | `150` | Sliding overlap chars (prose only; code paths skip) |
| `KNOWLEDGE_CHUNK_SIZE_CODE` | `800` | Target chunk size for code (favors method-per-chunk) |
| `KNOWLEDGE_CHUNK_SIZE_PROSE` | `1500` | Target chunk size for prose (SemanticChunker decides boundary) |
| `KNOWLEDGE_CHUNK_HARD_MAX` | `6000` | Absolute max chunk size |
| `KNOWLEDGE_SEMANTIC_THRESHOLD` | `0.5` | SemanticChunker topic-shift threshold (lower = sharper splits) |
| `KNOWLEDGE_QDRANT_HOST` | `127.0.0.1` | Qdrant bind/connect host |
| `KNOWLEDGE_QDRANT_PORT` | `6333` | Qdrant HTTP port |
| `KNOWLEDGE_QDRANT_VERSION` | `v1.17.1` | Pinned release for auto-download |
| `KNOWLEDGE_QDRANT_BIN` | (auto) | Override Qdrant binary path |
| `KNOWLEDGE_QDRANT_NO_SPAWN` | `0` | Set `1` to skip auto-spawn (BYO server) |

## Project Detection

Projects are identified by canonical name from manifest files (package.json, go.mod, etc.), not by path. This means the same project on different machines gets the same identity.

## CLI

```bash
knowledge setup              # install deps, register MCP, configure Claude Code
knowledge status             # enabled/disabled? server pid? memory stats?
knowledge enable [target]    # re-wire MCP + hooks + start server
knowledge disable            # stop server, unregister MCP, strip hooks
knowledge ingest <dir>       # detect projects + ingest into knowledge base
knowledge refresh <dir>      # re-index only changed files
knowledge server <cmd>       # daemon control: start | stop | status | log
knowledge sync serve --relay <host>  # expose KB for syncing
knowledge sync <host>/<id>   # bidirectional sync with peer
knowledge viz                # 3D brain cluster visualization
knowledge version            # print version
```
