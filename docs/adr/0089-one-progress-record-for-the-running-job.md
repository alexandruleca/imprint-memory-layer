---
title: ADR 0089 — One progress record for the running job, read by every surface
description: Every job that holds the queue lock, setup and the Stop hook's learner included, reports how far it is in data/progress.json, and the dashboard on every page, the desktop app while the dashboard server is down and a command waiting for the lock all show that record.
---

- Status: Accepted
- Date: 2026-10-27
- Amends: [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md) — the progress file is every lock holder's record, which more surfaces read
- Related: [ADR 0050](0050-registered-dashboard-server-and-supervised-restart.md), [ADR 0077](0077-stop-hook-hands-turns-to-one-waiting-learner.md), [ADR 0084](0084-setup-frees-the-runtime-before-replacing-its-files.md), [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md)

## Context

On 2026-10-26 the owner switched embeddings to the GPU from Settings on Windows and reported that progress was impossible to follow:

- **Setup reported nothing outside its own log.** The bootstrap that Settings started restarted the dashboard server (ADR 0084), then downloaded CUDA wheels for 3 min 11 s. The desktop window showed only "Local service is unavailable", and the dashboard did not come back until the app was reopened.
- **A waiting command named a process ID, not a job.** The Repair shortcut's bootstrap printed `Queued behind: learn (pid 34892)` for minutes, and only to its log file. The holder was the Stop hook's learner (ADR 0077), which wrote no progress, so the dashboard showed a `learn` job without numbers.
- **Progress was visible on two pages.** The home page and the Queue page showed the running job, which only Python ingestion jobs reported, and the card counted every job in "files".

Heavy work already runs one job at a time under the queue lock (ADR 0064), and Python jobs write `data/ingest_progress.json`, which `/api/queue` joins to the running job.

## Decision

**One record.** The job holding the queue lock reports in `data/progress.json`: its command, its phase, `processed` of `total`, and `unit`, what it counts (files, conversations, chunks, memories, pages or steps). A total of 0 is a step of unknown size. The record names its writer's process, and readers ignore it once that process has ended.

**Every lock holder writes it.** Python jobs keep `imprint/progress.py` and now give their unit. The Stop hook's learner reports reading, embedding with chunk counts, extracting and summarizing. Setup writes the same record from Go (`internal/queuelock`): its steps (installing dependencies, installing llama.cpp, checking document extractors) from when it holds the runtime until it releases it.

**Every surface reads it:**

- **Dashboard.** `/api/queue` passes the unit through. An indicator in the top bar shows the running job, or background tagging while it runs, on every page and links to the Queue page. Its components share one poll of the queue.
- **Desktop app.** It reads the file directly, because setup restarts the dashboard server and its launchers wait for setup (ADR 0084). While its service does not answer at startup, the splash names the running job. While setup reports, the app keeps waiting instead of failing after 60 seconds, since only setup holds the server back. While the server is down, the dashboard's offline banner asks the app for the record and says which job holds Imprint.
- **Commands.** A command waiting for the queue lock prints the holder's record in place of its process ID.

## Rejected alternatives

- **Keep the dashboard server running while setup replaces the runtime.** ADR 0084 settled why it must stop on Windows. With ADR 0086 a device change no longer replaces anything, but updates and repairs still do.
- **One progress file per job.** Only one job holds the lock at a time, so a second slot would only ever be empty or stale.
- **Let the desktop app ask the dashboard server.** The server is the thing that is down; the record is a file the app can read without it.

## Consequences

- During an update or repair, the desktop window says, for example, "Imprint is busy: setup · installing llama.cpp · 1/3 steps" instead of reporting the service unavailable.
- The Stop hook's learner and setup show progress in the dashboard and in waiting commands.
- The splash waits for setup as long as setup reports. A setup process that hangs while still alive keeps the splash waiting, and closing the window cancels startup.
- An installation's old `data/ingest_progress.json` is left unread.
- Model downloads report no progress of their own: a job that downloads its model shows its first phase until the download ends.

## Invalidation conditions

- If heavy jobs may run concurrently, the record needs one entry per job.
- If the dashboard server stays up while setup changes the runtime, the desktop app no longer needs to read the record itself.
- If model downloads become a common wait, report them in the record as a phase counted in bytes.

## Validation

- Python: `tests/test_progress.py` (the record, its unit, an ended writer's record and the queue's merge) and `tests/test_session_learn.py` (the learner's phases, and no record afterwards).
- Go: `internal/queuelock/progress_test.go` (writing, reading, clearing and an ended writer), `cmd/setup_test.go` (setup reports a step until it releases the runtime), and `go vet` for Linux, Windows and macOS.
- Electron: `ui-electron/activity.test.js` and `main.test.js` (the splash names setup's step; only the dashboard window may read the record).
- Dashboard: `tests/activity.spec.ts` (the indicator on another page and its link, background tagging, the idle queue, and the offline banner with the app's record), and the full Playwright suite.
- Not verified: a real update or repair through the desktop app on Windows or macOS.
