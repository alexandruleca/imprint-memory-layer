---
title: Command Queue
description: How Imprint serializes long-running CLI jobs (ingest, retag, wipe, sync) through a single-slot FIFO queue with advisory file locking, merged duplicate jobs, background LLM tagging that steps aside, SIGTERM-safe cancellation, and SSE streaming.
---
Ingest, refresh, retag, and ingest-url all load the embedding model, scan Qdrant, and — for retag and background tagging — run the LLM tagger. Running two in parallel on a workstation is the fastest way to OOM the box. Imprint serializes them with a shared single-slot queue. When nothing waits, the slot runs background LLM tagging, which steps aside for every other job.

## Semantics

- **One job at a time, system-wide.** A single advisory flock on `data/queue.lock` gates every heavy command, regardless of which side started it.
- **UI queues; the CLI hands over or waits.** `POST /api/commands/{cmd}` from the dashboard writes a row to `data/queue.sqlite3` and returns a `job_id` immediately; the FastAPI dispatcher picks it up in FIFO order. Direct CLI invocations (`imprint ingest`, `imprint refresh`, `imprint learn`, `imprint retag`, `imprint scrub`, `imprint ingest-url`, `imprint refresh-urls`) try the lock non-blocking. If it is held they print the current holder, then enqueue themselves through the dashboard's API when the dashboard runs, or wait for the lock otherwise. `IMPRINT_QUEUE_NO_WAIT=1` makes them exit nonzero instead. Why the queue exists: [ADR 0064](adr/0064-single-slot-command-queue-and-shared-lock.md).
- **The Stop hook's learner waits its turn.** After each turn the Stop hook starts a detached learner (`python -m imprint.session_learn <transcript>`) and returns. The learner takes the conversation's waiting slot in `data/learn_slots/`, or exits when another learner holds it, since that one has not read the conversation yet. It lets a burst of turns settle for 60 s, then waits for the lock without handing over to the dashboard (stopping background tagging, as any taker does), gives up its slot, and holds the lock as `learn` while it learns the exchanges, extracts decisions and, when enabled, summarizes. So a conversation has at most one learner waiting. Why: [ADR 0077](adr/0077-stop-hook-hands-turns-to-one-waiting-learner.md).
- **Unapproved roots are refused before queueing.** For `ingest` and `refresh`, `POST /api/commands/{cmd}` checks the body's `dir` against the workspace's roots with `workspace_policy.unapproved_root`, which mirrors `ensure_ingest_paths` including the first-root binding. Instead of queueing a job that would fail when it runs, it returns 409 `{error, code: "root_not_approved", workspace, root, overlaps}`; the dashboard asks to approve that root and resubmits (ADR 0015, ADR 0039).
- **Identical waiting jobs merge.** Submitting a job identical to one still waiting (same command, workspace and arguments) returns that job with `merged: true` instead of queueing another; the waiting job counts the requests it answers (`_merged`). One identical to the running job queues a single follow-up, since the running job may have read its inputs before the new request. Arguments are compared after normalization: dashboard fields and CLI arguments name the same job, option order does not matter, paths are compared without trailing separators or `.`/`..` segments, and relative paths are compared as given. Browser captures never merge. The CLI makes its paths absolute before handing a job to the dashboard. Why: [ADR 0068](adr/0068-merge-identical-waiting-jobs.md).
- **Background tagging runs last and steps aside.** With `tagger.llm` on, learn, ingest, refresh, ingest-url, the Stop hook and inline content store memories without running the LLM and mark them `tag_pending`. When no job runs or waits and no other process holds the lock, the dispatcher starts the background tagging job (`tag-backlog`, `python -m imprint.tag_backlog <job_id>`). It tags every workspace's marked memories, newest first, one memory per write, and exits between memories when a job is queued (exit code 75, recorded as `yielded`). The dispatcher looks again when a job ends, when a terminal job releases the lock, when a `tagger.*` setting changes, on `POST /api/queue/background/resume` and every 30 s. After a cancel it waits until more memories are marked; after a failure (for example no usable tagger), 30 minutes. It keeps the latest 20 runs. Why: [ADR 0067](adr/0067-background-llm-tagging-that-yields-to-every-job.md); tagging details in [tagging](tagging.md#llm-tagging-runs-in-the-background).
- **Any other lock taker stops background tagging.** The background job holds the lock as a *preemptible* holder. A Go or Python process that wants the lock and finds a live preemptible holder stops it (SIGTERM, SIGKILL 3 s later; `TerminateProcess` with exit code 15 on Windows) and takes the lock within a few seconds, instead of refusing (update, dashboard and MCP purge, move), waiting (CLI purge, move, sync import) or failing (`IMPRINT_QUEUE_NO_WAIT=1`). The stopped run loses at most the memory it was classifying and is recorded as `yielded`.
- **Cancel = kill the process group.** Subprocesses are spawned with `start_new_session=True`, so `killpg(pgid, SIGTERM)` takes down the Python process, its httpx worker threads (in-flight LLM tagger calls die with it), any `llama-cpp` inference thread, and any descendant helpers. If the group is still alive 3 s later, the dispatcher escalates to `SIGKILL`.
- **Persistent history.** The SQLite queue survives API restarts. `recover_on_startup()` marks rows stuck in `running` whose PID is dead as `failed` (`error='api_restart'`) and clears stale lock files. A background tagging run left by the previous server is stopped and recorded as `yielded`.

