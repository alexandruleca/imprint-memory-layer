# Architecture

## Components

```mermaid
graph LR
    subgraph "Go CLI (imprint binary)"
        SETUP[setup]
        STATUS[status]
        ENABLE[enable / disable]
        INGEST[ingest]
        REFRESH[refresh]
        SERVER[server start/stop]
        SYNC[sync]
        RELAY[relay]
        UI[ui]
    end

    subgraph "Python (imprint/)"
        MCP[FastMCP Server<br/><small>12 tools</small>]
        VS[vectorstore.py<br/><small>Qdrant HTTP client</small>]
        QR[qdrant_runner.py<br/><small>auto-spawn daemon</small>]
        EMB[embeddings.py<br/><small>ONNX Runtime, GPU/CPU</small>]
        KG[imprint_graph.py<br/><small>SQLite facts</small>]
        CHK[chunker.py<br/><small>Chonkie hybrid</small>]
        TAG[tagger.py<br/><small>4-source metadata</small>]
        CLS[classifier.py<br/><small>type detection</small>]
        PRJ[projects.py<br/><small>manifest detection</small>]
    end

    subgraph "Daemon"
        QSRV[(Qdrant server<br/>127.0.0.1:6333)]
    end

    subgraph "Storage (data/)"
        QSTORE[qdrant_storage/<br/><small>vectors + payload</small>]
        SQLITE[(SQLite<br/>facts per workspace)]
        WAL[wal.jsonl<br/><small>WAL per workspace</small>]
        WSCFG[workspace.json<br/><small>active + known</small>]
        PROTO[label_prototypes.npy<br/><small>zero-shot cache</small>]
        QBIN[qdrant-bin/<br/><small>downloaded binary</small>]
    end

    MCP --> VS --> QSRV --> QSTORE
    MCP --> KG --> SQLITE
    VS --> QR
    QR --> QBIN
    QR --> QSRV
    VS --> EMB
    VS --> WAL
    INGEST --> CHK --> EMB
    INGEST --> TAG
    TAG --zero-shot--> EMB
    TAG --zero-shot--> PROTO
    TAG -.LLM opt-in.-> LLM_API[LLM API<br/><small>anthropic/openai/<br/>ollama/vllm/gemini</small>]
    INGEST --> PRJ
    SERVER --> QR

    style QSRV fill:#1a1a3a,stroke:#60a5fa,color:#fff
    style QSTORE fill:#1a1a3a,stroke:#60a5fa,color:#fff
    style SQLITE fill:#1a1a3a,stroke:#4ecdc4,color:#fff
    style MCP fill:#0d1117,stroke:#a78bfa,color:#fff
    style EMB fill:#0d1117,stroke:#fbbf24,color:#fff
    style CHK fill:#0d1117,stroke:#f472b6,color:#fff
    style TAG fill:#0d1117,stroke:#34d399,color:#fff
    style QR fill:#0d1117,stroke:#ff6b6b,color:#fff
    style LLM_API fill:#0d1117,stroke:#fbbf24,color:#fff,stroke-dasharray: 5 5
```

| Component | Technology | Purpose |
|---|---|---|
| Vector store | Qdrant server (auto-spawned daemon) | HNSW + int8 scalar quantization, payload-indexed filters, multi-client safe |
| Server runner | `qdrant_runner.py` | Downloads + spawns + supervises the local Qdrant daemon |
| Embeddings | EmbeddingGemma-300M via ONNX Runtime | 768-dim, 2048 ctx. Configurable model via `imprint config` |
| Chunking | Chonkie 1.6+ | `CodeChunker` (tree-sitter) + `SemanticChunker` (topic shifts) + sliding overlap |
| Metadata tagger | Python | Deterministic + keyword dict + zero-shot (default) + opt-in multi-provider LLM |
| Imprint graph | SQLite | Temporal facts with valid_from/ended |
| MCP server | FastMCP (Python) | 12 tools — `search`/`neighbors`/`graph_scope`/`list_sources`/`file_summary`/`file_chunks` for reads, `store`/`delete`/`ingest_url` for writes, `kg_query`/`kg_edit` for facts, `status` for stats. Connects to Qdrant via HTTP. |
| CLI | Go | `setup`, `status`, `enable`, `disable`, `update`, `uninstall`, `ingest`, `learn`, `ingest-url`, `refresh`, `refresh-urls`, `retag`, `migrate`, `server`, `workspace`, `wipe`, `sync`, `relay`, `ui`, `config` |
| Relay | Go (nhooyr/websocket) | Stateless WebSocket forwarder for P2P sync |

## Data Flow

