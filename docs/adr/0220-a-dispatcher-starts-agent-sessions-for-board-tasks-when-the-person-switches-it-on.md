---
title: ADR 0220 — A Dispatcher starts agent sessions for Board Tasks when the person switches it on
description: Each device's dashboard server runs a Dispatcher, plain code that starts one-shot agent sessions for open Tasks and Tasks waiting for Review by the Board's own rules, switched on per device and workspace by the person only, off by default, within limits on sessions, time and failures.
---

- Status: Amended
- Date: 2026-11-27
- Amends: [ADR 0211](0211-agents-take-ready-and-stale-tasks-unasked-within-their-project.md) — its "Imprint does not start agents": a Dispatcher the person switches on starts agent sessions for Tasks
- Amended by:
  - [ADR 0227](0227-dispatched-work-never-feeds-the-dispatcher-until-the-person-moves-it.md) — its "Its own Tasks": which Tasks dispatched work has fed, and that the guard covers reviews as well as work
- Related: [ADR 0219](0219-agents-take-only-standalone-tasks-unasked.md) (only standalone Tasks), [ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md) (claims and their tokens), [ADR 0213](0213-built-in-skills-ship-with-each-release-and-a-fork-replaces-them.md) (built-in skills and forks), [ADR 0221](0221-dispatched-sessions-review-tasks-in-review-with-another-client.md) (reviews), [ADR 0222](0222-only-clients-with-a-sandbox-or-a-check-of-each-action-are-dispatched.md) (which clients), [ADR 0223](0223-dispatched-sessions-work-in-a-worktree-outside-the-project-and-never-push-or-merge.md) (where the work happens), [ADR 0224](0224-a-dispatched-session-starts-only-after-its-claim-has-reached-the-paired-devices.md) (claims across devices)

## Context

The owner asked (2026-11-27) for "a loop that can dispatch codex or claude-code agents to pick up open tasks or tasks that need review", switched on and off from the Board and off by default. Two agents must never work on the same Task. If two devices start one, one of them must stop by itself to avoid spending usage twice. Imprint should know which clients are available on the device.

- [ADR 0211](0211-agents-take-ready-and-stale-tasks-unasked-within-their-project.md) decided that only sessions already open take Tasks. It deferred launching headless agents to a record of its own, and its invalidation condition anticipates this one.
- Today, only an open session works the Board: one the person prompts, or a `/loop` the person started. When no session is open, Ready Tasks wait.
- `tasks(action="next")` already encodes which Tasks an agent may take unasked ([`work.py`](../../imprint/tasks/work.py), `next_task`): Ready or Stale, standalone, unblocked, not assigned to the user, assignment matching, ordered by priority, position and age, limited by `tasks.autonomy`.
- The dashboard server already hosts the device's long-running loops: the queue's dispatcher, the sync worker and the Reminders scheduler all start with it ([`api.py`](../../imprint/api.py), startup). It runs while the desktop app runs (hidden counts) or after `imprint ui start`. `imprint ui` in the foreground stops 15 seconds after the last browser tab closes. Nothing starts it at login.
- A claim token already lets a session take over a claim another session made ([`claims.py`](../../imprint/tasks/claims.py), `claim`). A Task in Review can be claimed without leaving Review.
- Nothing maps a Task's project to a folder. `project_roots` in `data/workspace-policy.sqlite3` maps project names to folders on this device ([`workspace_policy.py`](../../imprint/workspace_policy.py), `project_root`); ingest and `imprint workspace map` write it. `agent_activity` records each agent session's folder ([`agent_activity.py`](../../imprint/agent_activity.py)).
- Claude Code 2.1.332 (`claude -p`) and Codex 0.171 (`codex exec`) run one prompt to completion without a person (their `--help`, 2026-11-27).

## Decision

**The Dispatcher.** Each device's dashboard server runs a **Dispatcher**. It is plain code with no model: it picks Tasks by the rules below, starts **Dispatched sessions** (one-shot runs of a client, [ADR 0222](0222-only-clients-with-a-sandbox-or-a-check-of-each-action-are-dispatched.md)) for them, and watches them until they end. It dispatches only while the dashboard server runs. A server started with auto-shutdown waits for its running Dispatched sessions before it stops.

