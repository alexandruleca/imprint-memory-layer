---
title: ADR 0221 — Dispatched sessions review Tasks in Review, with another client where one is available
description: The Dispatcher starts a reviewer session for Tasks in Review that are not the person's, preferably in a different client from the one that did the work; a passing review moves the Task to Done, a failing one sends it back to Ready with the findings, and after two failed reviews the Task goes to the person.
---

- Status: Proposed
- Date: 2026-11-27
- Related: [ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md) and [ADR 0211](0211-agents-take-ready-and-stale-tasks-unasked-within-their-project.md) (their rule that agents review only when asked is what this record would amend once accepted, for Dispatched sessions), [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) (the Dispatcher), [ADR 0222](0222-only-clients-with-a-sandbox-or-a-check-of-each-action-are-dispatched.md) (which clients), [ADR 0223](0223-dispatched-sessions-work-in-a-worktree-outside-the-project-and-never-push-or-merge.md) (branches and merging)

## Context

The owner asked (2026-11-27) that the Dispatcher also pick up "tasks that need review".

- A holder that finishes moves its Task to Review. The person, or a different agent session that reviewed the work, moves it to Done. The session that did the work cannot: [`changes.py`](../../imprint/tasks/changes.py), `move`, compares the mover's session with the Task's `worked_by`. Agents review only when asked ([ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md), [ADR 0211](0211-agents-take-ready-and-stale-tasks-unasked-within-their-project.md)).
- A Task in Review can be claimed and stays in Review ([`state.py`](../../imprint/tasks/state.py), `_claimed`), so a claim can keep two reviewers off the same Task without a new mechanism.
- More than one client may be available on a device ([ADR 0222](0222-only-clients-with-a-sandbox-or-a-check-of-each-action-are-dispatched.md)).

## Decision

**Which Tasks.** The Dispatcher takes Tasks in Review that are not assigned to the person, whoever did the work: a Dispatched session or an interactive one. Reviews go before new work ([ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md)).

**Which client.** The reviewer runs in a client allowed on the device that differs from the client that did the work, so one model checks the other. When only one client is allowed, a fresh session of that client reviews.

**The review.** The reviewer claims the Task, which stays in Review. It checks the Task's branch against the base recorded in its first checkpoint ([ADR 0223](0223-dispatched-sessions-work-in-a-worktree-outside-the-project-and-never-push-or-merge.md)) and against the acceptance criteria. Then:

- **Pass.** It moves the Task to **Done**, with a comment to the person naming the branch ready to merge.
- **Fail.** It moves the Task back to **Ready**, with its findings as a comment for whoever takes it next. The next worker continues on the same branch.
- **Second failed review.** The Task is assigned to the person with a comment, and the Dispatcher no longer takes it.

Interactive sessions keep reviewing only when asked.

## Rejected alternatives

- **The same client reviews its own work.** The owner accepted a review by another client where one is available (2026-11-27). The same client stays the fallback, so a device with one client still gets reviews.
- **Leaving Done to the person.** The person keeps the step that matters: merging ([ADR 0223](0223-dispatched-sessions-work-in-a-worktree-outside-the-project-and-never-push-or-merge.md)). Done after a passing review says the work met its criteria, not that it shipped.
- **Unlimited review rounds.** Two sessions disagreeing could cycle a Task between Ready and Review until the daily limit; two failed reviews hand it to the person.
- **Reviewing only Dispatched work.** The owner asked for "tasks that need review", without a distinction by who did the work.

## Consequences

- Tasks can reach Done without the person having read the change; the branch still waits for them to merge.
- A review costs a session of the device's usage, and a cross-client review needs both clients set up.
- An interactive session's work, moved to Review, is reviewed by a Dispatched session when the Dispatcher is on.

## Invalidation conditions

- If the person reopens Tasks that Dispatched reviews passed, more often than once in five, require the person's approval for Done.
- If a client's reviews fail work that the other client's reviews pass, consistently on the same Tasks, revisit the cross-client rule.

## Validation

Not implemented. The claim of a Task in Review and the Done rule were read in the code cited in Context on 2026-11-27.
