---
title: ADR 0064 — Serialize heavy commands with a single-slot queue and a shared lock file
description: One heavy job runs at a time per data directory; the Go CLI and the Python dashboard take the same advisory lock on data/queue.lock, and the dashboard queues its jobs in SQLite.
---

- Status: Accepted
- Date: 2026-04-19
- Recorded: 2026-10-25
- Retrospective: reconstructed from code, docs and git history: `imprint/queue.py`, `imprint/queue_lock.py`, `internal/queuelock/`, `cmd/queuelock.go`, `docs/queue.md`, the inline rationale this record replaces in `docs/architecture.md`, and commits f99e9fa (the queue and lock) and d080e89 (Python's Windows lock)
- Related: [ADR 0006](0006-durable-mcp-and-preserved-client-config.md) (queued jobs keep their workspace; Go/Python lock interoperability tests), [ADR 0007](0007-verified-runtime-and-desktop-baseline.md) (Qdrant's first start has its own lock), [ADR 0015](0015-workspace-roots-and-source-purge.md) (purge shares the lock), [ADR 0023](0023-update-by-installation-type.md) (update takes the lock), [ADR 0029](0029-paired-browser-capture.md) (browser capture jobs), [ADR 0053](0053-connect-clients-without-touching-the-runtime.md) (Commands page catalog; jobs get no standard input), [ADR 0063](0063-auto-spawned-local-qdrant-server.md) (concurrent access to the store itself)

## Context

The commands that index or rewrite memories each load the embedding model, scan Qdrant and, with LLM tagging on, hold a connection to the tagger's provider or run a local model. In f99e9fa (authored 2026-04-19) these were ingest, refresh, retag, learn, ingest-url and refresh-urls. `docs/queue.md` gives the problem: "Running two in parallel on a workstation is the fastest way to OOM the box." `docs/architecture.md` said the same: "Two of them running in parallel on the same box easily exhaust RAM or VRAM."

They start from two places: the Go CLI in a terminal, and the dashboard's Python API. Before f99e9fa, `POST /api/commands/{cmd}` spawned the CLI for each request and streamed its output, with nothing limiting how many ran at once, and `/api/jobs` could report at most one job from the single progress file. A dashboard action and a terminal command, or two dashboard actions, could therefore run together. The two starters are separate processes in different languages, so an in-process mutex on either side could not exclude the other.

The store itself already accepted concurrent clients (ADR 0063); the limit here is the machine's memory.

## Decision

**One heavy job at a time per data directory**, whichever side starts it, enforced by an exclusive advisory lock on `data/queue.lock`.

- **One lock, two implementations.** Go (`internal/queuelock`) uses `flock` on Unix and `LockFileEx` on Windows. Python (`imprint/queue_lock.py`) uses `fcntl.flock` on Unix and, since d080e89 (2026-04-29), `msvcrt.locking` on Windows. The holder writes `{pid, job_id, command, started_at}` into the file, so the other side can name it. Stale metadata is cleared without replacing the lock file.
- **The process doing the work holds it.** The Go CLI takes the lock for the heavy command it runs, and Python helpers it starts run under it. Later commands joined: purge (2026-10-23), update (2026-10-23, ADR 0023), move (2026-10-24), scrub and sync import (2026-10-25). The browser-capture worker takes it for its jobs (ADR 0029), and purge from the API or MCP, and move from the API, refuse while it is held.
- **The dashboard queues.** `POST /api/commands/{cmd}` writes a row to `data/queue.sqlite3` and returns its job ID and position. A dispatcher task in the dashboard server starts the oldest queued row only when no row is running and no other process holds the lock. It spawns the CLI in its own session (`start_new_session=True`) with `IMPRINT_QUEUE_FOREGROUND=1`, so the child waits for the lock instead of queueing itself again. The dispatcher does not hold the lock itself. The queue's history survives restarts: `recover_on_startup()` marks rows left running by a dead process as failed (`api_restart`) and clears stale lock metadata.
- **The CLI hands over or waits.** A CLI command that finds the lock held names the holder. With `IMPRINT_QUEUE_NO_WAIT=1` it exits 1. Otherwise ingest, refresh, learn, retag, scrub, ingest-url and refresh-urls enqueue themselves through the dashboard's API when the dashboard runs, and exit 0 with the job ID; any other command, or one without a dashboard, waits and retries every 5 seconds. `imprint update` refuses while a job holds the lock.
- **Cancelling stops the whole job.** A queued row is marked cancelled. A running job's process group gets SIGTERM, then SIGKILL 3 seconds later if it is still alive, so the CLI, its Python child, HTTP worker threads, llama.cpp threads and helper processes stop together. Windows has neither process groups nor SIGKILL here, so the dispatcher signals the process itself.

## Rejected alternatives

- **Spawning each dashboard command directly**, the design f99e9fa replaced. Nothing limited concurrent jobs, so two heavy jobs could exhaust memory, which is the problem stated above.
- **The dispatcher holding the lock while its job runs.** The code records why not: "The Go subprocess we spawn will itself acquire the queue flock — we don't grab it here, because that would deadlock the child. We rely on the DB's status='running' row to serialize the dispatcher loop."

No other alternative evaluation was recorded.

## Consequences

- Heavy jobs never overlap on one data directory, so peak memory is that of one job. The cost is waiting: a long ingest delays every job behind it. ADR 0035 kept first-chat provisioning off the queue for this reason.
- The lock is advisory. Work that does not take it, such as MCP reads and writes, hook summaries, search and chat, runs alongside a job and shares Qdrant (ADR 0063).
- A job started in a terminal holds the lock without a queue row; the dashboard shows it as the active job from the lock file and the progress file.
- The two implementations must keep excluding each other on every platform; an interoperability test guards this.
- `docs/architecture.md` and `docs/queue.md` described the CLI as exiting nonzero when the lock is held, and `docs/architecture.md` described the dispatcher as waiting on the lock itself. The code has behaved as described here since f99e9fa; those pages are corrected with this record.

## Invalidation conditions

- If heavy commands stop loading their own embedding model and tagger, for example because one long-lived process serves embedding and tagging to all of them, the memory multiplication that motivates one job at a time is gone: revisit running jobs concurrently or a queue with several slots.
- If all heavy work comes to be started by one long-lived process, that process's own queue can replace the cross-language lock file.
- If a data directory sits on a file system where `flock` or Windows byte-range locks do not exclude other processes, for example some network file systems, the lock excludes nothing: detect that, or refuse such a data directory.

## Validation

No validation was recorded with the original decision; f99e9fa added manual verification steps to `docs/queue.md` without a record of running them. Checks that exercise the decision today:

- `internal/queuelock/interop_test.go`: while Go holds the lock, Python's lock call fails; it runs in native CI on each platform (ADR 0006).
- `tests/test_queue_safety.py`: stale-metadata cleanup never replaces an active lock's file.
- `tests/test_workspace_diagnostics.py`: a queued job keeps the workspace it was submitted for after a switch.
- `tests/test_browser_capture.py`: the capture worker takes the shared lock and releases it on failure, and the dispatcher records the failure.
- `tests/test_commands_catalog.py`: a queued job reads end-of-file from standard input (ADR 0053).

Not covered by automated tests: the CLI's busy path (handing over to the API, waiting, `IMPRINT_QUEUE_NO_WAIT`), cancellation escalation, and complete cancellation of a job's descendants on Windows, which `docs/queue.md` lists as still needing dedicated tests.
