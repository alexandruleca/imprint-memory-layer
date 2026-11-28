---
title: ADR 0227 — Dispatched work never feeds the Dispatcher until the person moves it
description: The Dispatcher neither works on nor reviews a Task that a Dispatched session created, or took out of Backlog by any path, until the person moves that Task; a Task a review sends back, or one claimed and released back to Backlog, was not fed.
---

- Status: Accepted
- Date: 2026-11-28
- Amends: [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) — its "Its own Tasks": which Tasks dispatched work has fed, and that the guard covers reviews as well as work
- Related: [ADR 0221](0221-dispatched-sessions-review-tasks-in-review-with-another-client.md) (reviews), [ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md) (statuses and moves), [ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md) (claims and releases)

## Context

[ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) decided that the Dispatcher never takes a Task that a Dispatched session created, or moved to Ready from Backlog, so that dispatched work cannot feed itself. Its code looked only at the event that made the Task Ready from Backlog: a creation in Ready, or a move ([`guards.py`](../../imprint/dispatch/guards.py), `_readying`). A review on 2026-11-28 found two paths around it:

- **Claim, then release.** A Dispatched session creates a Task with criteria, which goes into Backlog. It claims the Task, which makes it In progress, then releases it. A release of a Task with criteria puts it in Ready ([`claims.py`](../../imprint/tasks/claims.py), `release`), and releases were not looked at.
- **Straight to Review.** A Dispatched session moves a Backlog Task to Review. The Dispatcher then reviewed it, and after a review sent it back, worked on it.

On the Board, any agent may move a Task to any status ([`changes.py`](../../imprint/tasks/changes.py), `move`). Refinement sessions, decided with the owner on 2026-11-28, claim Backlog Tasks and leave them in Backlog (Feature T-7ytct).

## Decision

**Fed by dispatched work.** A Task counts as fed by dispatched work when either of these holds, and the person has not moved it since:

- a Dispatched session created it;
- a Dispatched session took it out of Backlog, by a move to any other status, or by a claim that ended in another status: a release, or a move to Review, Done or Cancelled. Imprint's own release of a Dispatched session's claim counts as that session's.

**Not fed.** None of these make a Task fed:

- a Task a review sends back to Ready: the work on it began in Ready;
- a claim released back to Backlog, as a refinement's is;
- moves by an interactive agent or by the person.

**The guard.** The Dispatcher neither works on nor reviews a fed Task. The person moving it, to any status, ends that.

**Dispatched sessions** are recognised as before: the Dispatcher's `dispatch:<run>` claim, and each claim that took one over with its token ([ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md)).

## Rejected alternatives

- **Looking only at what made the Task Ready.** That was the first version. It missed releases, and it never covered reviews.
- **Refusing Dispatched sessions' moves on the Board.** Workers must move their own Task to Review, and reviewers move theirs to Done or Ready. Telling a Dispatched session's Task from any other would put the Dispatcher's rules into the Board.
- **Never dispatching a Task a Dispatched session ever touched.** Every Task a worker finishes passes through Dispatched hands, so a returned review could never be worked on again.

## Consequences

- Follow-ups that Dispatched sessions find wait for the person, however they reach Ready or Review.
- Refinement can claim Backlog Tasks without making them eligible for work.
- The guard reads each candidate's history on every pass. That is the cost of one more scan of events the pass already reads.

## Invalidation conditions

- If the owner wants dispatched follow-ups worked without a look, a new record replaces the guard with a limit on the depth of follow-ups.
- If the Board gains a separate claim for refinement that leaves a Task's status alone, drop the exception for claims released back to Backlog.

## Validation

- `tests/test_dispatch_guards.py` covers:
  - a Dispatched session's Task claimed and released into Ready, never dispatched until the person moves it;
  - a Backlog Task a Dispatched session put in Review, never reviewed until the person moves it;
  - a claim released back to Backlog feeding nothing;
  - the existing cases: a move to Ready, a returned review, and Backlog creation.
- Linux (WSL Ubuntu 26.04, Python 3.12 venv): the `test_dispatch_*` modules pass.