```mermaid
sequenceDiagram
    participant U as You
    participant C as Claude Code
    participant K as Imprint MCP
    participant E as Embedding ONNX
    participant Q as Qdrant

    Note over C,K: Session Start
    C->>K: wake_up()
    K->>Q: scroll recent points + KG facts
    K-->>C: project list + essential context (~800 tokens)

    Note over U,C: Working on a task
    U->>C: "Why is CORS set to wildcard?"
    C->>K: search("CORS wildcard", lang="python", domain="auth")
    K->>E: embed query → dense vector
    K->>Q: HNSW search + payload filter
    Q-->>K: top-K matching chunks + structured tags
    K-->>C: ranked results with metadata
    C-->>U: answers from memory (no file reads needed)

    Note over C,K: Claude learns something
    C->>K: store("CORS wildcard is by design because...")
    K->>E: embed content → vector
    K->>Q: upsert point (vector + payload)

    Note over C,K: Session End
    C->>K: Stop hook fires (async)
    K->>Q: auto-extract decisions, batch upsert
```

## Concurrency: Auto-Spawned Local Server

Embedded Qdrant (the `path=...` mode) is single-writer — only one process can hold the on-disk lock. That breaks the moment your MCP server, your hooks, and an `imprint ingest` all try to write at once. Imprint sidesteps the limitation by **auto-spawning a local Qdrant server** on `127.0.0.1:6333`.

```mermaid
sequenceDiagram
    participant MCP as MCP server (Claude Code)
    participant H as Stop hook
    participant CLI as imprint ingest
    participant R as qdrant_runner
    participant Q as qdrant daemon

    MCP->>R: ensure_running()
    alt server not reachable
        R->>R: download binary (first time)<br/>spawn detached daemon
        R->>Q: start
        R->>Q: poll /readyz until 200
    end
    R-->>MCP: 127.0.0.1:6333

    CLI->>R: ensure_running()  (server already up)
    R-->>CLI: 127.0.0.1:6333
    H->>R: ensure_running()
    R-->>H: 127.0.0.1:6333

    par
        MCP->>Q: search (HTTP)
    and
        CLI->>Q: upsert points (HTTP)
    and
        H->>Q: upsert decisions (HTTP)
    end
    Q-->>MCP: results
    Q-->>CLI: ack
    Q-->>H: ack
```

[`qdrant_runner.py`](../imprint/qdrant_runner.py) handles the lifecycle:

- **First call**: downloads the pinned Qdrant binary (~50 MB) from GitHub releases into `data/qdrant-bin/`, then `subprocess.Popen([..., start_new_session=True])` so the daemon survives the parent process. Logs to `data/qdrant.log`, PID written to `data/qdrant.pid`.
- **Subsequent calls**: cheap HTTP probe to `/readyz` — returns immediately if alive.
- **Storage**: `data/qdrant_storage/` (collection data) + `data/qdrant_snapshots/`. Both gitignored.
- **Shutdown**: `imprint server stop` (or `imprint disable`) sends SIGTERM via the PID file.

| Env var | Default | Purpose |
|---|---|---|
| `IMPRINT_QDRANT_HOST` | `127.0.0.1` | Bind / connect host |
| `IMPRINT_QDRANT_PORT` | `6333` | HTTP port |
| `IMPRINT_QDRANT_GRPC_PORT` | `6334` | gRPC port |
| `IMPRINT_QDRANT_VERSION` | `v1.17.1` | Pinned release tag |
| `IMPRINT_QDRANT_BIN` | (auto) | Override binary path (e.g. system-installed qdrant) |
| `IMPRINT_QDRANT_NO_SPAWN` | `0` | Set `1` to disable auto-spawn — connect to your own managed server |

**Why server mode and not embedded?** Embedded mode pins a filesystem lock and rejects any second client — this conflicts with Claude Code (always-on MCP) running alongside `imprint ingest`, hooks writing decisions, and tools like `imprint ui` reading the collection. Server mode supports unlimited concurrent connections at the cost of a single ~50 MB binary in your data dir and a ~50 MB resident process. Worth it.

**Bring your own server**: set `IMPRINT_QDRANT_NO_SPAWN=1` and point `IMPRINT_QDRANT_HOST` at a Docker (`docker run -p 6333:6333 qdrant/qdrant`) or remote Qdrant. Auto-spawn is disabled and the runner connects directly.

## Lifecycle Commands

```bash
imprint status            # is the system enabled? server pid? memory count?
imprint enable            # idempotent re-wire of MCP + hooks + server
imprint disable           # stops daemon, removes MCP registration, strips hooks
imprint server start      # explicit server boot (auto on first MCP/CLI call)
imprint server stop       # SIGTERM the daemon
imprint server status     # JSON: pid, host, port, log path
imprint server log        # path to qdrant.log for tailing
```

**`disable` / `enable` are kill switches.** Disable stops the Qdrant daemon, removes the MCP server registration from Claude Code, and strips the imprint hooks from `~/.claude/settings.json`. Your venv and data directory are kept intact, so re-enabling is instant — no re-ingest needed. `imprint status` shows the current state.

Example status output:

```
═══ Imprint Status ═══

[+] ENABLED

  ✓ MCP server registered (Claude Code)
  ✓ Hooks installed (5 entries)
  ✓ Qdrant server  http://127.0.0.1:6333  (pid 19803)
  ✓ Python venv    /home/you/code/imprint/.venv/bin/python
  ✓ Data dir       /home/you/code/imprint/data

  Memories: 14293  across 32 projects
    my-web-app (1551)
    backend-api (1053)
    ...
```
