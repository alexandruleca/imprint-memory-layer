---
title: ADR 0067 — Background LLM tagging that yields to every other job
description: Storing paths mark memories for LLM tagging instead of running the tagger; a background job tags the marked memories newest first when no other job waits, steps aside between memories, and holds the queue lock as a holder any other taker may stop.
---

- Status: Accepted
- Date: 2026-10-25
- Amends: [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md) — a preemptible background job runs when nothing waits, the lock names preemptible holders and is released with its metadata cleared, and Python's Windows lock byte moves past the metadata
- Related: [ADR 0006](0006-durable-mcp-and-preserved-client-config.md) (provider selection never enables LLM tagging), [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md) (the tagging measurement), [ADR 0051](0051-synced-tags-follow-the-latest-retag.md) (`tagged_at`), [ADR 0060](0060-redact-secrets-before-memories-are-stored.md) (redacted tagger prompts), [ADR 0068](0068-merge-identical-waiting-jobs.md) (merged submissions), [queue](../queue.md), [tagging](../tagging.md)

## Context

With `tagger.llm` on, every path that stores memories in bulk ran the LLM tagger itself before it finished. ADR 0049 records the cost on the owner's machine: `imprint learn` stored 73 of 140 conversations in about six hours, and the local tagger took 38 s per exchange on CPU against about 1.5 s for the embedding, so tagging was about 95% of the time. Under ADR 0064's single slot, a learn held the queue for those hours: a URL ingest, a scrub dry run or a one-file refresh waited behind it.

Reading the code for this change showed more:

- learn and ingest tagged each memory twice. `cli_conversations.index_transcript`, the directory path of `cli_index` and `ingest_core` called `build_payload_tags(llm=None)`, which classifies with the LLM when `tagger.llm` is on, and their phase 2 (`_llm_tag_recent`) classified the same memories again.
- learn's phase 2 also ran when `tagger.llm` was never set and the provider was the default `local`, against ADR 0006 and `docs/tagging.md` ("Selecting a provider does not enable LLM tagging").
- The Stop hook ran the tagger inside the hook process for every exchange it imported and every message it extracted.
- MCP `store` never runs the tagger: it keeps the type the agent chose (ADR 0006). Session summaries, memories stored with LLM tagging off, and memories received from another device have no LLM tags either, by design.

The owner approved, on 2026-10-25, splitting LLM tagging out of learn and ingest into a resumable background job that pauses whenever a real job arrives and resumes after a crash or cancel, and letting small jobs run without waiting behind a long one: "i like option 1 and 2 honestly".

## Decision

**Storing never runs the LLM tagger.** Learn, ingest, refresh, ingest-url, refresh-urls, desktop learn, the Stop hook's import and extraction, and inline content (MCP `ingest_content`, browser capture) store memories with deterministic, keyword and zero-shot tags. While `tagger.llm` is on they also set `tag_pending: true` on each memory they store; with it off they set nothing, as no LLM tagging happened before either. MCP `store` and session summaries are unchanged. The legacy LanceDB import keeps its rows' types and is not marked.

**The backlog is the memories marked `tag_pending`.** It is a payload field with a boolean index, not a separate store. Classifying a memory goes through `vectorstore.set_classifications`, which writes the tags, the type when the LLM gave one, `llm_tagged`, a `tagged_at` past the previous stamp (ADR 0051) and `tag_pending: false` in one payload update. A classification received by sync clears the marker too. The marker never leaves the device: sync does not carry it, so the device that stored a memory tags it, and its tags reach the others through `tagged_at`.

**The background job** (`python -m imprint.tag_backlog <job_id>`, command `tag-backlog`):

- tags every known workspace's backlog, the active workspace first, each **newest first** (by `timestamp`): the memories of the session in progress are the likeliest to be searched next, and a fresh learn's oldest conversations can wait. A memory without a timestamp comes after the dated ones;
- classifies one memory at a time and writes it before the next, so a crash, cancel or stop loses at most the memory being classified, which stays marked;
- before each memory, stops with exit code 75 when a job is queued, or with 0 when `tagger.llm` was turned off;
- refuses to start, with exit code 1 and without touching a memory, when `tagger.llm_unavailable()` reports the tagger cannot work (no llama.cpp binding or model for `local`, no API key for a provider that needs one), instead of stamping every memory with the empty fallback classification;
- prompts go through `tagger._call_llm_raw_text`, which redacts them (ADR 0060).

