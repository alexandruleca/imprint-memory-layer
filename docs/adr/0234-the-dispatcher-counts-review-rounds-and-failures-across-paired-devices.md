---
title: ADR 0234 — The Dispatcher counts review rounds and failures across paired devices
description: Imprint's note at the end of each Dispatched run carries a structured tag, and the two returned reviews and two failed sessions that hand a Task to the person are counted from those tags in the Task's synced history, so every paired device's runs count.
---

- Status: Accepted
- Date: 2026-11-28
- Amends:
  - [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) — its "Failures": the two failures that hand a Task to the person are counted on every paired device
  - [ADR 0221](0221-dispatched-sessions-review-tasks-in-review-with-another-client.md) — its "Second failed review": the review rounds are counted on every paired device
- Related: [ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md) (the Board's history and its sync), [ADR 0228](0228-a-dispatched-session-that-fails-to-start-ends-the-pass-and-counts-toward-switching-off.md) (failed starts are not the Task's), [ADR 0224](0224-a-dispatched-session-starts-only-after-its-claim-has-reached-the-paired-devices.md) (claims across devices)

## Context

A Task whose Dispatched sessions fail twice, or whose Dispatched reviews send it back twice, is assigned to the person ([ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md), [ADR 0221](0221-dispatched-sessions-review-tasks-in-review-with-another-client.md)). A review on 2026-11-28 found that both counts came from the device's own `dispatch.sqlite3` ([`guards.py`](../../imprint/dispatch/guards.py), `after_review`, `after_failure`). The runs table is device-local. With two paired devices dispatching, each counted only its own runs, so a Task could go four rounds before it reached the person.

What the devices share is the Board: its events merge by union on every paired device ([ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md)). At the end of a run, Imprint already comments on the Task when a review passes, and when a session fails or is stopped. The text of those comments is written for the person and changes with the wording. Imported events keep any extra keys in their data ([`merge.py`](../../imprint/tasks/merge.py), `import_events`).

The owner chose (2026-11-28) to mark each run's end on the Board with a structured tag rather than to infer the counts from the Board's existing events or leave them per device.

## Decision

**The tag.** When a Dispatched run ends as `passed`, `returned`, `failed` or `stopped`, Imprint's note on the Task carries `dispatch: {run, role, outcome, started}` in its data. `started` says whether the run's client started.

- **Where it goes.** The tag goes on the comments Imprint already writes: a passing review, and a failed or stopped session.
- **A returned review.** It gets a short note of its own ("A Dispatched review … sent this Task back to Ready").
- **Who may tag.** Only Imprint (the system actor) may tag a note: `tasks.comment` refuses `dispatch=` from an agent or the person.

**The counts.** The guards count how each run on the Task ended, from:

- the tags on Imprint's own notes in the Task's synced history;
- this device's own runs, which cover runs from before the tags.

They count each run once, by its id. Two `returned` reviews, or two `failed` runs whose client started ([ADR 0228](0228-a-dispatched-session-that-fails-to-start-ends-the-pass-and-counts-toward-switching-off.md)), hand the Task to the person, as before. A tag on a note that is not Imprint's is ignored.

## Rejected alternatives

- **Inferring the counts from the Board's existing events.** A returned review shows as a Dispatched session moving the Task from Review to Ready. A failed session differs from a stopped one only in the wording of Imprint's comment, so counting would break when that wording changes. The owner chose the tag.
- **Leaving the counts per device.** Each paired device would allow its own two rounds. The cost grows with the number of devices, and the person is told late.
- **A new event kind for run ends.** An older release stores an event of a kind it does not know, but skips it when it folds the Task's state ([`state.py`](../../imprint/tasks/state.py), `_fold`), so the person would not see the note on that device. A comment with an extra key already syncs and shows on every release.
- **Syncing the runs table.** The runs hold device-local facts (pids, worktree paths, log files). Only how a run ended matters to the other devices.

## Consequences

- With any number of paired devices, a Task reaches the person after two returned reviews or two failed sessions in all.
- Each returned review adds one short note from Imprint to the Task.
- A device on an older release writes no tags. Its runs count on that device as before, but not on the others.

## Invalidation conditions

- If the Board gains structured events for run outcomes (for example a Dispatched run's whole record syncing), count from those and drop the tag.
- If the person finds the returned-review note noisy, fold it into the reviewer's own comment, which would then need Imprint to write it.

## Validation

- `tests/test_dispatch_guards.py`, `PairedDevicesCountTests`, covers:
  - a review another device returned, arriving by sync, plus one returned here, handing the Task to the person;
  - a failure on another device plus one here doing the same;
  - another device's failed start, and a tag on an agent's note, not counting;
  - Imprint's note at a run's end carrying the tag;
  - only Imprint being able to tag a note.
- Linux (WSL Ubuntu 26.04, Python 3.12 venv): the `test_dispatch_*` modules pass.
- Not run: two real devices dispatching one Task.
