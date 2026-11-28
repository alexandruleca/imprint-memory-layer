---
title: ADR 0242 — A Feature's status follows its Tasks
description: A Feature is created as one or becomes one when a Task is linked to it; its status is computed from its Tasks on every read, only Cancelled is set by hand, agents neither claim nor take it, and the Board shows it beside its columns rather than in one.
---

- Status: Proposed
- Date: 2026-11-28
- Related: [ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md) (Features, which this would amend), [ADR 0219](0219-agents-take-only-standalone-tasks-unasked.md) (what a Feature is when agents take work unasked, which this would amend), [ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md) (a Task's state computed from its events), [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) (the Dispatcher's candidates)

## Context

The owner asked (2026-11-28): "we could also extract the features outside the board just as a place to keep track of tasks inside of them since features should get closed when all of theyre sub tasks are done right?"

They were not closed. A Feature was a Task that other Tasks belong to ([ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md)), with a status of its own that someone set by hand:

- The Features T-ygxe1 (eight Tasks) and T-7ytct (four) reached Done only because the agents that finished their last Tasks moved them there.
- On the Board, a Feature's card sat in a status column beside its own Tasks and could be dragged to any status, whatever its Tasks said.
- T-e5zyz: runeforge created the Feature T-8t6rp at 18:01 as Ready, before its Tasks existed. The Dispatcher claimed it at 18:02 and started a Claude Code session on it. Nothing marked it as a Feature until a Task was linked to it, so `_standalone` in [`work.py`](../../imprint/tasks/work.py) counted it as a standalone Task, which [ADR 0219](0219-agents-take-only-standalone-tasks-unasked.md) rules out.

## Decision

