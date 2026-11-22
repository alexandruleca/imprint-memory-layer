---
title: ADR 0201 — Upkeep brings what is stored up to date in the idle slot
description: The background job's last step works through a list of finite, resumable steps that bring memories stored by earlier releases to the current form, a piece at a time, at low priority, resting between pieces and stepping aside for any job. Its first steps date synced memories back and keep this device's large data files whole without a refresh.
---

- Status: Accepted
- Date: 2026-11-22
- Amends:
  - [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md) — the background job ends with upkeep, and the dispatcher starts it for upkeep alone too
  - [ADR 0132](0132-organize-runs-in-the-idle-slot-after-background-tagging.md) — a third step, upkeep, follows tagging and Organize in the same job
  - [ADR 0195](0195-data-files-are-kept-whole-and-found-by-a-digest.md) — stored data files are kept whole in the idle slot, not only at the next ingest or refresh
- Related: [ADR 0200](0200-synced-memories-keep-the-time-they-were-first-stored.md) (the dates upkeep repairs), [ADR 0196](0196-a-file-gone-from-disk-keeps-its-snapshot.md) (missing files stay refresh's to mark), [ADR 0176](0176-deletions-reach-paired-devices-that-hold-the-memory.md) (replaced chunks leave paired devices too), [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md), [queue](../queue.md), [configuration](../configuration.md)

## Context

- **Changes in form reach only what is written again.** When a release changes how memories are stored, memories stored before it keep the old form until something writes them again. Two such changes waited on 2026-11-22:
  - **Data files (ADR 0195).** They are kept whole only when the device holding them next ingests or refreshes their folder. On the owner's Windows PC that is 443 files holding 176,288 chunks; 63 files wait on the Mac and one on `bedrock`.
  - **Dates (ADR 0200).** Memories received from paired devices before that fix keep their arrival date. On the same PC that is 152,317 memories.
- **What the owner asked for.** A background process that takes care of this under the hood:
  - non-invasive, and without consuming a lot of resources;
  - piece by piece, until only new things remain;
  - in a way that helps with future changes too — "like what sleep does for the human brain".
- **The idle slot already exists.** One preemptible background job runs when no other job runs or waits, and steps aside for any of them (ADR 0067). ADR 0132 added Organize to it as a second step, after rejecting a second background job.
- **WSL shares need care.** The owner's Windows runtime indexes WSL projects through the `\\wsl.localhost` share. Reading a file there starts its distribution when it is stopped.

## Decision

1. **A third step in the background job.** After tagging and Organize, with `upkeep.background` on (the default), the job runs upkeep ([`upkeep.py`](../../imprint/upkeep.py)).
   - The dispatcher counts upkeep's due steps as background work, so it starts the job for upkeep alone too.
   - Changing an `upkeep.*` or `ingest.data_file*` setting wakes the dispatcher.
2. **Steps.** Upkeep works through a list of steps. Each has a cheap check for whether it is due, and a run that works a piece at a time, can resume, and is safe to repeat.
   - **`timestamps`** (ADR 0200), once per workspace:
     - It reads memories recorded by other devices in pages of 1,000.
     - Each whose `timestamp` is more than a minute after its `recorded_at` takes `recorded_at`, capped at the current time.
     - A resume point is saved after each page.
   - **`data-files`** (ADR 0195):
     - **Which files.** This device's data files still held as record chunks that ingest now keeps whole, chosen from the snapshots alone. That means files whose source device is this one and whose path is local, judged by the recorded size and the path within the project.
     - **How.** Each is indexed again from disk, kept whole, through the same records and `replace_source` as a refresh (`data_files.reindex`).
     - **Peers follow.** This device recorded the old chunks, so their deletion reaches paired devices (ADR 0176).
     - **One direction only.** Turning `ingest.data_files` to `records` still needs a refresh.
3. **Due without the vector store.** The due check reads only:
   - the known workspaces;
   - the ledger;
   - for `timestamps`, whether this device may hold synced memories: it holds a pair (a row in `sync-pairs.sqlite3`, read only, since the API creates that store empty) or trusts a device (`trusted_devices.json`), or the workspace's write log records a sync import (a forgotten pair leaves that trace). The log is read once per process, then only what was added;
   - for `data-files`, the snapshot database: the count and latest update of data-file snapshots, plus the two data-file settings. With no data-file snapshot the step is not due.

   So the dispatcher's polls never start or reach a vector store, and a fresh store has nothing due. A workspace without a collection has its steps settled without one being created.
4. **A ledger per device.** `data/upkeep.json` is never synced. It holds:
   - each workspace's finished step versions and resume point;
   - the data-file signature at the last full check;
   - the WSL distributions files wait for;
   - the files a step could not handle, with their content hash and time.
5. **Gentle by construction.**
   - **Priority.** Before upkeep, the job's own process drops to Windows' background mode (lower CPU, disk and memory priority), or to `nice 10` elsewhere. Upkeep is the last step of that process. Direct callers of the job's function, such as tests, keep their priority.
   - **Rest.** After each piece it rests as long as the piece took, at most 5 seconds.
   - **Yield.** Between pieces it stops when a job is queued (recorded as `yielded`) or the setting is turned off.
   - **Budget.** One run spends at most 10 minutes on upkeep; the next idle run goes on after tagging and Organize.
   - **WSL.** On Windows, a file in a `\\wsl.localhost` share is touched only while its distribution runs. `wsl.exe --list --running` reports that and starts nothing. Otherwise the file waits, and the step is due again once that distribution runs.
   - **Files it cannot handle.** A file that is not on disk, cannot be read, or is no longer one to keep whole is left as it is. It is recorded with its content hash and tried again after 7 days, or when its snapshot changes.
   - **Missing files.** Upkeep never marks a file missing; that stays with refresh (ADR 0196), which knows which folders it scanned.
6. **In the foreground.** `imprint upkeep [--dry-run]` runs what is due now, under the queue lock (or in the dashboard's queue when the dashboard holds it). It does not rest or lower its priority, and `--dry-run` says what it would change. The dashboard's Commands page lists it as **Bring memories up to date**.
7. **Visible in the dashboard.**
   - **Queue page.** The **Background work** card names upkeep and how many of its steps are due. A running pass reports phase `upkeep`.
   - **Settings.** Advanced settings have an **Upkeep** section, and the simple settings list **Keep old memories up to date**.
8. **The next change.** A later change to how memories are stored adds a step to the list, or raises a step's version so it runs once more everywhere.

## Rejected alternatives

- **Refresh every indexed folder in the background.**
  - It would walk and fingerprint every file of every folder to find the few that need changing: the owner's store has about 72,000 sources, many read through the WSL share, for 443 conversions.
  - It would also mark files missing whenever a drive or distribution is offline.
  - Upkeep reads the snapshots to find exactly the files to change.
- **A one-time migration at update or startup.**
  - Dating 152,317 memories and converting 443 files takes tens of minutes. That would hold up the update or the dashboard's start.
  - It would happen only once, so later format changes would need another one.
- **A separate background job or a timer.** ADR 0132's reasons hold:
  - one job means one preemptible holder, one schedule and one Queue page state;
  - a timer runs whether or not anything is due.
- **Keep data files whole from their snapshots, without reading the file.** The snapshot of a record-chunked file holds its redacted text, so it could be kept whole without the disk. But a file that changed since is better read again, and a file gone from disk is refresh's to mark. This may become a later step for files on devices that no longer exist.
- **Merge near-duplicate memories as a step.** The audit found 8% near-duplicates among extracted memories. Deleting memories is not a mechanical change of form, and the owner wanted upkeep non-invasive, so merging belongs in a reviewed Organize proposal.

## Consequences

- **On the owner's PC**, the first idle runs date back 152,317 memories in a few minutes. Over several runs they keep 443 data files whole, turning 176,288 chunks into about one or two per file, without a refresh. The Mac and `bedrock` convert their own files, and the deletions shrink every paired store.
- **Every device that synced checks each workspace once** after updating, even when nothing needs changing: one filtered scroll. A device that never synced, or holds no data file, starts no run for those steps.
- **The background job also runs where tagging and Organize are off**, whenever upkeep has work. It ends at once when everything is up to date.
- **While it keeps a file whole**, the job loads the embedding model, as ingest would.
- **Without the dashboard** nothing runs in the background; `imprint upkeep` does the same work.
- **Unreachable files** (an unmounted drive, a deleted file not yet refreshed) are tried again weekly. Each attempt is one existence check.

## Invalidation conditions

- If upkeep slows the computer noticeably while the owner works, then also require idle input or mains power before it starts.
- If a step needs far more than a few idle runs (for example embedding every memory again after a model change), then give it its own budget and an estimate on the Queue page.
- If steps multiply, then order them by cost and show each step's progress on the Queue page.

## Validation

- **`tests/test_upkeep.py`** (20 tests, Windows Python 3.13 venv, native copy) covers:
  - **Timestamps step:** received memories take back their origin's time once; memories recorded here, memories without provenance and memories already on time are left alone; a peer's clock running ahead never dates the future.
  - **Resuming:** after a queued job stops the step, the next run resumes from the saved page.
  - **Dry run:** it changes nothing and writes no ledger.
  - **A workspace without a collection** is settled without one being created.
  - **Data-files step:**
    - a file stored as record chunks (98 KB of fixtures) becomes one chunk, with the snapshot still restoring it byte for byte;
    - small data files and code are untouched;
    - a file in a stopped WSL distribution waits for it;
    - a file gone from disk is left and tried again after a week;
    - lowering `ingest.data_file_min_kb` looks again;
    - another device's files are left to that device.
  - **Background:**
    - the dispatcher counts upkeep's steps as work and the Queue state reports them;
    - the job yields to a queued job;
    - resting happens only in the background;
    - the 10-minute share ends a run;
    - only the job's own process lowers its priority;
    - the due check never reaches the vector store;
    - a fresh store has nothing due, and an unpaired device's write log showing a sync import makes the timestamps step due.
  - **Commands:** the Commands page queues it with `--dry-run`, and the CLI refuses unknown arguments.
- **Suites that pass:** `test_queue_priority` and `test_organize` (which now count tagging and Organize apart from upkeep), plus `test_background_tagging`, `test_device_provenance`, `test_restore`, `test_data_files` and `test_topics`.
- **Live dry run** (read-only, owner's store): it would date 152,317 memories and keep 443 files whole, 176,288 chunks in all. Three files missing from disk would be left as they are.
- **Go:** `go vet` and `go test ./cmd/ ./internal/...` pass, and `imprint upkeep --help` prints its usage.
- **Dashboard:**
  - `tsc` and `eslint` pass.
  - `tests/queue-background.spec.ts` passes, with 2 new tests: a running upkeep pass with steps left, and an idle slot that is up to date.
  - `next build` generated all pages, then stopped at its security notice for the installed Next.js 16.4.2 (`package.json` asks for 16.4.6).
- **Not yet run:** upkeep against the live store for real, macOS, and Linux.