## Storage

| Path | Purpose |
|---|---|
| `data/queue.sqlite3` | FIFO queue + job history (see schema below) |
| `data/queue.lock` | `fcntl` advisory flock + JSON body describing the current holder |
| `data/queue_logs/{job_id}.log` | Full subprocess stdout+stderr per job (tailed by `/stream`) |
| `data/ingest_progress.json` | Live ingest/refresh/learn progress JSON, including learn reading and embedding-batch phases; joined into the active job row by `/api/queue` |
| `data/learn_slots/{hash}.lock` | Stop hook learners' waiting slots, one per conversation and workspace; removed when the waiting learner takes the queue lock |
| `data/session_learn.log` | Stop hook learners' output; starts over past 1 MiB |

### Schema

```sql
CREATE TABLE jobs (
    id          TEXT PRIMARY KEY,   -- uuid4 hex
    command     TEXT NOT NULL,      -- ingest | refresh | retag | ingest-url | ...
    body_json   TEXT NOT NULL,      -- request body the UI/CLI submitted
    status      TEXT NOT NULL,      -- queued | running | done | failed | cancelled | yielded (tag-backlog only)
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

The background tagging job adds `"preemptible": true`. A holder clears the body before it unlocks, so a process that keeps running after a release (the dashboard after a purge) is not named as the holder.

Both sides parse the same shape: Python via [`imprint/queue_lock.py`](../imprint/queue_lock.py), Go via [`internal/queuelock/lock.go`](../internal/queuelock/lock.go). On Windows, where locks are mandatory for reads, Python locks one byte at offset 2^30, past the body, so other processes can read who holds the lock; Go locks the whole file, so the two still exclude each other.

## HTTP API

All endpoints live on the Imprint dashboard server (default `http://127.0.0.1:8420`).

| Method | Path | Body | Response |
|---|---|---|---|
| `POST` | `/api/commands/{command}` | `{...flags}` | `{job_id, position, merged}` — position is `0` if it started immediately, otherwise its 1-based slot; `merged` is true when an identical job was already waiting and `job_id` is that job |
| `GET` | `/api/queue?recent_limit=20` | — | `{active, queued[], recent[], background}`; `active` is enriched with live progress fields (phase, processed, total, percent, eta_seconds). Background tagging runs appear only in `background`: `{enabled, pending, state, job, resume_at}`, where `state` is `off`, `running`, `yielded`, `waiting`, `paused` (after a cancel), `failed` (retried at `resume_at`) or `idle`, `pending` counts marked memories across workspaces and `job` is the running or latest run |
| `POST` | `/api/queue/background/resume` | — | The `background` object; starts background tagging again after a cancel or failure as soon as the queue is idle |
| `GET` | `/api/jobs` | — | Back-compat — `{jobs: [active]}` (empty array when nothing is running) |
| `GET` | `/api/jobs/{id}` | — | Full row, with progress when running |
| `GET` | `/api/jobs/{id}/stream` | — | SSE — replays the stored log then tails new lines; emits a final `{type:"done", exit_code, status}` |
| `POST` | `/api/jobs/{id}/cancel` | — | `{ok, was_running}`. Queued → marked `cancelled`; running → SIGTERM → SIGKILL (3 s) |
| `GET` | `/api/commands` | — | `{commands, clients}`: the Commands page catalog and the CLI's clients (see below); 502 when `imprint clients` fails |