**What a Feature is.** A Task is a Feature when it is created as one (`kind="feature"` on `task_edit`, `imprint tasks create --kind feature`, the dashboard's API), or once another Task belongs to it. Editing `kind` turns a Task into a Feature, or a Feature without Tasks back into a Task. Features still do not nest.

**Its status follows its Tasks.** The Board computes it each time it is read, from the Tasks that belong to it:

- **Backlog:** no Tasks, or only Backlog ones (Cancelled Tasks are not counted).
- **Ready:** at least one Ready Task, and none started.
- **In progress:** any Task in progress, in review or Done, while not every one is Done.
- **Done:** every Task that was not cancelled is Done. It ends when the last of them ended.

A Task added to a Done Feature, or reopened, reopens it. **Cancelled** is the one status set by hand. A Cancelled Feature stays Cancelled whatever its Tasks do, until it is moved out of Cancelled, which hands its status back to its Tasks. The Feature's own acceptance criteria do not hold it open.

**What is refused.**
- Moving a Feature to any status other than Cancelled, unless it is Cancelled now. The refusal names the status its Tasks give it.
- Claiming a Feature. Its Tasks are claimed, through its frontier.
- Linking a Task to a Task that a live claim holds, or editing such a Task into a Feature, so a Task an agent is working on never becomes a Feature under it. A Stale claim does not count.

**What agents take.** `tasks(action="next")`, `takeable()` and so the Dispatcher's candidates leave out every Feature, including one created before any Task is linked to it. A Feature never reaches Review, so the Dispatcher's reviews never see one. A Feature's Tasks are still taken through its frontier, as ADR 0219 says.

**Where it is shown.** `tasks.board()` lays out Tasks by status and lists Features beside the columns, not in them. The `tasks` tool's Board text lists open Features after the columns, each with its computed status and how many of its Tasks are done; `get` says that a Feature's status follows its Tasks. The dashboard's Board keeps its columns for Tasks, and Features get a tab of their own on the Agents page.

**Storage and sync.** No new kind of event. A Feature's `created` event carries `kind: "feature"`, and an `edited` event can change `kind`. A plain Task's events are written as before. The projection keeps the status that events wrote, and the view computes a Feature's status over it, so every device computes the same status from the same events. A move written by a release without this decision is kept in the history and ignored.

## Rejected alternatives

- **The `feature` tag as the sign of a Feature.** Listed in T-e5zyz. All six Features on the Board carried it, by the planning skills' habit, but a tag is free text that any agent adds or removes, and "feature" also tags feature requests that are ordinary Tasks. A tag that silently changed how a Task is taken and shown would be a hidden rule.
- **Creating Features in Backlog and moving them to Ready once their Tasks exist.** Listed in T-e5zyz, and written into the `imprint-tasks` skill that evening. It depends on every agent and person following the order, and a Feature still needed someone to move it to Done.
- **Moving a Feature with a stored event when its last Task ends.** Each device that saw the last Task end would write its own move, paired devices would sync several, and a person's later move would contradict them. A computed status needs no event and agrees on every device.
- **Keeping Features in the columns with a computed status.** A Feature's card would sit in In progress beside its own Tasks and could be dropped nowhere, and the owner asked for a place outside the Board.
- **Stopping a Dispatched run whose Task becomes a Feature.** The third option in T-e5zyz. Refusing to make a held Task a Feature prevents that case on the Board, without a new way for a run to end.

## Consequences

- Features close by themselves and reopen when work is added. The finishing agent no longer moves them, and the built-in `imprint-tasks` skill tells agents to create Features with `kind="feature"` and not to move or claim them.
- T-e5zyz's first gap is closed for devices on this release. Its second gap cannot arise from this device's Board. A paired device on an older release can still link a Task to a held one, and there the Dispatcher's existing claim checks apply.
- The Dispatcher's Refine role reads `state['tasks']` to skip Features in [`dispatch/loop.py`](../../imprint/dispatch/loop.py). Until it reads `kind`, a Feature created without Tasks could be offered for refinement.
- Existing Features take their computed status as soon as a device runs this release. A Feature an agent moved by hand ahead of its Tasks shows where its Tasks are.
- A paired device on an older release still shows and sets a Feature's stored status, and its agents may still take a Feature created with `kind` but no Tasks. Such a Feature is stored as Backlog, which older releases do not offer.
- The person can no longer hold a Feature open after its Tasks are Done. A final Task does that, as every Feature's "accept the records and finish the docs" Task does today.

## Invalidation conditions

- If the person wants a Feature checked after its Tasks are Done, for example a review of the whole, then a Feature needs a state between In progress and Done, and this record is amended.
- If Features ever nest, which ADR 0208 rules out, a Feature's status must add up its sub-Features too.
- If paired devices on releases older than this one stay in use and their moves confuse the person, the dashboard should mark a Feature whose stored status differs from the computed one.

## Validation

- `tests/test_tasks.py` `FeatureStatusTests`:
  - a Feature created as one waits in Backlog without Tasks;
  - its status follows its Tasks through each rule, and a new Task reopens it;
  - a Task becomes a Feature when a Task is linked to it, and its stored status stops counting;
  - moves are refused, while cancelling and reopening are not;
  - a move written by another release is ignored;
  - agents neither claim nor take a Feature, even before it has Tasks (T-e5zyz);
  - a held Task does not become a Feature;
  - only a Feature without Tasks becomes a Task again, and Features do not nest;
  - the Board lists Features beside its columns;
  - progress counts a Feature without Tasks as a Feature.
- `tests/test_tasks_api.py` `test_features_are_created_with_their_kind_and_kept_out_of_the_columns` and `tests/test_tasks_tool.py` `test_an_agent_creates_a_feature_and_reads_it_apart_from_the_columns`.
- Windows 11 (the installed runtime's Python 3.13, from a native copy of the repository): the Board, tool, API, CLI, progress, sync and Dispatcher loop, refine, review, takeover and Needs-you tests, 209 in all, passed.
- Not yet run: the full suite, macOS and Linux CI, and the dashboard's Features tab, which T-f5vvc builds.
