---
title: ADR 0068 — Merge identical waiting jobs
description: Submitting a job identical to one still waiting returns that job instead of queueing a second; one follow-up may wait behind an identical running job.
---

- Status: Accepted
- Date: 2026-10-25
- Amends: [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md) — a submission identical to a waiting job joins it instead of being queued
- Related: [ADR 0015](0015-workspace-roots-and-source-purge.md) (roots are checked before queueing), [ADR 0029](0029-paired-browser-capture.md) (browser capture jobs), [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md) (the bootstrap job already avoided duplicates), [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md) (background tagging), [queue](../queue.md)

## Context

Under ADR 0064 every `POST /api/commands/{cmd}` inserted a row, and a CLI command that found the lock held queued itself the same way. The same request queued twice ran twice: two refreshes of one folder, a learn clicked again while the first waited behind a long job, or a terminal and the dashboard asking for the same thing. Each run loads the embedding model and holds the single slot, so a duplicate costs a full job. The one place that avoided it was `_provision_runtime`, which returns a waiting `bootstrap` job rather than queueing another and queues one more when a `bootstrap` is already running, because the running one may have read the configuration before the change.

The owner approved merging duplicates on 2026-10-25: "3 sounds good as well to avoid duplicates".

Two things decide whether two submissions are the same job. The dashboard sends structured fields (`{"dir": …, "force": true}`), the CLI sends its own arguments (`{"args": [...]}`), and both reach the CLI through `queue.build_command_args`. And the CLI handed over relative paths as typed, which the job then resolved in the dashboard's working directory, not the terminal's.

## Decision

**A submission identical to a waiting job returns that job.** `queue.submit` compares the new job's key with each waiting job of the same command; on a match it returns `{job_id, merged: true}` and queues nothing. `POST /api/commands/{cmd}` returns `merged`. The waiting job counts the requests it answers (`_merged` in its body), and the Queue page shows it. Only waiting jobs absorb submissions: one identical to the running job queues a single follow-up, for the reason `_provision_runtime` gives, and further identical submissions join that follow-up.

**The key** (`queue.dedupe_key`) is the command, the job's workspace and the arguments `build_command_args` produces, whichever form the body used, normalized:

- positional arguments in order;
- options in any order; a repeated option keeps its last value, as the CLI parses it, except `--path`, which accumulates and is compared as a set;
- a path (the first argument of ingest and refresh, `--path`, `--from-file`) without a trailing separator or `.` and `..` segments, with the platform's case rules; symlinks are not resolved, and a relative path is left as given, since it has no meaning without the submitter's directory.

Browser captures are never merged: their body is captured content, not arguments.

**The CLI hands over absolute paths.** Ingest, refresh, learn (`--path`) and ingest-url (`--from-file`) make their paths absolute, translated under WSL as they already are, before queueing through the dashboard. The Go CLI prints `Already queued as <id>` for a merged job.

## Rejected alternatives

- **Merge with the running job too.** The running job may have read its inputs (files, configuration) before whatever prompted the new request; returning it would drop that request's work. `_provision_runtime` already made this choice for `bootstrap`.
- **Compare request bodies as sent.** The dashboard's fields and the CLI's arguments for the same job never compare equal, nor do two CLI argument orders.
- **Resolve symlinks in paths.** Two names of one directory can index under different project roots, so treating them as one job could drop a request that would have stored different sources.

## Consequences

- A duplicate request costs nothing; whoever follows the merged job's output follows the same job, and cancelling it cancels it for every request it answers.
- A CLI command handed to the dashboard now runs on the directory its user named; before, a relative path was resolved in the dashboard's working directory.
- Requests that differ only in a way the normalization does not cover (a relative path, an equivalent URL spelling, a symlinked path) still queue separately; they run twice, as before.

## Invalidation conditions

- If a command comes to depend on something outside its arguments and workspace (for example the time it starts), identical arguments no longer mean an identical job: exclude it from merging.
- If the dashboard or the CLI stops sending arguments through `build_command_args`, the key must follow the new path or merges stop matching.
- If a client needs to know about each request a merged job answers (for example separate completion notices), a count is not enough: record the requests.

## Validation

Executed on Linux (WSL2) and, for `tests/test_queue_priority.py`, natively on Windows 11 with the installed runtime's Python:

- `tests/test_queue_priority.py`: an identical waiting job absorbs submissions and counts them; dashboard fields and CLI arguments for ingest, refresh, ingest-url and learn give one key; a different path, option, workspace or command, a relative path and a different last value of a repeated option give different keys; one follow-up waits behind an identical running job and later submissions join it; finished and cancelled jobs absorb nothing; browser captures never merge; the API reports `merged`.
- `cmd/queue_args_test.go`: the CLI makes ingest's and refresh's path, learn's `--path` and ingest-url's `--from-file` absolute and leaves option values alone.
- `imprint/ui/tests/queue-background.spec.ts`: the Queue page shows how many requests a waiting job answers; the Commands page and the quick actions say when a run joined a waiting job.

Not validated: the Go CLI's `Already queued` message against a running dashboard.