### Allowed commands

[`imprint/commands.py`](../imprint/commands.py) holds the catalog the Commands page renders: each command's label, description, group, fields (`path`, `text`, `switch`, `project`, `client`) and, for a command that removes something, the confirmation the page asks first. `POST /api/commands/{command}` accepts the catalog's commands plus `wipe`, `migrate` and `workspace`, which the workspace dialogs queue, and `wsl` (`setup` or `remove` of one distribution's client profile), which Settings → WSL profiles queues. [`queue.build_command_args`](../imprint/queue.py) maps each field to the CLI's arguments; a test checks that every catalog field reaches them. A `client` field offers the clients `imprint clients --json` reports, from the CLI's client table, with whether each is connected.

| Group | Commands |
|---|---|
| Index | `learn` (`desktop`), `ingest`, `refresh`, `ingest-url`, `refresh-urls` |
| Maintain | `retag` (`project`, `all`, `dry_run`, `pending`: only the memories waiting for background tagging), `scrub` (`apply`; a dry run without it, [secret redaction](redaction.md)), `sync` (export) |
| Clients | `connect` (Connect clients), `disconnect` (Disconnect clients, confirmed first), both with `target`: a client or `all` |
| Inspect | `status`, `doctor` (`probe_mcp`), `config` |

**Safe to run from the dashboard.** A job runs under the dashboard server, from the runtime it serves, so the catalog leaves out commands that would change either one ([ADR 0053](adr/0053-connect-clients-without-touching-the-runtime.md)):

- `connect` registers the installed runtime with clients and installs and starts nothing.
- `disconnect` only unregisters clients, and the dashboard and Qdrant keep running.
- `setup`, `enable`, `bootstrap` and `profile` install dependencies into the venv the server has loaded. Not offered. When a change in Settings needs another embedding runtime (`model.device`, `model.gpu_device`), the server queues `imprint bootstrap --non-interactive` itself; once that job has replaced the ONNX Runtime the server loaded, the server restarts through its launcher ([ADR 0050](adr/0050-registered-dashboard-server-and-supervised-restart.md), [ADR 0062](adr/0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md)). Replacing loaded libraries works because uv hard-links the venv's files to its cache; Windows refuses only to delete a loaded library's last link, so those cache files are freed by a later setup.
- `disable`, `ui`, `server` and `uninstall` stop or remove the dashboard itself. Not offered.
- `wsl setup` and `remove` run from Settings → WSL profiles, where a distribution is chosen. They register the installed Windows runtime in a WSL profile and install nothing.
- Workspaces, purge, move, sync pairing and updates have their own dashboard flows.

**Non-interactive.** Jobs have no terminal: the dispatcher gives each one the null device as standard input, so a prompt reads end-of-file instead of waiting. Commands that would ask (wipe) are sent with their confirmation flag by the dialog that asked the user.

## UI

- **`/queue`** — active job card (with live progress bar + cancel), queued list (cancel removes the row; a merged job shows how many requests it answers), background tagging (state, memories waiting, progress, the current or latest run with its log, cancel, and **Resume now** after a cancel or failure), recent history (exit code, duration, expandable log viewer). Background runs raise no job toasts.
- **Dashboard sidebar** — [`IngestionProgress`](../imprint/ui/src/components/ingestion-progress.tsx) now shows the active card plus a compact queued list with per-row cancel X.
- **`QuickIngest` / `CommandsPage`** — the Commands page loads its commands from `GET /api/commands` and reloads them when a job finishes, so client connection states stay current. Both continue to use `streamCommand()` from [`lib/api.ts`](../imprint/ui/src/lib/api.ts). The helper now enqueues first, then SSE-tails `/api/jobs/{id}/stream`. `AbortController.abort()` cancels the job server-side (not just the fetch).

## Verification

1. **Queue gate (UI):** start `ingest` via UI → start `refresh` while the first runs. The second enters the queued list and begins after the first finishes.
2. **Queue gate (CLI):** while a UI job is running, run `imprint ingest <path>` from a terminal. Expect it to name the running job, print `Queued as <id>` and exit 0, with the job in `/queue`. With `IMPRINT_QUEUE_NO_WAIT=1`, expect exit code `1` and an error printing the holder's command, PID and start time and "Cancel it from the UI (/queue) or: kill <pid>".
3. **Cancel running (cascade kill):** start ingest with `IMPRINT_LLM_TAGS=1` + anthropic provider; hit cancel in UI mid-LLM-call. Within ~3 s `ps -ef` should show no python/httpx processes from that job. The row ends as `cancelled`, the lock file is released, the next queued job starts.
4. **Cancel queued:** enqueue A, B, C. Cancel B while A runs. B's status flips to `cancelled` immediately without ever starting; C runs after A.
5. **Restart recovery:** start a job; `pkill -f imprint.api`; restart. `/api/queue` shows the old running row as `failed` with `error='api_restart'`, the lock file is gone, new jobs enqueue normally.
6. **Process-group cascade:** during `retag` with the local `llama-cpp` tagger, cancel. `ps --forest` should show the subtree (llama.cpp threads, httpx workers, extractor helpers) gone in one shot, not staggered.
7. **Merge:** queue `refresh` of a folder twice while another job runs. The second request returns the first job's ID with `merged: true`; the queued row shows "2 requests"; the folder is refreshed once.
8. **Background tagging steps aside:** with `tagger.llm` on, run `imprint learn`; it finishes after embedding and prints how many memories wait. The Queue page shows background tagging running. Queue a URL ingest: tagging yields within one classification, the ingest runs, tagging resumes. Purge a file from the dashboard (or run `imprint update`) while tagging runs: it takes the lock within seconds, and tagging resumes after it.

## Workspace and lock integrity

New submissions store their workspace in the job body as `_workspace`. The dispatcher pins `IMPRINT_WORKSPACE` in the child; switching the dashboard's active workspace does not redirect an already queued job. Old jobs missing this field keep the previous active-at-execution behavior. Go and Python honor the same `IMPRINT_DATA_DIR`.

Stale metadata cleanup acquires the original lock inode before truncation and never unlinks it. Qdrant first startup has a separate shared lock with a health recheck, preventing competing CLI/API/MCP processes from starting two daemons. Collection creation has its own shared lock (`data/qdrant-collections.lock`), held by threads as well as processes: Qdrant lists a new collection, and answers 409 to a second create, before the first create finishes, and fails reads of it until then. On Windows this failed the dashboard's first overview with a 500. PID reuse, crash stress and complete Windows descendant cancellation still need dedicated tests; liveness alone is not process identity.

Queued `ingest` and `refresh` accept `force: true` and `batch_size: N`; equivalent CLI flags are preserved when enqueueing. Forced runs re-embed unchanged content within the same model profile.

## Browser capture prototype

The WIP [Chrome extension](browser-extension.md) submits bounded inline text to
`POST /api/browser/captures`. Its internal `browser-capture` jobs use the same
SQLite queue and ingestion lock, with a Python worker launched by job ID instead
of the Go CLI. This command is not added to the general HTTP command allowlist.
The paired workspace is pinned on submission and pairing is checked again before
indexing. Capture text is retained in the job body/history; the browser status
endpoint exposes only state and timestamps. The dashboard keeps normal local
queue visibility and cancellation. Revocation is not cancellation of a job
already indexing; multi-message captures can partly complete before a failure.
