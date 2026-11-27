---
title: ADR 0219 — Agents take only standalone Tasks unasked
description: An agent working the Board on its own takes only a Task that is neither a Feature nor one of a Feature's Tasks; a Feature's Tasks are taken when the user asks for that Feature, through its frontier, which keeps their blockers and order.
---

- Status: Accepted
- Date: 2026-11-27
- Amends: [ADR 0211](0211-agents-take-ready-and-stale-tasks-unasked-within-their-project.md) — which Tasks an agent takes unasked: only standalone ones, never a Feature or a Feature's Task
- Related: [ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md) (Features), [ADR 0215](0215-the-board-is-the-issue-tracker-of-the-planning-skills.md) (the frontier the planning skills work)

## Context

The owner asked (2026-11-27) whether agents "pick up only tasks that are stand alone and not part of a conglomerated larger tasks that might have prerequisits", expecting that they already did.

ADR 0211's rules for unasked work name the status, the project, blockers and assignees, not Features. `next_task` in [`work.py`](../../imprint/tasks/work.py) followed them to the letter:

- A Feature's Tasks were offered unasked once their blockers were Done, alongside standalone Tasks.
- A Feature is itself a Task that other Tasks belong to ([ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md)), so a Feature moved to Ready was offered as one piece of work. A probe against the code at 70d8d17 showed a Ready P0 Feature offered ahead of its own Ready child, and still offered after the child was claimed.

The blocker check was already in place: a Task waiting on a Task that is not Done, or marked Blocked, was never offered, unasked or in a frontier.

## Decision

Unasked, an agent takes only a **standalone** Task: one that is neither a Feature (a Task other Tasks belong to) nor a Task of a Feature. Every other condition of [ADR 0211](0211-agents-take-ready-and-stale-tasks-unasked-within-their-project.md) still applies, and this holds for Stale Tasks as well as Ready ones.

A Feature's Tasks are taken when the user asks for that Feature: `tasks(action="next", feature="T-…")` (or `imprint tasks next --feature T-…`) lists its frontier, the Ready or Stale Tasks that are unblocked, unclaimed and not the user's, in order. A planning skill the user runs works the same frontier ([ADR 0215](0215-the-board-is-the-issue-tracker-of-the-planning-skills.md)). When an agent is asked for a Task directly, ADR 0211's "when asked" rule is unchanged.

A Task whose Feature was deleted stands alone again, as the Board shows it.

## Rejected alternatives

- **Leaving a Feature's Tasks open to unasked work and excluding only the Feature itself.** Offered to the owner as the narrower fix on 2026-11-27; the owner chose standalone Tasks only. A Feature's Tasks carry the Feature's context and order, which an agent that wanders past mid-session does not have.
- **Letting agents take over a Feature's Stale Tasks unasked.** Weighed while implementing this. A Stale Task's handoff gives the previous holder's checkpoint, not the Feature's plan, and one rule for every status is easier to follow. Stale Tasks stay visible on the Board and in the Feature's progress until the user asks for the Feature.

## Consequences

- Work inside a Feature moves only when the user asks for it, or runs a planning skill or `/loop` on it. A run that works the Board in general leaves it alone.
- `next` without a Feature tells an agent that a Feature's Tasks are taken only when the user asks for that Feature. The session-start line still invites it to ask `next` whenever the project has open Tasks, Feature Tasks included.
- The dashboard's autonomy choice reads "Standalone Ready and Stale tasks" and "Only standalone Stale tasks". A Task's drawer says that its Feature's Tasks are taken when asked.
- Each device decides what to offer from its own copy of the Board. Until a paired device runs a release with this change, its agents still take a Feature's Tasks unasked. Nothing is stored or synced differently.

## Invalidation conditions

- If the owner asks for some Features to be open to unasked work, a new record decides how a Feature opens, for example a per-Feature setting, and amends this one.
- If Features ever nest, which [ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md) rules out today, "standalone" must cover every ancestor, not just the Task's own Feature.

## Validation

- `tests/test_tasks.py` `test_only_standalone_tasks_are_taken_unasked`: neither a Ready Feature nor its Ready or Stale Tasks is offered, a standalone Task is, the frontier still lists the Feature's Tasks, and they stand alone once the Feature is deleted.
- `tests/test_tasks_tool.py` `test_a_features_frontier_through_next`: `next` without a Feature offers nothing and says that a Feature's Tasks are taken when asked.
- `imprint/ui/tests/board.spec.ts`: the drawer's hint for a standalone Task, a Feature's Task and a Feature, and the autonomy choice's label; the Board's 13 tests passed against a fresh build.
- Linux (WSL Ubuntu, Python 3.12 venv): the full suite, 1,568 tests, passed on 2026-11-27.
- Windows 11 (the installed runtime's Python 3.13, from a native copy of the repository): `test_tasks*`, `test_cli_tasks`, `test_adr_records`, `test_docs_tables` and `test_builtin_skills` passed.
- Not run locally: macOS; CI runs the suite there.
