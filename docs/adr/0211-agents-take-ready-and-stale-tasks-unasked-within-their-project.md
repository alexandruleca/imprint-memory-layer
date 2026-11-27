---
title: ADR 0211 — Agents take Ready and Stale Tasks unasked, within their project
description: Besides taking Tasks when asked, an agent may claim one Ready or Stale Task on its own once it has finished what it was asked, if the Task is in its current project, unblocked, and not assigned to the user or to another client or device; a workspace setting turns this down, and Imprint does not start agents itself.
---

- Status: Accepted
- Date: 2026-11-27
- Related: [ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md) (the Board), [ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md) (claims and staleness), [ADR 0212](0212-agents-talk-through-comments-on-tasks.md) (what agents are told at session start)

## Context

The owner asked that agents be "able to pick them up to finish them" when Tasks are stale or stalling (2026-11-27). Claude recommended that agents take Tasks only when asked, so that a session in another project could not take work it was not meant to have; the owner chose that agents take Tasks "on their own as well as when asked".

- Coding clients run an agent only while a session is open: it acts on the user's prompt, a `/loop`, or a scheduled task.
- A session's directory and workspace are recorded ([`agent_activity.py`](../../imprint/agent_activity.py)), and its project follows from them ([ADR 0072](0072-projects-name-their-workspace-and-each-device-approves-it.md)).

## Decision

**When asked.** An agent takes any Task the user points it at, except one assigned to the user, which agents only comment on; the user changes its assignees to hand it to an agent.

**Unasked.** An agent may claim a Task on its own after it has finished what it was asked in the session, or during a run the user started to work the Board (such as a `/loop`), when the Task:

- is Ready or Stale (never Backlog);
- belongs to the agent's current project;
- has no blocking Task that is not Done;
- is not assigned to the user;
- has no assignees, or is assigned to the agent's client, its device, or its client on its device.

It holds one Task at a time, takes the highest priority first, then the top position, then the oldest, and says in its reply and in a comment on the Task that it took it. Reviewing a Task in Review is never taken unasked ([ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md)).

**Creating Tasks unasked.** An agent creates a Task on its own only for work it cannot finish in the session: what is left over, a follow-up, a defect found outside its current scope. Such Tasks go to Backlog. Ready Tasks come from the user, or from a planning skill the user runs, such as `to-tickets`. A holder records a checkpoint at each meaningful step and at least every 30 minutes, well inside the 60 minutes after which its Task is Stalling ([ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md)).

**Assignees.** A Task's assignees ([ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md)) limit only what agents take unasked: a matching agent takes it unasked, and any agent takes it when asked, unless it is assigned to the user.

**Setting.** `tasks.autonomy`, per workspace: `off` (only when asked), `stale` (Stale Tasks only), or `ready` (Ready and Stale Tasks; the default).

**Imprint does not start agents.** Only sessions that are already open take Tasks. Imprint does not launch headless agents (`claude -p`, `codex exec`) for Tasks that nobody is working on.

## Rejected alternatives

- **Only when asked.** Claude's recommendation; the owner chose otherwise (2026-11-27). The project match, the Ready requirement and the setting answer the risk it addressed.
- **Imprint launches a headless agent when a Task goes Stale.** That runs code unattended, with permission and security questions of its own; deferred to a record of its own.
- **Taking Backlog Tasks unasked.** Backlog holds work that is captured but not yet described well enough to start.
- **Agents turning their own plans into Tasks.** Weighed in the design discussion; every multi-step request would fill the Board with Tasks that are finished within the session.

## Consequences

- A session may go on working after it finishes its request, at the user's token cost, until the setting is lowered.
- A Task becomes available to agents unasked only when the user or an agent moves it to Ready, which makes Ready a deliberate signal.
- Work in one project is not taken by sessions in another, even in the same workspace.

## Invalidation conditions

- If the owner cancels or reverts work that agents took unasked more often than they keep it, change the default to `stale` or `off`.
- If Imprint gains a safe, approved way to start agents, a new record decides whether Stale Tasks start them.

## Validation

- `tests/test_tasks.py`: `next_task` offering only Ready or Stale, unblocked, unclaimed Tasks of the agent's project and for it; assignee matching by client, device or both; priority, position and age; autonomy per workspace; a Feature's frontier; the handoff of a Stale Task.
- `tests/test_tasks_tool.py` and `tests/test_cli_tasks.py`: `next`, `handoff` and `autonomy` through the tools and the CLI.
- Linux (WSL Ubuntu, Python 3.12 venv): the full suite, 1,522 tests, passed on 2026-11-27 with the Board and built-in skills together.
- Windows 11 (the installed runtime's Python 3.13, from a native copy of the repository): `tests.test_tasks`, `test_tasks_tool`, `test_cli_tasks`, `test_hooks`, `test_agent_activity`, `test_config_help`, `test_builtin_skills`, `test_registry`, `test_registry_files`, `test_skills_tool`, `test_chat_tool_coverage`, `test_adr_records` and `test_docs_tables`, 187 tests, passed.
- Not run: macOS.
