---
title: ADR 0209 — A Task's history is a log of events that devices merge by union
description: Every change to a Task is an immutable event in its log; paired devices exchange the events the other lacks and keep all of them, and a Task's current state is computed from its events in one fixed order, so concurrent edits and comments on two devices are never lost.
---

- Status: Accepted
- Date: 2026-11-27
- Scope: On one device. The log, its fold in clock order, appends, tombstones and the import of another device's events (`import_events`) are implemented; exchanging events with paired devices as the `tasks` dataset, and the renamed event for colliding ids, are not yet.
- Related: [ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md) (the Board), [ADR 0168](0168-skills-and-personas-are-kept-once-and-synced-to-paired-devices.md) (the newest-wins rule this declines), [ADR 0116](0116-paired-devices-send-only-what-the-other-lacks-in-stored-chunks.md) (the paired exchange), [ADR 0060](0060-redact-secrets-before-memories-are-stored.md) (redaction), [ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md) (claims), [sync](../sync.md)

## Context

The owner asked for Tasks "synced across devices" so that agents in different clients on different devices can coordinate ([ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md)).

- Paired devices sync each pair again about 60 seconds after the previous attempt ends (`sync_pairs.py`, `watch(interval=60)`; [sync](../sync.md)). Two devices can change the same Task within one interval.
- Skills, personas and core memories sync one record per item, and the newest change wins ([`registry.py`](../../imprint/registry.py), "Sync (ADR 0168)"). [ADR 0168](0168-skills-and-personas-are-kept-once-and-synced-to-paired-devices.md) accepted losing the older of two concurrent edits because one person rarely edits one skill on two devices within an interval. On a Board, concurrent writes are the expected case: several agents comment on and checkpoint the same Task.
- Deletions travel as tombstones only for memories and recordings ([`sync_deletions.py`](../../imprint/sync_deletions.py), `DATASETS`).

## Decision

**Events.** Every change to a Task is an event appended to its log: created, edited (the fields changed), appended (text added to the description), moved (status), marked or cleared blocked, linked, tagged, assigned, claimed, released, checkpoint, comment, renamed, cancelled and deleted. An event is never changed after it is written. It has a random id, the device and agent session that wrote it, and a hybrid logical clock timestamp.

**Merging.** A device sends a paired device the events it lacks, and the receiver keeps every event it has not seen. A Task's state is computed from its events in timestamp order, then event id: for each field the last edit wins, appended text, comments and checkpoints accumulate, and claims resolve as [ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md) describes. Every device that holds the same events computes the same state. Appending is its own event because `wayfinder` resolves work by appending to one shared ticket's description while other sessions do the same; as an edit, one of two concurrent appends would be lost.

**Colliding ids.** When a Task arrives whose id another Task already has, the Task created later gets a new id through a renamed event, which every device applies the same way; references by the old id resolve to it.

**Lease renewals are not events.** Each claim's latest renewal time is one value per claim, and devices exchange it keeping the later one, so the log does not grow every few minutes while an agent works.

**Deletion.** Deleting a Task appends a deleted event. Every device that holds it drops the content of the Task's other events and keeps only their ids and the tombstone, and discards events for that Task that arrive later. The tombstone is kept as long as the workspace.

**Redaction and scope.** Every event's text is redacted before it is stored ([ADR 0060](0060-redact-secrets-before-memories-are-stored.md)), including events received from a paired device. Events travel as the per-workspace dataset `tasks`, sent only to a peer whose handshake offers it; an older peer neither sends nor receives Tasks.

## Rejected alternatives

- **The newest change of the whole Task wins, as for skills.** Two comments or checkpoints written on two devices within one sync interval would keep only one, and concurrent writes are what the Board is for.
- **Holding conflicting versions for the user to choose.** [ADR 0168](0168-skills-and-personas-are-kept-once-and-synced-to-paired-devices.md) declined this for skills because the held conflict must sync too; on a Board there would be one for every pair of concurrent writes.

## Consequences

- No edit, comment or checkpoint is lost. Edits to one field on two devices within an interval resolve to the later timestamp, and both stay in the history.
- The log grows with activity, and archived Tasks keep their events; compacting it is not decided.
- Ordering between devices whose clocks disagree is decided by the hybrid logical clock, which keeps causally later events later but orders independent events within the skew arbitrarily.
- Tombstones for Tasks live in the log rather than in `sync-deletions.sqlite3`.

## Invalidation conditions

- If a workspace's log grows until loading the Board or computing its state takes more than a second, add compaction (a computed snapshot plus later events) in a new record.
- If Boards are shared with other people, events need verifiable authors before they are merged; decide that in a new record.

## Validation

- `tests/test_tasks.py`: every change recorded as an event with its device, session and clock; clocks that keep order when the wall clock stands still or goes back; last-edit-wins fields and accumulating appends; a deletion that empties the other events' content; an imported event kept once, ordered by its clock, and moving the store's clock.
- Linux (WSL Ubuntu, Python 3.12 venv): the full suite, 1,522 tests, passed on 2026-11-27 with the Board and built-in skills together.
- Windows 11 (the installed runtime's Python 3.13, from a native copy of the repository): `tests.test_tasks`, `test_tasks_tool`, `test_cli_tasks`, `test_hooks`, `test_agent_activity`, `test_config_help`, `test_builtin_skills`, `test_registry`, `test_registry_files`, `test_skills_tool`, `test_chat_tool_coverage`, `test_adr_records` and `test_docs_tables`, 187 tests, passed.
- Not run: macOS.
