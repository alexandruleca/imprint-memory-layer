---
title: ADR 0132 — Organize runs in the idle slot after background tagging
description: With organize.background on, the background job that tags memories when the queue is idle then organizes what changed since its last pass, as the same preemptible holder, and steps aside for every other job.
---

- Status: Accepted
- Date: 2026-11-05
- Amends:
  - [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md) — the background job also organizes, after tagging, when `organize.background` is on
  - [ADR 0130](0130-reorganization-is-proposed-by-a-background-job-and-reviewed.md) — Organize also runs by itself, incrementally, not only as a queued job
- Related: [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md), [ADR 0133](0133-strong-placements-apply-by-themselves.md), [ADR 0134](0134-placement-rules-and-organize-proposals-sync.md), [queue](../queue.md)

## Context

ADR 0130 made Organize a queued job the user starts. After it shipped, the user asked for it to work like background tagging: run by itself when enabled.

ADR 0130 noted that "running it in the background would need ADR 0067's single preemptible flag to become a priority order". That holds only for a second background job beside tagging. ADR 0067's job is one preemptible lock holder that every other job stops.

A full Organize run is slow on a local model. On an Apple M3 with Qwen3.5-4B, a model placement takes about 14 s per source and a profile 4–12 s (ADR 0130). Running all of that every time the queue goes idle would keep the model busy for nothing.

## Decision

A new setting, `organize.background`, off by default, lets the background job organize.

**One job, two steps.** When no job runs or waits, the dispatcher starts the background job (`tag-backlog`, ADR 0067) if LLM tagging is on and memories wait for it, or if `organize.background` is on and Organize has something new. The job tags first. Then, with the setting on, it organizes each workspace that changed. It holds the lock as the same preemptible holder, so any other taker still stops it. Between sources, profiles and preference memories it checks for a queued job and exits as `yielded` (exit code 75). A pass that yields is not recorded, and the next pass continues: each source already analysed is skipped by its fingerprint.

**What counts as new.** A workspace has something new for Organize when a signature of what the pass depends on differs from the last finished pass's. The signature is read from payload indexes and counts, without a scan:

- the catch-all projects' sizes, and the number of memories without a project;
- the known projects;
- the number of preference memories;
- the profiles due, when the local helper model is installed;
- the `organize.auto_move` setting.

The dispatcher checks this every 30 s while idle, as for tagging. After a cancel, the job waits until the combined count of memories to tag and workspaces with changes moves (ADR 0067's rule).

**An incremental pass.** It writes a profile only when it is missing, or when its project has gained or lost at least 20 memories or a tenth of them since it was written. It analyses only catch-all sources that are new or whose text or candidate projects changed. Sources found to have nothing to propose are remembered, so they are not analysed again. It reads only preference memories not read before for core memory candidates. The local helper model loads only when a step first needs it. A queued Organize from the dashboard or `imprint organize` still writes every profile again.

**Where it shows.** The Queue page's card becomes **Background work**: its state, the memories waiting for tagging, whether LLM tagging and Organize are on, and how many workspaces changed. A running pass says whether it is tagging or organizing. The Organize page says whether Organize runs by itself.

## Rejected alternatives

- **A second background job for Organize.** Each would need its own preemptible holder, scheduling and Queue page state. Two background jobs would also need a priority order between them, which ADR 0130 named as the cost of running in the background. One job doing two steps reuses ADR 0067's lock, yield, cancel and failure handling unchanged.
- **A full run on every idle period.** On the maintainer's workspace, 363 catch-all sources at about 14 s each for the model's share is over an hour of model time, repeated whenever anything was stored.
- **Running Organize on a timer.** A timer runs whether anything changed or not, and still needs the yielding a background holder already has.

## Consequences

- With the setting on, memories sorted into catch-alls are looked at soon after they arrive, without the user starting anything.
- Organize's own changes (profiles written, sources moved) change the signature, so one more pass follows each pass that changed something. That pass finds nothing new for the model and does not load it.
- The setting is per device, like every setting. Turned on on two paired devices, both analyse the same sources. Their proposals reach each other (ADR 0134), but their profile notes are separate memories, which sync never deletes.
- `organize.background` and `tagger.*` settings changes wake the dispatcher at once.
- The background job's label is now "Background work".

## Invalidation conditions

- If the background job grows a third step, or the two steps need different priorities, replace the single job with an ordered list of background tasks.
- If the signature's index reads prove slow on large workspaces (the dispatcher reads it every 30 s), replace it with a change counter that writers bump.
- If a profile written for a count change of 20 memories or 10 % proves too often or too rare, change those thresholds.

## Validation

- `tests/test_organize.py`, `BackgroundTests`:
  - a pass runs only when something changed, and none runs with the setting off;
  - a waiting job stops a pass, which is not recorded, and the next pass finishes it;
  - the model is asked once per source, profile and preference memory across passes;
  - a pass with nothing for the model does not load it;
  - the idle slot organizes after tagging, and the background state reports both parts.
- `imprint/ui/tests/queue-background.spec.ts`: the Background work card while organizing, idle with Organize on, and the existing tagging cases under the new name.
- The full Python suite (892 tests) and the dashboard's 169 browser tests pass on macOS.
- Not verified: a background pass on a real workspace with the local model, and the signature's cost on a large workspace.
