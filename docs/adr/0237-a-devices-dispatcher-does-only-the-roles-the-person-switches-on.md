---
title: ADR 0237 — A device's Dispatcher does only the roles the person switches on
description: The Dispatcher's single switch per device and workspace becomes four role switches, Work, Review, Refine and Deploy, each off by default, so a device can, for example, only review and deploy.
---

- Status: Accepted
- Date: 2026-11-28
- Amends:
  - [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) — its "The switch": four role switches per device and workspace in place of one, `on` and `off` kept as shortcuts
  - [ADR 0230](0230-the-dispatcher-refines-backlog-tasks-when-the-person-switches-refinement-on.md) — its switch of its own: refinement becomes the Refine role
- Related: [ADR 0229](0229-dispatched-sessions-cannot-switch-the-dispatcher-on-or-start-more-sessions.md) (only the person switches), [ADR 0239](0239-the-dispatcher-deploys-tasks-a-dispatched-review-passed.md) (the Deploy role), [ADR 0241](0241-a-needs-you-list-shows-what-waits-for-the-person.md) (Review Tasks no device will review)

## Context

The owner asked (2026-11-28): "I should also be able to set a dispatcher on a device to review and deploy only, no other work."

- A device's Dispatcher has one switch per workspace, kept in `data/dispatch.sqlite3` (`switches`, [`store.py`](../../imprint/dispatch/store.py)). On, it takes reviews first and then Stale and Ready Tasks ([`loop.py`](../../imprint/dispatch/loop.py), `Dispatcher.dispatch`). Nothing lets a device review without also working.
- Backlog refinement already has a switch of its own (`refine_switches`, [ADR 0230](0230-the-dispatcher-refines-backlog-tasks-when-the-person-switches-refinement-on.md)). The loop refines in `refining_workspaces()` whether or not the main switch is on, so independent switches already work side by side.
- Paired devices learn whether a device dispatches from a per-device Board setting, `{'on', 'name'}` ([`work.py`](../../imprint/tasks/work.py), `announce_dispatching`). The Board's strip shows them.
- Deploying is a new kind of work ([ADR 0239](0239-the-dispatcher-deploys-tasks-a-dispatched-review-passed.md)) that a device should be able to do without working on Tasks.

## Decision

**Four roles.** Per device and workspace, the Dispatcher has four switches, each **off by default**:

| Role | What it takes |
| --- | --- |
| Work | Stale and Ready Tasks, by the rules of `next` ([ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md)) |
| Review | Tasks in Review ([ADR 0221](0221-dispatched-sessions-review-tasks-in-review-with-another-client.md)) |
| Refine | Backlog Tasks without acceptance criteria ([ADR 0230](0230-the-dispatcher-refines-backlog-tasks-when-the-person-switches-refinement-on.md)) |
| Deploy | Tasks a Dispatched review passed ([ADR 0239](0239-the-dispatcher-deploys-tasks-a-dispatched-review-passed.md)) |

A device's Dispatcher is on for a workspace when any role is on. Within a pass, the order stays: reviews, then work, then refinement. Deploying starts no client session and is described in its own record.

**Who switches them.** Only the person: a checkbox per role on the Board's strip, in both dashboards, or `imprint tasks dispatch roles <work,review,refine,deploy|none>`. The existing commands stay as shortcuts: `on` is Work and Review, `off` is every role off, and `refine on|off` is Refine. The CLI refuses Dispatched sessions as before ([ADR 0229](0229-dispatched-sessions-cannot-switch-the-dispatcher-on-or-start-more-sessions.md)).

**Migration.** On first read, a switch that was on becomes Work and Review, and a refinement switch that was on becomes Refine.

**What paired devices see.** The per-device Board setting carries the device's roles beside `on`. `on` stays true while any role is on, so older releases, which read only `on`, still show the device as dispatching.

**Switching off.** A run waiting for its claim to travel is called off when its own role is switched off. Reaching `dispatch.failure_limit` switches every role off, and Imprint's comment says so.

## Rejected alternatives

- **A main switch above the roles.** Two controls for one intent. The loop already runs refinement without the main switch (`refining_workspaces()` is read on its own), so roles work side by side without a master.
- **Roles as a setting in `config.json`.** Settings are per device but not per workspace, while the switches are per workspace ([ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md)). `imprint config set` is also open to any local process; only `imprint tasks dispatch` refuses Dispatched sessions (ADR 0229).

## Consequences

- A device can review and deploy without starting work sessions, and a busy laptop can leave reviews to another device.
- The Board's strip grows from one switch and a refinement switch to four checkboxes, and shows paired devices' roles.
- `status`, the dashboard's Dispatcher API and `tasks.dispatching()` report roles. Callers that read only `on` keep working.
- Needs you ([ADR 0241](0241-a-needs-you-list-shows-what-waits-for-the-person.md)) can tell whether any paired device will review a Task in Review.

## Invalidation conditions

- If the person wants roles per project rather than per workspace, roles move to (workspace, project) and this record is amended.
- If a role needs more than on and off (for example a client of its own per role), its settings move beside the role, and this record is amended.
- If no paired device runs a release that reads only `on`, the `on` field may be dropped from the Board setting.

## Validation

Work, Review and Refine (T-dcc2s), on Linux and natively on Windows (208 `test_dispatch_*` tests on each, one skipped by design on Windows):
- `tests/test_dispatch_roles.py` (18 tests): roles off by default and set exactly, unknown roles refused, the old switches as shortcuts, the migration of an earlier release's switches on first read, the old tables kept in step, a pass with Review only, Work only and Refine only, a waiting run called off when its own role goes, the roles paired devices see (and an earlier release's announcement read as Work and Review), the failure limit switching every role off, the `roles` command and its refusal inside a Dispatched session, and the API;
- the other `test_dispatch_*` modules, two of them updated to the Refine role.

In a browser (Playwright, a throwaway dashboard server on scratch data), in the simple and the advanced dashboard: ticking Review switched exactly that role on and the strip said so; Review and Refine Backlog together likewise; at a phone's width the strip wraps without a horizontal scroll. The Deploy role comes with T-k9gjm.
