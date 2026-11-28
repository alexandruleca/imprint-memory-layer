---
title: ADR 0229 — Dispatched sessions cannot switch the Dispatcher on or start more sessions
description: The CLI refuses to switch the Dispatcher on or start a Dispatched session from inside one, a session started from the CLI keeps the daily and concurrency limits, and the boundary is stated as it is - no agent tool, but the loopback dashboard API is open to local processes like the rest of the dashboard.
---

- Status: Accepted
- Date: 2026-11-28
- Amends: [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) — its "The switch": what keeps agents from switching the Dispatcher on or starting sessions, and its claim that no agent can start more agents
- Related: [ADR 0225](0225-a-running-dispatched-session-stops-when-it-no-longer-holds-its-claim.md) (stopping sessions), [ADR 0228](0228-a-dispatched-session-that-fails-to-start-ends-the-pass-and-counts-toward-switching-off.md) (failed starts)

## Context

[ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) says only the person switches the Dispatcher, and that "Agents get no tool for it, so no agent can start more agents". A review on 2026-11-28 found that this overstates the boundary:

- **`dispatch run` skipped the limits.** `imprint tasks dispatch run` started a session at once, ignoring the switch, `dispatch.daily_limit` and `dispatch.concurrency` ([`cli_tasks.py`](../../imprint/cli_tasks.py)).
- **A Dispatched session could run the CLI.** Such a session has a shell. Its environment carries `IMPRINT_DISPATCH_RUN`, set by the Dispatcher ([`dispatched.py`](../../imprint/dispatch/dispatched.py), `_env`), and nothing checked it, so the session could run `imprint tasks dispatch on` or `run` itself.
- **The dashboard API has no token.** It accepts only loopback connections and local origins ([`api.py`](../../imprint/api.py), `reject_remote_origin`). Any process on the computer can call `PUT /api/tasks/dispatch`, as it can call every other dashboard action.

## Decision

**What the CLI refuses.** Inside a Dispatched session, `imprint tasks dispatch on` and `imprint tasks dispatch run` refuse, and say why. `off` and `stop` work everywhere.

**The limits of `dispatch run`.** `dispatch run` is the person's one-off start. It works whether the Dispatcher is switched on or not, but it refuses past `dispatch.daily_limit` or when `dispatch.concurrency` sessions already run.

**The boundary, as it is.**

- Agents get no MCP tool for the switch or for starting sessions.
- Dispatched sessions are refused by the CLI.
- An interactive agent in the person's own session can run the CLI as the person can, under the person's own permission prompts.
- The loopback dashboard API is reachable by local processes, like the rest of the dashboard.

This record does not add a token to the dashboard API.

## Rejected alternatives

- **A token for the dashboard API.** Every caller would have to carry it: the desktop app, the browser dashboard, the CLI's queue calls and the extension. That is a decision about the whole API, not about the Dispatcher.
- **Refusing every agent from the CLI by its client's markers** (`CLAUDECODE`, Codex's variables). The markers differ between clients and versions. An agent may run the command on the person's request, and a Dispatched session's own marker is the one the Dispatcher sets.
- **Requiring the switch for `dispatch run`.** The person running it asks for that Task now. The limits still bound the spending.

## Consequences

- A Dispatched session cannot widen the Dispatcher through the CLI. A process that calls the dashboard API directly still can, as it can change anything else the dashboard offers.
- `dispatch run` reports the limit that stopped it instead of starting a session.
- The documentation no longer claims that no agent can start more agents.

## Invalidation conditions

- If the dashboard API gains authentication, require it for `PUT /api/tasks/dispatch` and say so here.
- If a Dispatched session is seen switching the Dispatcher on through the API, give the switch a confirmation only the person can give, such as from the desktop app or the dashboard with a fresh page token.

## Validation

- `tests/test_dispatch_loop.py`, `CommandLineTests`, covers:
  - `dispatch on` and `run` refused with `IMPRINT_DISPATCH_RUN` set, while `off` works;
  - `run` refused at the daily limit and at the concurrency limit.
- Linux (WSL Ubuntu 26.04, Python 3.12 venv): the `test_dispatch_*` modules pass.
