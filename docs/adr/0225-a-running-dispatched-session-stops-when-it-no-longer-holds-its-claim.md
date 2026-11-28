---
title: ADR 0225 — A running Dispatched session stops when it no longer holds its claim, and who ended the claim decides the outcome
description: While a Dispatched session runs, the Dispatcher stops it as soon as its run no longer holds the Task's claim, recording a claim another session took as lost, the session's own release as failed, and the person's or Imprint's release, cancel or Done as stopped; at most one session keeps running for a Task.
---

- Status: Accepted
- Date: 2026-11-28
- Amends: [ADR 0224](0224-a-dispatched-session-starts-only-after-its-claim-has-reached-the-paired-devices.md) — its "While the session runs", for claims that end without another claim winning, and its consequence that exactly one of two devices starts its client
- Related: [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) (failures and stops), [ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md) (claims, tokens and staleness), [ADR 0223](0223-dispatched-sessions-work-in-a-worktree-outside-the-project-and-never-push-or-merge.md) (worktrees and their cleanup)

## Context

A review of the Dispatcher on 2026-11-28 found two gaps in how a running session's claim was checked ([`claimcheck.py`](../../imprint/dispatch/claimcheck.py), `ours`, and [`dispatched.py`](../../imprint/dispatch/dispatched.py), `Session.step`):

- **A released claim counted as lost.** When the worker, a reviewer, the person or Imprint released the claim while the client still ran, the run was stopped and recorded `lost` "to nobody, whose claim was earlier". A lost claim is not a failure, so the guards of [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) never counted it. Its worktree was also removed, losing uncommitted work.
- **A Task the person ended counted as finished.** A Task the person cancelled or moved to Done counted as "nothing to lose", so the session kept running and was recorded `failed` when it ended.

Two further facts bear on this:

- A claim ends by a release, by a move to Review, Done or Cancelled while it is held, or by another session's claim replacing it. A Stale claim can be taken over without its token, and that claim is another agent's ([ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md); [`state.py`](../../imprint/tasks/state.py), `_claimed`, `_released`, `_moved`).
- The order of two claims made on two devices before they sync follows their hybrid logical clocks, not the moment each device saw the other. A device can confirm its claim and start its client, and a claim made later on another device whose clock runs behind can still come first in the merged order ([ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md)). [ADR 0224](0224-a-dispatched-session-starts-only-after-its-claim-has-reached-the-paired-devices.md)'s consequence that "exactly one starts its client" therefore does not hold in every case.

## Decision

**The run's claim.** A run holds the Task's claim while the holder is the Dispatcher's `dispatch:<run>` session or a session that took one of the run's claims over with the same token. A Stale claim of the run still counts as held.

**Checking it while the session runs.** At least every 10 seconds, the Dispatcher finds how the run's claim ended, if it did:

| How the claim ended | The session | Outcome | Worktree |
| --- | --- | --- | --- |
| Another session's claim holds or took the Task | stopped at once | `lost` ([ADR 0224](0224-a-dispatched-session-starts-only-after-its-claim-has-reached-the-paired-devices.md)) | removed, commits kept on the branch |
| The session released it unfinished | stopped at once | `failed`, counted by the guards of [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) | kept |
| The person, Imprint or another agent released it, or moved the Task to Review, Done or Cancelled | stopped at once | `stopped`, not a failure | kept until the Task ends ([ADR 0223](0223-dispatched-sessions-work-in-a-worktree-outside-the-project-and-never-push-or-merge.md)) |
| The session moved the Task to Review, Done or Cancelled | left to end on its own, within its time limit | decided when it ends, as before | kept |

**Before the client starts.** The same check follows the wait of [ADR 0224](0224-a-dispatched-session-starts-only-after-its-claim-has-reached-the-paired-devices.md). A claim another session won starts nothing and is recorded `lost`. A claim the person released, or a Task the person ended during the wait, starts nothing and is recorded `stopped`.

**What holds across devices.** At most one Dispatched session keeps running for a Task. When both devices were seen recently, normally only one starts. If the clocks disagree, both can start, and the one whose claim comes second in the merged order is stopped as soon as the other's claim arrives.

## Rejected alternatives

- **Letting a session whose claim was released run until it ends.** Its agent learns of the release only on its next Imprint call, and goes on spending usage on work nobody holds until then. This is the gap [ADR 0224](0224-a-dispatched-session-starts-only-after-its-claim-has-reached-the-paired-devices.md) closed for lost claims.
- **Recording every ended claim as lost.** This was the first version. It hid a session's own release from the failure guards, and removed the worktree of work that nobody else had taken.
- **Removing the worktree whenever a session is stopped.** A stopped session's uncommitted work would go with it. Cleanup removes the worktree once the Task reaches Done or Cancelled, keeping the branch.

## Consequences

- A session the person stops by releasing, cancelling or finishing its Task costs no failure and leaves its worktree for inspection.
- A session that gives up on its claim counts toward switching the Dispatcher off and toward handing the Task to the person.
- Two devices may both start a session for one Task when their clocks disagree; the second in order is stopped when the first's claim arrives, which wastes at most that much work.

## Invalidation conditions

- If sync comes to confirm claims through a relay round trip before a client starts, so that two devices can no longer both start one, restate the guarantee as exactly one.
- If people stop Dispatched sessions by releasing their claims and expect the worktree gone, remove it on a stop as well.

## Validation

- `tests/test_dispatch_claims.py` covers each case:
  - the session's own release recorded as failed and counted, keeping its worktree;
  - the person's release, cancel and Done recorded as stopped;
  - a lost claim removing the worktree;
  - a takeover without the token recorded as lost;
  - a takeover with the token that lands while the check reads the Board still counted as the run's (the check reads the Task's state before its history);
  - a release while waiting starting nothing.
- `tests/test_dispatch_two_devices.py`: two Dispatchers, each in its own process with its own data dir, home and clone of the project, claim one Task before their Boards sync; after the test's relay exchanges them, exactly one starts its client (the earlier claim) and the other records the claim as lost.
- Linux (WSL Ubuntu 26.04, Python 3.12 venv): the `test_dispatch_*` modules pass.
- Not run: two real devices with clocks set apart.
