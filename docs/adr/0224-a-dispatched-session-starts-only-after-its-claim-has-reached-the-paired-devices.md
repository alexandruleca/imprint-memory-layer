---
title: ADR 0224 — A Dispatched session starts only after its claim has reached the paired devices
description: Before starting a client, the Dispatcher has sync exchange its claim at once and waits until an exchange begun after the claim has completed with every paired device seen recently; while the session runs it checks the claim, and stops the session itself the moment the claim is lost.
---

- Status: Accepted
- Date: 2026-11-27
- Amends: [ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md) — its "Concurrent claims": a Dispatched session's claim must reach the paired devices before its client starts, and the Dispatcher stops a session whose claim an earlier one beat
- Related: [ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md) (the order that decides between claims), [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) (the Dispatcher), [ADR 0223](0223-dispatched-sessions-work-in-a-worktree-outside-the-project-and-never-push-or-merge.md) (worktrees)

## Context

The owner asked (2026-11-27) that two agents never work on the same Task, and that when two are started on separate devices one of them "needs to terminate by itself to avoid extra usage".

- Of two claims on one Task, the earlier in the log's order holds on every device. The later one is recorded as a lost claim ([`state.py`](../../imprint/tasks/state.py), `_claimed`). Its agent learns this on its next Imprint call ([`tasks_tool.py`](../../imprint/tasks_tool.py), `_pending`). An agent that is editing files makes no Imprint call, so it keeps working.
- Each pair of devices exchanges about 60 seconds after its previous exchange ended. A Board change does not trigger an exchange. Only a change to a pair's state makes one run at once ([`sync_pairs.py`](../../imprint/sync_pairs.py), `watch`).
- A pair's record keeps `last_seen`, `last_checked` and `last_sync`, but not when an exchange began, and no mark of which events a peer has received.
- The sync worker runs in the dashboard server, the same process as the Dispatcher ([`api.py`](../../imprint/api.py), startup).
- [ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md) accepted optimistic claims for interactive sessions. Its invalidation condition proposes "a claim check through the relay before a claim is confirmed".

## Decision

**Before starting.** After claiming a Task, the Dispatcher asks the sync worker to exchange with every paired device at once. It waits until an exchange that began after the claim has completed with every enabled pair seen in the last 10 minutes, for at most 2 minutes. Then it reads the Task again:

- If its claim holds, it starts the client.
- If an earlier claim won, nothing starts, and the result reads "lost to" the winner.

A pair not seen in the last 10 minutes is not waited for.

**While the session runs.** After every exchange, and at least once a minute, the Dispatcher checks that the Task's claim is still its own, or the one its session took over with the token. When the claim is lost, the Dispatcher itself ends the session's process group (SIGTERM, then SIGKILL after 3 seconds), removes the worktree with its commits kept on the branch ([ADR 0223](0223-dispatched-sessions-work-in-a-worktree-outside-the-project-and-never-push-or-merge.md)), and comments on the Task. It does not wait for the agent to read the notice.

**For sync.** The worker gains a request to exchange now. Each pair's record keeps when its last completed exchange began.

**Unchanged.** Interactive sessions keep optimistic claims as [ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md) decided.

## Rejected alternatives

- **A lock through the relay.** It needs both devices connected at the moment of claiming, and [ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md) rejected it for that reason.
- **Relying on the agent to read the lost-claim notice.** An agent editing files makes no Imprint call, and goes on spending usage until its next one.
- **Waiting for every paired device.** A device that is off would hold up all dispatching.

## Consequences

- A Dispatched session starts up to about 2 minutes after its claim; an offline device costs nothing more than the wait.
- When two devices dispatch the same Task in the same minute, exactly one starts its client. When a device that was not seen for 10 minutes comes back with an earlier claim, the later session is stopped as soon as the exchange arrives, with at most that much work wasted.
- Sync gains an exchange-now request, which the Dispatcher uses after each claim.

## Invalidation conditions

- If lost claims still stop running Dispatched sessions more than once a week on the owner's Boards, add a relay round-trip check that the peers acknowledge.
- If sync comes to mark which events each peer has received, wait for that mark instead of an exchange that began after the claim.

## Validation

- Linux (WSL Ubuntu 26.04, Python 3.12 venv): the full suite, 1,691 tests, passed on 2026-11-27 with the Dispatcher's Tasks together.
- Windows 11 (the installed runtime's Python 3.13, from a native copy of the repository): the `test_dispatch_*` modules (121 tests), `test_tasks*`, `test_cli_tasks`, `test_sync_pairs`, `test_builtin_skills`, `test_config_help`, `test_adr_records` and `test_docs_tables` passed.
- `tests/test_dispatch_claims.py`: the exchange-now request and its expiry, no wait without recently seen peers, the client starting after the peer's exchange, nothing started when an earlier claim won, the wait limit, and a running session stopped by the Dispatcher when another device's earlier claim arrives. `tests/test_sync_pairs.py`: the start of a completed exchange recorded through a real relay, for a transfer and for an idle exchange.
- Not run: two real devices dispatching the same Task.
