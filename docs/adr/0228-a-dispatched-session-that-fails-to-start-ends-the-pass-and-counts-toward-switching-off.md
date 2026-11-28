---
title: ADR 0228 — A Dispatched session that fails to start ends the pass and counts toward switching the Dispatcher off
description: When a claimed Task's session cannot start, the Dispatcher records the failure, ends its pass, and counts it toward dispatch.failure_limit, but not toward the two failures that hand a Task to the person.
---

- Status: Accepted
- Date: 2026-11-28
- Amends: [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) — its "Failures", for sessions that fail before their client starts
- Related: [ADR 0222](0222-only-clients-with-a-sandbox-or-a-check-of-each-action-are-dispatched.md) (runners and clients), [ADR 0225](0225-a-running-dispatched-session-stops-when-it-no-longer-holds-its-claim.md) (outcomes of a running session)

## Context

[ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) counts failures of sessions that ran. A review on 2026-11-28 showed how sessions that failed while starting behaved:

- **What failed.** After the Dispatcher claimed a Task, starting its session could still fail: the `imprint-dispatch` skill unreadable, the client's process not starting, the lease not renewed. Each failure released the claim and recorded a failed run ([`dispatched.py`](../../imprint/dispatch/dispatched.py), `start`).
- **What happened next.** The pass went on to the next Task, so one pass could fail every candidate and pass `dispatch.daily_limit`. The check for failures in a row ran only after a running session ended ([`loop.py`](../../imprint/dispatch/loop.py), `watch`), so the Dispatcher never switched itself off. The second pass then handed every Ready Task to the person as "failed twice".

A reproduction with an unreadable skill gave 5 failed runs in one pass, 5 failures in a row, and the Dispatcher still on.

## Decision

**What ends the pass.** When a session fails to start after its Task was claimed, the Dispatcher:

- records the run as `failed`, releases the claim and comments on the Task, as before;
- kills the client if it had already started;
- ends its pass, so at most one start fails per pass.

**Switching off.** A failed start counts toward `dispatch.failure_limit` like any failure. At the limit, the Dispatcher switches itself off and tells the person.

**The Task's own count.** A run whose client never started is not counted toward the two failures that hand a Task to the person. Such a failure is the device's problem, not the Task's.

## Rejected alternatives

- **Going on to the next Task after a failed start.** That was the first version. It turned one broken setup into a failure on every candidate.
- **Not counting failed starts at all.** A setup that cannot start any session would then retry every pass until the daily limit, failing one Task each time.
- **Counting failed starts against the Task.** Every Task the Dispatcher tried while the setup was broken would reach the person.

## Consequences

- A broken setup, such as a damaged skill or a client that will not launch, costs at most `dispatch.failure_limit` failed starts, one a pass, before the Dispatcher switches itself off.
- Tasks tried during that time stay available to agents and the Dispatcher once the setup works.

## Invalidation conditions

- If failed starts turn out to come from the Task, for example a project whose folder breaks only some Tasks' worktrees, count them toward the Task again.

## Validation

- `tests/test_dispatch_guards.py`, `FailedStartTests`, covers:
  - a failed start ending the pass with one failed run;
  - five passes with an unreadable skill stopping after three failed starts, with the Dispatcher switched off, the person told, and no Task handed to the person.
- Linux (WSL Ubuntu 26.04, Python 3.12 venv): the `test_dispatch_*` modules pass.
