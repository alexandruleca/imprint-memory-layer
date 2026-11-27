---
title: ADR 0215 — The Board is the issue tracker of the planning skills
description: The built-in imprint-tasks skill carries an issue-tracker description that a repository adopts through the planning skills' "Other" tracker option, mapping their triage states, sub-issues, blocking edges, assignment, closing and frontier query onto the Board, so the user's own skills work against it without being forked.
---

- Status: Accepted
- Date: 2026-11-27
- Related: [ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md) (the Board), [ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md) (appending), [ADR 0213](0213-built-in-skills-ship-with-each-release-and-a-fork-replaces-them.md) (built-in skills)

## Context

The owner plans and runs work through skills that read and write "the configured issue tracker": `to-tickets` publishes tickets with blocking edges, `triage` moves issues through triage states, `wayfinder` keeps a map ticket with child tickets, and `implement-spec` works the ready ones. The owner's guidelines ask agents to use such skills for any larger piece of work.

- `setup-matt-pocock-skills` writes a repository's tracker into `docs/agents/issue-tracker.md`, a free-prose description chosen from GitHub, GitLab, local Markdown files, or "Other", which records any other tracker in prose.
- The operations these skills perform: create; read with comments and labels; list by label and state; make a ticket the sub-issue of a parent; add blocked-by edges and count open blockers; comment; add and remove labels; close after posting a comment; assign, which is how `wayfinder` claims a ticket; and a "frontier" query of open, unblocked, unassigned children in order.
- Triage uses five states, `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human` and `wontfix`, each mapped to a repository's own label strings.

## Decision

`imprint-tasks` includes an issue-tracker description for the Board. A repository adopts it through the "Other" option of `setup-matt-pocock-skills`, which then records the Board as its tracker. The skills map onto the Board as follows:

| Tracker operation | On the Board |
| --- | --- |
| `needs-triage`, `needs-info` | Backlog, with that tag |
| `ready-for-agent` | Ready |
| `ready-for-human` | Ready, assigned to the user |
| `wontfix` | Cancelled |
| labels | tags |
| sub-issue of a parent | a Task of that Feature |
| blocked-by edge, open blockers | blocking Tasks, and those not Done |
| assign | claim |
| comment | comment |
| close | Done, after the closing comment |
| append to a ticket's body | append to the description |
| frontier query | `tasks next` for the Feature |

Every operation is available through the `tasks` and `task_edit` tools and the `imprint tasks` CLI, for skills that run shell commands.

## Rejected alternatives

- **Forking `setup-matt-pocock-skills` to add the Board as a fifth option.** Weighed in the design discussion of 2026-11-27. The "Other" option already records any tracker in prose, and a fork of the user's skill would stop following its upstream.

## Consequences

- The user's planning skills publish to and work from the Board in any repository that adopts it, from any client.
- Triage states other than the ready ones live as tags, so the Board's columns stay the fixed statuses.
- A repository that adopts the Board stops publishing those tickets to GitHub or its previous tracker.

## Invalidation conditions

- If the planning skills add an operation the Board cannot express (for example a state between Ready and Done), map it or add it in a new record.
- If `setup-matt-pocock-skills` drops its "Other" option or starts validating tracker names, ship the description as an option of its own.

## Validation

- The description and mapping ship in `imprint/builtin_skills/imprint-tasks/issue-tracker.md`, reached from the skill's `SKILL.md`.
- `tests/test_tasks_tool.py`: every `tasks(...)` and `task_edit(...)` example in the skill's files names an action and arguments the shipped tools accept; a mutation that adds a wrong argument and a wrong action fails it.
- Not run: a planning skill (`to-tickets`, `wayfinder`) publishing to the Board in a repository set up through `setup-matt-pocock-skills`.
