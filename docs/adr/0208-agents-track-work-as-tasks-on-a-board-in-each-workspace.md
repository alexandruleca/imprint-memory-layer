---
title: ADR 0208 — Agents track work as Tasks on a Board in each workspace
description: Agents and the user track work as Tasks on one Board per workspace, with fixed statuses, kept by Imprint itself rather than mirrored to an external tracker such as GitHub Issues.
---

- Status: Accepted
- Date: 2026-11-27
- Scope: The Board, its tools and `imprint tasks`. The dashboard's Board tab is not built yet, and Tasks are not yet synced to paired devices ([ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md)).
- Related: [ADR 0072](0072-projects-name-their-workspace-and-each-device-approves-it.md) (projects name their workspace), [ADR 0117](0117-the-dashboard-shows-each-agent-session-and-its-calls.md) (the Agents page), [ADR 0168](0168-skills-and-personas-are-kept-once-and-synced-to-paired-devices.md) (installation-wide data, the contrast), [ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md) (how Tasks sync), [ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md) (claims), [ADR 0211](0211-agents-take-ready-and-stale-tasks-unasked-within-their-project.md) (taking work unasked), [ADR 0212](0212-agents-talk-through-comments-on-tasks.md) (comments), [ADR 0215](0215-the-board-is-the-issue-tracker-of-the-planning-skills.md) (the Board as the skills' tracker)

## Context

On 2026-11-27 the owner asked for "a task system kanban style that can be used by agents to keep track of issues and work progress for easier tracking and multi agent workflows", shown "in another tab in the agents section" of the dashboard, where agents "detect stale issues or stalling issues" and "pick them up to finish them", which "can also facilitate communication between different harnesses such as codex and claude code", with issues "synced across devices".

- Every connected client starts its own Imprint MCP server process against the same installation, and each records itself in `data/agent_activity.sqlite3` ([`agent_activity.py`](../../imprint/agent_activity.py), lines 1-12). Clients on one device already share one Imprint.
- Imprint keeps data at two scopes: per workspace (memories, facts, placements, Organize, backlog; `sync_ws.py`, lines 778-785) and per installation (core memories, [ADR 0129](0129-core-memories-are-user-approved-and-start-every-session.md); skills and personas, [ADR 0168](0168-skills-and-personas-are-kept-once-and-synced-to-paired-devices.md)).
- The owner's skills `to-tickets`, `triage`, `wayfinder` and `implement-spec` read and publish work through "the configured issue tracker", which the skill `setup-matt-pocock-skills` describes in a repository's `docs/agents/issue-tracker.md`: GitHub, GitLab, local Markdown files, or another tracker described in prose.

## Decision

**A Board per workspace.** A Task belongs to one workspace, and each workspace has one Board. Every Task names a project, by default the creating agent's current project, and the Board filters by project. Tasks are kept with the workspace's other data, travel with that workspace's pair sync ([ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md)), and go when the workspace is deleted or wiped.

**What a Task is.** A Task has an id of `T-` and five base32 characters (`T-7f3kq`), drawn at random so that devices which are offline do not hand out the same one ([ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md) renames the later of two that collide). It has a title, a description, a project, a priority from P0 (most urgent) to P3, a position within its status that the user sets by dragging, and acceptance criteria.

**Fixed statuses.** A Task is in exactly one of Backlog, Ready, In progress, Review, Done or Cancelled. *Blocked* is a mark with a reason, not a status, so a blocked Task keeps its place. Statuses are the same in every workspace and cannot be configured, so every agent in every client reasons about the same states. A Task moves to Ready only with at least one acceptance criterion, since an agent that takes it unasked ([ADR 0211](0211-agents-take-ready-and-stale-tasks-unasked-within-their-project.md)) needs to know when it is finished.

**Grouping.** A Task can have blocking Tasks (blocked-by edges), belong to one Feature (a Task that groups the Tasks building one piece of work; a Feature's Tasks have no Tasks of their own), and carry free-form tags.

**Assignees.** A Task has a list of assignees, each the user, a client (`claude-code`, `codex`, …), a device, or a client on a device. A Task assigned to the user is never claimed by an agent; agents comment on it, and the user hands it over by changing its assignees. [ADR 0211](0211-agents-take-ready-and-stale-tasks-unasked-within-their-project.md) says how assignees limit which agents take a Task.

**Filtering.** The Board, `tasks list` and the CLI filter by project, Feature, tag, status, priority, assignee, holder and text, and the Board groups its columns by project or by Feature.

**Review and Done.** The holder that finishes a Task moves it to Review with a checkpoint saying what changed and how it was verified. The user, or a different agent session that reviewed the work, moves it to Done; the session that did the work does not. Agents review a Task when asked, never unasked.

**Ending a Task.** Agents can cancel a Task but not delete it. The user can delete one from the dashboard or the CLI ([ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md) says how a deletion travels). Done and Cancelled Tasks leave the Board 14 days after they end and stay readable.

**Access.** Agents read the Board through one MCP tool, `tasks` (board, list, get, next, inbox, handoff), and change it through another, `task_edit` (create, edit, append, move, assign, claim, release, checkpoint, comment, link, block, cancel, name). The `imprint tasks` CLI and the dashboard's API call the same functions. The dashboard shows the Board as a tab of the Agents page, in simple and advanced mode. Tasks are not memories: they are not embedded and `search` does not return them; `tasks` searches their text, and an agent stores what a Task taught as a memory, as it does today.

**Standalone.** The Board is Imprint's own record. It is neither mirrored to nor imported from GitHub Issues or another tracker. [ADR 0215](0215-the-board-is-the-issue-tracker-of-the-planning-skills.md) makes it usable as the "issue tracker" of the skills above.

## Rejected alternatives

- **One Board for the whole installation, like skills and core memories.** Weighed in the design discussion of 2026-11-27, where the owner chose a Board per workspace. Workspaces are isolated so that one project's sessions do not see another's memories ([ADR 0015](0015-workspace-roots-and-source-purge.md)); an installation-wide Board would show every workspace's work in every session.
- **Mirroring Tasks to GitHub Issues.** Claude recommended starting standalone and adding an adapter later; the owner chose standalone, not mirrored (2026-11-27).
- **Statuses configurable per workspace.** Weighed in the same discussion and declined: agents in different clients would have to discover each workspace's states before acting on them.
- **One Board per project.** Weighed in the same discussion. A workspace often holds several related projects, and a filter on one Board shows them together or apart.
- **Numbering Tasks from a counter per workspace (`T-14`).** Two devices that are offline would both hand out the next number.
- **The holder marks its own work Done.** Weighed in the same discussion; a second look, by the user or another session, is what Review is for.
- **Indexing Tasks as memories, so `search` finds them.** There would be two copies of every Task, synced by separate datasets, with deletions to keep in step. Weighed in the same discussion and deferred until there is evidence that agents miss Tasks in `search`.

## Consequences

- Claude Code, Codex and the other clients on one device share a Board with no sync, because they reach the same local Imprint.
- Work tracked on the Board does not appear in GitHub Issues; people who do not run Imprint do not see it.
- Moving a Task to another workspace is not provided.
- The Board becomes a new per-workspace store that deleting or wiping the workspace must remove, and that the workspace's sync must carry.

## Invalidation conditions

- If Tasks must be visible to collaborators who do not run Imprint, add a mirror to their tracker in a new record.
- If agents regularly need Tasks from another workspace (the owner moves or copies Tasks by hand between workspaces), reconsider an installation-wide Board.
- If a skill the owner relies on needs a state that the fixed statuses, the Blocked mark and tags cannot express, add a status in a new record.
- If agents repeat work that a Done Task already records because `search` did not return it, index Tasks as memories in a new record.
- If two Task ids collide more than once in the owner's workspaces, lengthen the id.

## Validation

- `tests/test_tasks.py`: creation, ids and their collisions, workspace isolation and unknown workspaces, redaction, edits, appends, moves, the Ready rule, Features and blockers (nesting and cycles refused), tags, assignees, the Blocked mark, filters, archiving after 14 days, and the user-only deletion that leaves a tombstone.
- `tests/test_tasks_tool.py`: the `tasks` and `task_edit` tools through an in-process MCP client, including that agents have no delete. `tests/test_cli_tasks.py`: every `imprint tasks` command as a subprocess.
- Linux (WSL Ubuntu, Python 3.12 venv): the full suite, 1,495 tests, passed on 2026-11-27.
- Windows 11 (the installed runtime's Python 3.13, from a native copy of the repository): `tests.test_tasks`, `test_tasks_tool`, `test_cli_tasks`, `test_hooks`, `test_agent_activity`, `test_config_help`, `test_chat_tool_coverage`, `test_adr_records` and `test_docs_tables`, 121 tests, passed.