**One slot; every other job first.** The dispatcher starts the background job only when LLM tagging is on, no job runs or waits, no other process holds the lock, and memories are marked. It looks after each job ends, when a job held from a terminal releases the lock, when a tagging setting changes, on request (`POST /api/queue/background/resume`), at startup and every 30 seconds otherwise (the Stop hook and inline content store without a job). A run ends as `done` (backlog empty), `yielded` (it stepped aside, or another lock taker stopped it), `cancelled` (by the user) or `failed`. After a cancel it waits until the number of marked memories changes; after a failure, 30 minutes; a settings change or resume clears both. After any run, the next start waits for the next poll, so it does not race a process that stopped it for the lock. The dispatcher keeps the latest 20 background runs and their logs. A run the previous server left behind is stopped and recorded as yielded at startup.

**The background job is a preemptible lock holder.** It takes `data/queue.lock` itself (Python, one process) and writes `"preemptible": true` into the holder metadata. Any other taker, Go (`queuelock.TryAcquire`) or Python (`queue_lock.acquire`), that finds a live preemptible holder stops it (SIGTERM on Unix, then SIGKILL after 3 s; `TerminateProcess` with exit code 15 on Windows) and takes the lock within a few seconds, instead of refusing (update, purge and move from the dashboard or MCP), waiting (the CLI's purge, move and sync import) or failing (`IMPRINT_QUEUE_NO_WAIT=1`). The background job never preempts. To make this readable on Windows, where locks are mandatory and the old Python lock on byte 0 hid the metadata from every other process, Python locks a byte at offset 2^30, past any metadata; Go's lock still covers the whole file, so the two exclude each other as before. A holder now clears its metadata before it unlocks.

**Without the dashboard** nothing tags in the background. A storing command says how many memories wait and that `imprint retag --pending` tags them now; that new option drains the backlog in the foreground, newest first. `imprint retag` keeps its meaning (every memory not yet LLM-tagged), so it also covers the backlog.

## Rejected alternatives

- **Keep tagging inside learn and ingest.** The measured 95% of a learn's time stays in the slot, and the double classification and the Stop hook's inline calls remain.
- **Use `llm_tagged != true` as the backlog**, as `imprint retag` does. It also selects memories that were never meant to get LLM tags: MCP `store` memories, whose agent-chosen type the classification would replace, session summaries, memories stored with LLM tagging off and memories received from another device. A background job would rewrite all of them without being asked.
- **Only a cooperative yield, no preemption.** Update refuses while the lock is held, purge and move from the dashboard and MCP refuse, and the CLI's purge, move and sync import wait: all of them would be blocked for as long as the backlog lasts, hours on the measured machine. Only processes that queue through the dashboard can be seen between memories.
- **Have every taker identify the background job through the queue database or the dashboard API.** The Go CLI has no SQLite, and asking the dashboard would make taking a file lock depend on an HTTP server that may not be running. The lock's own metadata already exists to name the holder (ADR 0064).
- **Keep Python's Windows lock on byte 0.** Run natively on Windows with the old offset, the Go interop test could not read the Python holder's metadata (`ReadHolder` returned nothing), so no taker could see that it may stop the holder.
- **Start the background job again as soon as it yields.** In the dispatcher test the new run took the lock back before the process that had stopped it got it, and that process's wait failed.
- **A second slot for small jobs.** ADR 0064's reason for one slot (each heavy job loads its own models) still holds; with the background job stepping aside, a small job waits at most for one classification or for the few seconds a stop takes.
- **Stop the background job for queued jobs too, instead of letting it yield.** It would lose the classification in progress every time a job is queued; between memories it loses nothing.

## Consequences

- learn, ingest and the Stop hook finish once memories are embedded and searchable; their LLM tags arrive later. Until then memories carry keyword domains, zero-shot topics (when `tagger.zero_shot` is on, now also while `tagger.llm` is on) and the regex classifier's type.
- Each memory is classified once, not twice, and the Stop hook no longer loads a model per exchange.
- Installations that never set `tagger.llm` and use the default `local` provider no longer get LLM tags from learn; that was the ADR 0006 violation above. Setting `tagger.llm` restores LLM tagging, in the background.
- The dashboard's `tagger.llm` decides whether the backlog drains: a terminal session with `IMPRINT_LLM_TAGS=1` and a dashboard without it marks memories the dashboard will not tag; `imprint retag --pending` tags them.
- A stop by another taker loses the classification in progress (one LLM call). Stopping a process by the PID in the lock file can hit another process if the holder died and its PID was reused before anyone cleared the metadata; holders now clear it on release, which leaves a crash in that window.
- A remote provider that fails during a run still produces the empty fallback classification per memory, as `imprint retag` and inline tagging always did; only an unusable configuration is caught before a run.
- A Python process from before this change still locks byte 0 on Windows and does not exclude a new Python process (it does exclude Go); such processes (an MCP server's purge) are replaced when their clients restart them.
- New API: `background` in `GET /api/queue` and `POST /api/queue/background/resume`. The Queue page shows background tagging's state, the marked memories and its current or latest run apart from the jobs; background runs raise no job toasts.
- `release()` clearing the metadata also fixes the dashboard naming itself as the holder after a purge or move from the dashboard, which kept its dispatcher from starting queued jobs.

## Invalidation conditions

- If one long-lived process comes to serve tagging to every command, the background job and its preemption can move into that process's own scheduling (ADR 0064's second condition).
- If classification becomes cheap enough to cost about as much as embedding (for example a GPU tagger near the 1.5 s embedding time in ADR 0049), revisit tagging inline, which keeps memories fully tagged when the command ends.
- If another job needs to run in the background, the single `preemptible` flag becomes a priority order between holders.
- If Windows locks stop being mandatory for reads, or both sides adopt a lock that does not cover the metadata, the separate Windows lock byte is no longer needed.
- If the marked memories keep growing faster than the backlog drains on a supported configuration, the job needs batching or a cheaper model, not more priority.

## Validation

Executed on Linux (WSL2) unless noted:

- `tests/test_background_tagging.py`: only marked memories are counted and tagged, newest first, with tags, type, `llm_tagged`, `tagged_at` and a cleared marker in one write; an agent's `store` memory and a memory stored with LLM tagging off are untouched; a stop between memories leaves the rest marked and a rerun resumes; a crash loses only the memory being classified; a memory deleted while it was classified is skipped; one without a timestamp is still reached; conversation fields survive; the prompt reaching a provider is redacted; a classification received by sync clears the marker, and the sync export does not contain it; the worker yields to a queued job and finishes on its next start, refuses without a usable tagger, does nothing with LLM tagging off, covers every workspace active one first, holds the lock as preemptible and releases it; `retag --pending` drains only the backlog; learn's conversation import, single-file ingest, inline content and the Stop hook's extraction mark memories without calling the LLM.
- `tests/test_queue_priority.py`: waiting and running jobs and a terminal's lock hold background tagging back; run endings map to statuses; a cancel pauses it until the backlog changes, a failure until settings change, a yield holds nothing back; old runs are pruned; startup stops a run left behind; `/api/queue` reports it apart and resume clears a pause; a tagging setting change lets it start. With the real dispatcher loop and stand-in processes: a queued job runs first and background tagging resumes after it; a lock taker stops it and it resumes after the taker releases. A preemptible Python holder is stopped by a Python taker; other holders are not; a preemptible taker stops nobody.
- `internal/queuelock/interop_test.go`: Go stops a preemptible Python holder and takes the lock, leaves a non-preemptible one running, and a released lock names no holder.
- End to end against a real Qdrant v1.17.1 server, with the real dispatcher and worker and a stand-in OpenAI-compatible model (0.4 s per call) through the vllm provider: 12 marked memories were tagged newest first; the run yielded (exit 75) to a queued job after two, the job ran, tagging resumed; a purge-like taker got the lock in 0.06 s by stopping the run (exit -15), the memory in progress was classified again after the release, the backlog emptied, and an agent's `store` memory kept its type.
- Natively on Windows 11 through WSL interop, with the installed runtime's Python 3.12: the Go interop tests (cross-compiled) pass, including stopping a Python holder (exit code 15); with Python's lock moved back to byte 0 the metadata test fails as described. `tests/test_queue_priority.py`, `tests/test_background_tagging.py`, `tests/test_queue_safety.py`, `tests/test_browser_capture.py`, `tests/test_workspace_purge.py`, `tests/test_projects.py`, `tests/test_enqueue_root_approval.py`, `tests/test_workspace_diagnostics.py` and `tests/test_commands_catalog.py` pass.
- The full Python suite, `go test ./...`, the Electron `node --test` suite and the dashboard's Playwright suite (`imprint/ui/tests/queue-background.spec.ts` for the Queue page) pass.

Not validated: a real multi-hour learn followed by draining its backlog with the local llama.cpp tagger; macOS; the dashboard's dispatcher running natively on Windows (only the lock, the worker logic and the queue tests ran there); the Go CLI's hand-off to a running dashboard (`acquireOrEnqueue`) end to end.
