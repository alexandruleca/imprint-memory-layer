---
title: ADR 0243 — A Dispatched session that releases its Task at the person's word ends as stopped
description: When a Dispatched session releases its own claim after a comment from the person, as its skill tells it to on "stop" or "hold", the Dispatcher records the run as stopped rather than failed, so the person's word never counts toward handing the Task over or switching the Dispatcher off.
---

- Status: Accepted
- Date: 2026-11-28
- Amends: [ADR 0225](0225-a-running-dispatched-session-stops-when-it-no-longer-holds-its-claim.md) — its "the session released it unfinished: failed", for a release after a comment from the person
- Related: [ADR 0240](0240-the-persons-comments-reach-a-working-agent-after-its-next-tool-call.md) (the person's comments reach a working agent, and outrank its plan), [ADR 0234](0234-the-dispatcher-counts-review-rounds-and-failures-across-paired-devices.md) (failures counted across devices)

## Context

- [ADR 0240](0240-the-persons-comments-reach-a-working-agent-after-its-next-tool-call.md) brings the person's comment to a working agent after its next tool call. Its built-in skills tell the agent that the person's comment outranks its plan, and that "stop" or "hold" means record a checkpoint and release the Task.
- [ADR 0225](0225-a-running-dispatched-session-stops-when-it-no-longer-holds-its-claim.md) records a Dispatched session's own release as `failed`. The guards then count it: two failed sessions hand the Task to the person, and `dispatch.failure_limit` failures in a row switch the Dispatcher off ([ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md), [ADR 0234](0234-the-dispatcher-counts-review-rounds-and-failures-across-paired-devices.md)).
- So a session that did exactly what the person asked would count as a failure, and telling two sessions to hold would hand the Task back with a failure note.

## Decision

A Dispatched session that releases its own claim after a comment from the person, during its run, is recorded `stopped` ("released its claim at the person's word"), whether the Dispatcher notices the release while the session runs or only when it ends. A stopped run counts toward neither the Task's two failures nor `dispatch.failure_limit`, and its worktree is kept, as for any stop.

A release with no comment from the person since the run started stays `failed`, as before.

The `imprint-dispatch` skill tells the session to record a checkpoint, release the Task and end its run when the person says stop or hold.

## Rejected alternatives

- **Ending the run without releasing.** The session would then end without moving the Task to Review, which [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) records as `failed`: the same miscount.
- **Reading the comment's words for "stop" or "hold".** The person writes in their own words and languages. The session understands the comment; Imprint only sees that the release followed one.
- **Any comment from the person makes any later end a stop.** A comment such as "also update the docs" would then hide a session that later crashes or runs out of time. Only a release, the step the skill names for stopping, counts.

## Consequences

- The person can stop a Dispatched session from the Board's comment box without it counting against the Task or the Dispatcher.
- A session that releases right after an unrelated comment from the person is recorded as stopped rather than failed. Its note says so, and the person sees it.

## Invalidation conditions

- If sessions release after unrelated comments often enough to hide real failures, the stop requires the comment to be addressed to the session or the holder, and this record is amended.
- If the Board gains a direct "stop this session" action, that action replaces this path, and this record is superseded.

## Validation

T-7dnj3, 2026-11-28, `tests/test_dispatch_comments.py`: a fake client works until the person comments, then checkpoints and releases. The run ends `stopped` with "at the person's word", its end-of-run tag says stopped, the Task is back in Ready, and the failure count stays 0. With the check switched off, the same test fails. The existing test of a session that releases without a comment from the person still records `failed`. `test_dispatch_*` passes on Linux and Windows.
