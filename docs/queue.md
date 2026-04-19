# Command Queue

Ingest, refresh, retag, and ingest-url all load the embedding model, scan Qdrant, and — when LLM tagging is on — hold a per-batch HTTP connection to the tagger provider. Running two in parallel on a workstation is the fastest way to OOM the box. Imprint serializes them with a shared single-slot queue.

## Semantics

- **One job at a time, system-wide.** A single advisory flock on `data/queue.lock` gates every heavy command, regardless of which side started it.
- **UI queues; CLI refuses.** `POST /api/commands/{cmd}` from the dashboard writes a row to `data/queue.sqlite3` and returns a `job_id` immediately; the FastAPI dispatcher picks it up in FIFO order. Direct CLI invocations (`imprint ingest`, `imprint refresh`, `imprint retag`, `imprint ingest-url`, `imprint refresh-urls`) try the lock non-blocking — if held, they exit nonzero and print the current holder.
- **Cancel = kill the process group.** Subprocesses are spawned with `start_new_session=True`, so `killpg(pgid, SIGTERM)` takes down the Python process, its httpx worker threads (in-flight LLM tagger calls die with it), any `llama-cpp` inference thread, and any descendant helpers. If the group is still alive 3 s later, the dispatcher escalates to `SIGKILL`.
- **Persistent history.** The SQLite queue survives API restarts. `recover_on_startup()` marks rows stuck in `running` whose PID is dead as `failed` (`error='api_restart'`) and clears stale lock files.

## Storage

| Path | Purpose |
|---|---|
| `data/queue.sqlite3` | FIFO queue + job history (see schema below) |
| `data/queue.lock` | `fcntl` advisory flock + JSON body describing the current holder |
| `data/queue_logs/{job_id}.log` | Full subprocess stdout+stderr per job (tailed by `/stream`) |
| `data/ingest_progress.json` | Existing live-progress JSON — unchanged; joined into the active job row by `/api/queue` |

### Schema

```sql
CREATE TABLE jobs (
    id          TEXT PRIMARY KEY,   -- uuid4 hex
    command     TEXT NOT NULL,      -- ingest | refresh | retag | ingest-url | ...
    body_json   TEXT NOT NULL,      -- request body the UI/CLI submitted
    status      TEXT NOT NULL,      -- queued | running | done | failed | cancelled
    pid         INTEGER,
    pgid        INTEGER,
    exit_code   INTEGER,
    error       TEXT,
    created_at  REAL NOT NULL,
    started_at  REAL,
    ended_at    REAL
);
CREATE INDEX idx_jobs_status  ON jobs(status, created_at);
CREATE INDEX idx_jobs_created ON jobs(created_at DESC);
```

### Lock file body

```json
{"pid": 12345, "job_id": "<uuid>", "command": "ingest", "started_at": 1.713e9}
```

Both sides parse the same shape: Python via [`imprint/queue_lock.py`](../imprint/queue_lock.py), Go via [`internal/queuelock/lock.go`](../internal/queuelock/lock.go).

## HTTP API

All endpoints live on the Imprint dashboard server (default `http://127.0.0.1:8420`).

| Method | Path | Body | Response |
|---|---|---|---|
| `POST` | `/api/commands/{command}` | `{...flags}` | `{job_id, position}` — position is `0` if it started immediately, otherwise its 1-based slot |
| `GET` | `/api/queue?recent_limit=20` | — | `{active, queued[], recent[]}`; `active` is enriched with live progress fields (phase, processed, total, percent, eta_seconds) |
| `GET` | `/api/jobs` | — | Back-compat — `{jobs: [active]}` (empty array when nothing is running) |
| `GET` | `/api/jobs/{id}` | — | Full row, with progress when running |
| `GET` | `/api/jobs/{id}/stream` | — | SSE — replays the stored log then tails new lines; emits a final `{type:"done", exit_code, status}` |
| `POST` | `/api/jobs/{id}/cancel` | — | `{ok, was_running}`. Queued → marked `cancelled`; running → SIGTERM → SIGKILL (3 s) |

### Allowed commands

`ingest`, `ingest-url`, `refresh`, `refresh-urls`, `retag`, `wipe`, `migrate`, `sync`, `config`, `workspace`, `status` — same set the CLI accepts via `imprint <command>`.

## UI

- **`/queue`** — active job card (with live progress bar + cancel), queued list (cancel removes the row), recent history (exit code, duration, expandable log viewer).
- **Dashboard sidebar** — [`IngestionProgress`](../imprint/ui/src/components/ingestion-progress.tsx) now shows the active card plus a compact queued list with per-row cancel X.
- **`QuickIngest` / `CommandsPage`** — continue to use `streamCommand()` from [`lib/api.ts`](../imprint/ui/src/lib/api.ts). The helper now enqueues first, then SSE-tails `/api/jobs/{id}/stream`. `AbortController.abort()` cancels the job server-side (not just the fetch).

## Verification

1. **Queue gate (UI):** start `ingest` via UI → start `refresh` while the first runs. The second enters the queued list and begins after the first finishes.
2. **Queue gate (CLI):** while a UI job is running, run `imprint ingest <path>` from a terminal. Expect exit code `1` and an error printing the holder's PID + start time + "Cancel it from the UI (/queue) or: kill <pid>".
3. **Cancel running (cascade kill):** start ingest with `IMPRINT_LLM_TAGS=1` + anthropic provider; hit cancel in UI mid-LLM-call. Within ~3 s `ps -ef` should show no python/httpx processes from that job. The row ends as `cancelled`, the lock file is released, the next queued job starts.
4. **Cancel queued:** enqueue A, B, C. Cancel B while A runs. B's status flips to `cancelled` immediately without ever starting; C runs after A.
5. **Restart recovery:** start a job; `pkill -f imprint.api`; restart. `/api/queue` shows the old running row as `failed` with `error='api_restart'`, the lock file is gone, new jobs enqueue normally.
6. **Process-group cascade:** during `ingest-url` with the local `llama-cpp` tagger, cancel. `ps --forest` should show the subtree (llama.cpp threads, httpx workers, extractor helpers) gone in one shot, not staggered.