**The switch.** The Dispatcher is switched on per device and per workspace, and is **off by default**. The switch is device-local and never synced; each device's state travels with the Board only so the Board can show which paired devices dispatch. Only the person switches it: on the Board, in both the simple and the advanced dashboard, or with `imprint tasks dispatch on|off|status|stop <session>`. Agents get no tool for it, so no agent can start more agents. One Dispatcher serves every workspace switched on on its device, whichever workspace the dashboard shows.

**What it takes, in order.**

1. Tasks in **Review**, for a review ([ADR 0221](0221-dispatched-sessions-review-tasks-in-review-with-another-client.md)).
2. **Stale** Tasks, taken over from their Handoff.
3. **Ready** Tasks.

Within each group it follows the Board's order: priority, position, then age. A Task qualifies under the same rules as `tasks(action="next")`. It must be standalone, not blocked, and not assigned to the user. It must be unassigned, or assigned to a client or device that matches. `tasks.autonomy` applies: `off` dispatches nothing, and `stale` dispatches no Ready Task. Beyond those rules:

- **Projects.** The Dispatcher works across every project that has a folder on this device. The folder comes from `project_roots`, else from the folder of the most recent agent session in that project, and it must be a git repository. A project without a folder is skipped, and the Board offers to set one.
- **Origin.** Tasks created on any paired device are taken.
- **Its own Tasks.** It never takes a Task that a Dispatched session created, or moved to Ready from Backlog. Tasks that Dispatched sessions create go into Backlog, so dispatched work cannot feed itself. A Task a review sends back to Ready is taken again ([ADR 0221](0221-dispatched-sessions-review-tasks-in-review-with-another-client.md)), and Tasks created by the person, or by an agent working with the person, are taken as usual. A Dispatched session is recognised from the Board's history: the Dispatcher's claim, made as `dispatch:<run>`, and each claim that took it over with its token.

**How a session gets its Task.** The Dispatcher claims the Task itself, confirming the claim across devices ([ADR 0224](0224-a-dispatched-session-starts-only-after-its-claim-has-reached-the-paired-devices.md)), then starts the client with the child's pid on the lease. A crashed session therefore makes the Task Stale at once. The prompt hands the session the claim token, and the session takes the claim over on its first Imprint call. The prompt also carries:

- the text of a new built-in skill, `imprint-dispatch`, which holds the worker's and the reviewer's instructions and which a fork replaces ([ADR 0213](0213-built-in-skills-ship-with-each-release-and-a-fork-replaces-them.md));
- the Task id;
- the session's role (work or review);
- its worktree ([ADR 0223](0223-dispatched-sessions-work-in-a-worktree-outside-the-project-and-never-push-or-merge.md)).

**Limits.** Each is a setting.

| Setting | Default | Meaning |
| --- | --- | --- |
| `dispatch.concurrency` | 1 | Dispatched sessions running at once on the device, across workspaces |
| `dispatch.session_minutes` | 60 | Time limit of one session |
| `dispatch.daily_limit` | 10 | Sessions started a day |
| `dispatch.failure_limit` | 3 | Failures in a row after which the Dispatcher switches itself off |

**Failures.** A worker fails when it ends (exit, crash or time limit) without moving its Task to Review; a session the person or the server's shutdown stopped is *stopped*, not failed. A reviewer fails when it ends without moving its Task to Done or Ready. A failure releases the claim, with a comment holding the end of the session's log. A Task whose Dispatched sessions fail twice is assigned to the user with a comment.

**Stopping.** Switching the Dispatcher off stops new sessions; running ones finish. Each running session has a Stop action that ends its process group (SIGTERM, then SIGKILL after 3 seconds), releases the claim and comments on the Task.

**Seeing it.** The Board shows a Dispatcher strip:

- the switch;
- the clients available on the device and the default client ([ADR 0222](0222-only-clients-with-a-sandbox-or-a-check-of-each-action-are-dispatched.md));
- the running sessions with their Stop actions;
- recent results (Task, client, outcome, time).

