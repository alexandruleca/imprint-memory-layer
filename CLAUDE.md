# Knowledge Base

Custom lightweight MCP memory server for Claude Code. Replaces MemPalace with LanceDB + ONNX embeddings.

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
| `search` | Semantic search with optional project/type filters |
| `store` | Store a memory with metadata |
| `delete` | Remove a memory by ID |
| `kg_query` | Query temporal facts |
| `kg_add` | Add a structured fact |
| `kg_invalidate` | Mark a fact as ended |
| `status` | Show overview stats |

## Architecture

- **Vector store**: LanceDB (Rust, zero native deps)
- **Embeddings**: nomic-embed-text-v1.5 via ONNX Runtime (768-dim, 8192 token context)
- **Knowledge graph**: SQLite with temporal facts
- **MCP framework**: FastMCP
- **Data**: `data/` directory (gitignored)

## Project Detection

Projects are identified by canonical name from manifest files (package.json, go.mod, etc.), not by path. This means the same project on different machines gets the same identity.

## CLI

```bash
knowledge setup              # install deps, register MCP, configure Claude Code
knowledge ingest <dir>       # detect projects + ingest into knowledge base
knowledge refresh <dir>      # re-index only changed files
knowledge sync serve --relay <host>  # expose KB for syncing
knowledge sync <host>/<id>   # bidirectional sync with peer
knowledge viz                # 3D brain cluster visualization
knowledge version            # print version
```
