---
title: ADR 0218 — Imprint renames a Task whose id another device gave out
description: When synced Tasks share an id, the older keeps it and each other one takes an id derived from its own identity, recorded by Imprint itself as a renamed event; the renamed Task keeps its former ids, which search finds, while the id now names only the older Task.
---

- Status: Accepted
- Date: 2026-11-27
- Amends: [ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md) — how colliding ids are renamed, and where the old id leads
- Related: [ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md) (Task ids)

## Context

Task ids are `T-` and five random characters ([ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md)), so two devices that are offline can give out the same one, and the Board syncs between paired devices ([ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md)). ADR 0209 decided that the Task created later gets a new id through a renamed event "which every device applies the same way; references by the old id resolve to it".

Building it showed two gaps. Each device that notices a clash records a renamed event of its own, so the new id must not depend on which device chose it. And one id cannot lead to both Tasks: the older one keeps it, so references by the old id lead to the older Task.

## Decision

- When Tasks share an id, the one whose creation has the earliest clock (then the smallest identity) keeps it. Each other one takes `T-` and five characters derived from a hash of its own identity, derived again with a counter while the result is taken; every device derives the same id.
- The rename is a `renamed` event whose author is Imprint itself (actor `system`), not the user or an agent. Applying a second record of the same rename changes nothing.
- The old id names only the Task that kept it. The renamed Task lists its former ids, search by text finds it by them, and the dashboard shows them beside its new id.

## Rejected alternatives

- **A random new id, as for a new Task.** Two devices that both notice the clash would choose different ids, and the last rename by clock would win only after both had shown another id for a while.
- **Resolving the old id to the renamed Task.** It would take the id from the older Task, which other agents and the user already know by it.

## Consequences

- An agent that knew the renamed Task by its old id finds the older Task there; the renamed Task's history and search name the old id.
- History shows Imprint as the author of a rename.

## Invalidation conditions

- If renames happen often enough to confuse agents (more than one a month on the owner's Boards), lengthen ids ([ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md)).

## Validation

- `tests/test_tasks_sync.py`: two devices that gave out the same id end, after syncing in either order, with the same two ids; the older keeps its id; the renamed one lists the former id, search finds it by it, and its rename's author is Imprint.
- Not run: macOS; a real sync between two devices.
