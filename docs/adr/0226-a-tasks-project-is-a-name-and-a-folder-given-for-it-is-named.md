---
title: ADR 0226 — A Task's project is a name, and a folder given for it becomes the name of the project it holds
description: When a Task is created or edited with a folder as its project, the Board stores the name of the project that folder holds, named as the session's project is named, and refuses a folder that holds none; an upkeep step gives Tasks already stored with a folder their project's name, as one change by Imprint that syncs like any other.
---

- Status: Accepted
- Date: 2026-11-28
- Amends: [ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md) — its "Every Task names a project": the project is a name, never a folder
- Related: [ADR 0211](0211-agents-take-ready-and-stale-tasks-unasked-within-their-project.md) (how a session's project is named), [ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md) (every device computes the same state from the same events), [ADR 0201](0201-upkeep-brings-what-is-stored-up-to-date-in-the-idle-slot.md) (upkeep steps), [tasks](../tasks.md)

## Context

The owner reported on 2026-11-28 that one project showed up on the Board twice: once by its name and once by its folder, for Tasks that different agents created. The workspace's Board had Tasks under `gdpr-web-compliance` and under `/home/alexa/code/gdpr-web-compliance`, so the project filter, grouping and progress counted them apart.

- `tasks_tool.project` stored a project an agent named as given, and `changes.create` and `changes.edit` checked only its length. T-k76gh's history shows the agent passing its working directory as `project` at 2026-11-27 20:22.
- That was four hours after commit 779f218 (2026-11-27 16:06), which stopped the MCP server passing the session's folder as the default project ([ADR 0211](0211-agents-take-ready-and-stale-tasks-unasked-within-their-project.md)). So the default was fixed, but a folder an agent passes itself still got through. Builds from before that commit (v0.63.0-dev.1 to dev.3) used the session's folder as the default.
- A Task's state is computed from its events, and every device that holds the same events must compute the same state ([ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md)).

## Decision

**A folder becomes a name when it is written.** When a Task is created or its project edited, through the MCP tools, `imprint tasks` or the dashboard, a project that starts like a folder becomes the name of the project that folder holds. That covers an absolute POSIX path, a `~` path, a UNC share and a Windows drive path. The name comes from `derive_project`, as the session's own project is named ([ADR 0211](0211-agents-take-ready-and-stale-tasks-unasked-within-their-project.md)): `/home/alex/code/imprint`, `C:\Users\alex\code\imprint` and `\\wsl.localhost\Ubuntu\home\alex\code\imprint` all give `imprint`. A folder in a WSL distribution is named from its Linux path, so naming it never reads the share or starts the distribution. A folder this device cannot name is refused with a request to name the project. A name containing slashes that does not start like a folder, such as `github.com/owner/repo`, is kept as written. A folder used as a filter (`tasks(project=…)`, the board API, progress) means the project it holds.

**Tasks already stored with a folder get their project's name.** An upkeep step, `board-projects`, finds the workspace's Tasks whose project is a folder. It names each one and records the new name as an edit by Imprint (the `system` actor), a change that reaches paired devices like any other. A folder this device cannot name, for example another device's path outside a home's code folder, waits a week, or until the Task's project changes, and is then tried again. The step reads the Board, never the vector store, and needs no collection.

**The dashboard edits the project.** The Task drawer shows the project next to its status, priority and Feature: one the Board already knows, or another typed in.

## Rejected alternatives

- **Name folders when state is computed, not when it is written.** This would change no stored events and would fix every old Task at once. But `derive_project` names a folder outside a home's code folder from this device's project mappings and the manifests on its disk ([`cli_conversations.py`](../../imprint/cli_conversations.py), `derive_project`). Two devices holding the same events could then compute different projects, against [ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md).
- **Refuse every folder.** The agent would have to retry with a name, but a folder already holds the name it means, and the server names a session's folder this way already. Only a folder that holds no project this device can find is refused.
- **Leave stored Tasks for the person to fix in the drawer.** The drawer now lets them, but a Board where agents created Tasks before the write-time check would keep both spellings until someone noticed. The upkeep step repairs them without asking, and its changes show in each Task's history.

## Consequences

- One project has one name on the Board, whichever agent, client or device created its Tasks. Filters, grouping and progress count them together.
- An agent that passes a folder its device cannot name gets an error naming the folder, not a Task under a path.
- The repair is an ordinary edit event. A device that has not upgraded applies it like any other edit, and the upkeep step on two devices writes the same name.
- A project named after a folder that later moves keeps its name; nothing re-derives it.

## Invalidation conditions

- If `derive_project` starts to name a folder differently (another layout, or manifests first), then Tasks named before keep the old name. Reopen this record to decide whether upkeep renames them.
- If projects gain an identity of their own on the Board (an id, a folder per device), then storing a derived name stops being the right form. Supersede this record.
- If agents keep passing folders that cannot be named and the refusals reach the owner, reopen the refusal, for example by falling back to the session's project.

## Validation

- `tests/test_tasks.py` (`ProjectNameTests`): POSIX, macOS, Windows drive and WSL share folders become `imprint`; a name with slashes is kept; a folder holding no project is refused and nothing is created; a folder filter finds the project's Tasks. `tests/test_upkeep.py` (`BoardProjectStepTests`): the step renames a folder project once, as Imprint, without reaching the vector store; a dry run changes nothing; a folder it cannot name waits a week or until the project changes. These ran on Windows (Python 3.13, the install's venv) on 2026-11-28 with `tests/test_tasks*.py` and `tests/test_upkeep.py`.
- `imprint/ui/tests/board.spec.ts` covers moving a Task to another project in the drawer (Chromium, 2026-11-28).
- Not yet run: the step against the owner's live Board (T-k76gh and T-gekrp), which happens at the first idle run after the update, and the repair arriving on a paired device.