Each session's output is kept under `data/dispatch/`. Its conversation is learned like any other, so a later Handoff links to it. The person is notified (toast and desktop notification) only when the Dispatcher switches itself off or hands a Task to them; Tasks reaching Review already notify.

## Rejected alternatives

- **An agent as the loop**, a long-running session told to work the Board. That already exists as a `/loop` the person starts ([ADR 0211](0211-agents-take-ready-and-stale-tasks-unasked-within-their-project.md)). It keeps a conversation and its tokens running while nothing is ready, and it ends with its client. The owner accepted plain code (2026-11-27).
- **A separate Dispatcher daemon.** It would be one more process to start, stop and keep alive. The dashboard server already hosts the device's loops, including the sync worker that the claim check needs ([ADR 0224](0224-a-dispatched-session-starts-only-after-its-claim-has-reached-the-paired-devices.md)).
- **A switch synced across devices.** One click would start agents on every paired device, with their own clients and usage.
- **Agents switching the Dispatcher on through an MCP tool.** An agent could then start more agents.
- **Dispatching a Feature's Tasks.** [ADR 0219](0219-agents-take-only-standalone-tasks-unasked.md) keeps a Feature's order the person's call, and the Dispatcher follows it in its first version.
- **Taking Tasks that Dispatched sessions put in Ready from Backlog.** Dispatched work would feed itself until the daily limit stopped it.

## Consequences

- Agents work while nobody has a session open, at the person's cost in their clients' usage. The limits and the default-off switch bound it.
- Nothing is dispatched while the dashboard server is down, or in a project without a folder on the device.
- A Task written on one device can start an agent on another. That agent runs under its client's sandbox or check ([ADR 0222](0222-only-clients-with-a-sandbox-or-a-check-of-each-action-are-dispatched.md)), in a worktree, and never pushes ([ADR 0223](0223-dispatched-sessions-work-in-a-worktree-outside-the-project-and-never-push-or-merge.md)).
- New: the `dispatch.*` settings, the built-in skill `imprint-dispatch`, the Board's strip, the `imprint tasks dispatch` command and logs under `data/dispatch/`.
- Docs to update once accepted: [the Board](../tasks.md), [the dashboard](../dashboard.md), [configuration](../configuration.md), the glossary's Dispatcher and Dispatched session.

## Invalidation conditions

- If the owner cancels or reverts dispatched work more often than they keep it, lower the defaults or change what the Dispatcher takes.
- If the dashboard server comes to run at login or as a service, revisit "only while the dashboard server runs".
- If a client offers a supported local API for handing it work in the background, prefer it to one-shot processes for that client.
- If Feature work is regularly left undone while the Dispatcher is idle, a new record decides whether it works a Feature's frontier the person picked.

## Validation

- Linux (WSL Ubuntu 26.04, Python 3.12 venv): the full suite, 1,691 tests, passed on 2026-11-27 with the Dispatcher's Tasks together.
- Windows 11 (the installed runtime's Python 3.13, from a native copy of the repository): the `test_dispatch_*` modules (121 tests), `test_tasks*`, `test_cli_tasks`, `test_sync_pairs`, `test_builtin_skills`, `test_config_help`, `test_adr_records` and `test_docs_tables` passed.
- `tests/test_dispatch_run.py`, `test_dispatch_loop.py`, `test_dispatch_api.py` and `test_dispatch_guards.py`: one Task dispatched end to end with a fake client, the switch off by default and per workspace, the next-rules order, concurrency and time limits, stopping from the CLI and the Board, recovery of unwatched runs, the daily and failure limits, a Task failing twice going to the person, and Dispatched sessions' own Tasks kept out.
- Real runs on Windows 11 against throwaway repositories and workspaces: Claude Code 2.1.302 took a Task from Ready to Review in 35 s; the dashboard's strip was rendered in both dashboards (Playwright, Chromium) against a throwaway server.
- Not run: macOS; the loop running for days on the owner's own Boards.
