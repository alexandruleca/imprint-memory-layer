---
title: ADR 0200 — Synced memories keep the time they were first stored
description: A memory received from a paired device is dated when its origin device recorded it, not when it arrived; memories already received are dated back once by upkeep. Statistics, recent lists and the tagging order then show when a memory was made on every device.
---

- Status: Accepted
- Date: 2026-11-22
- Related: [ADR 0017](0017-device-provenance-and-local-project-paths.md) (`recorded_at`, the origin's record time), [ADR 0116](0116-paired-devices-send-only-what-the-other-lacks-in-stored-chunks.md) and [ADR 0051](0051-synced-tags-follow-the-latest-retag.md) (what sync compares, unchanged), [ADR 0176](0176-deletions-reach-paired-devices-that-hold-the-memory.md) (deletions, which already compare `recorded_at`), [ADR 0201](0201-upkeep-brings-what-is-stored-up-to-date-in-the-idle-slot.md) (the repair of memories already received), [sync](../sync.md)

## Context

- **What reads `timestamp`.** A memory's `timestamp` payload field (a float index) drives:
  - the dashboard's statistics: memories per month, and "added in the last 7 days" (`dashboard_summary`, `cli_viz`);
  - recent lists, and the recent activity `wake_up` gives a session;
  - background tagging's newest-first order (ADR 0067).
- **What sync wrote.** Live sync stores received memories through `vectorstore.store_batch_precomputed`. It set `timestamp` to the time of arrival. The sender's `timestamp` travelled in every record (`MEMORY_FIELDS`), but the importer dropped it.
- **The origin's time survived elsewhere.** `recorded_at`, the time the origin device recorded the memory (ADR 0017), travels and is kept.
- **The owner's report:** memories from October showed up as November in the statistics.
- **Measured** read-only on the owner's Windows store (388,411 memories) on 2026-11-22:
  - 154,607 memories came from paired devices;
  - 152,317 of them carried a `timestamp` more than a minute after their `recorded_at`;
  - 77,572 were more than a day late. 77,084 of those were created in October and counted in November.
- **Only this path was affected:**
  - Snapshot bundles (`imprint sync import`) restore the vector store's payloads unchanged.
  - Facts keep `valid_from`.
  - Source snapshots keep their `updated_at`.

## Decision

1. **A received memory is dated when it was first stored.** Its `timestamp` is set by `vectorstore.first_stored`:
   - the origin's `recorded_at`;
   - else the sender's `timestamp`;
   - else the time of arrival;
   - and never later than the time of arrival, since a peer's clock may run ahead.
2. **The importer keeps the sender's `timestamp`.** A record from before provenance, without `recorded_at`, keeps the sender's time. `device.prepare` fills its `recorded_at` from that time, as it already did for any record that carries one.
3. **Memories already received are dated back once per workspace by upkeep** (ADR 0201). The repair applies to memories recorded by another device whose `timestamp` is more than a minute after their `recorded_at`: they take `recorded_at`.
   - Memories recorded on this device are left alone, and so are memories without an origin.
   - Each device repairs its own copies.

## Rejected alternatives

- **Make the views read `recorded_at` instead.** Every reader of `timestamp` would change: statistics, recent lists, `wake_up`, the tagging order and Organize's profile times. `recorded_at` has no payload index, and memories stored before provenance lack it.
- **Send a new field or raise the sync protocol.** The right time already travels. Old devices keep working with new ones in both directions.
- **Rewrite the dates through sync.** Sync compares memory ids and classification times (ADRs 0116, 0051), never `timestamp`, so a repaired date would not travel. It does not need to: every device dates its own copies.

## Consequences

- **Statistics show when a memory was made, on every device.**
  - Months count memories by their creation.
  - "Added in the last 7 days" counts memories made in the last week, wherever they were made. A first sync of old memories no longer inflates it.
- **Recent lists follow creation time.** The recent lists and a session's recent activity show a peer's memories at their creation time, so a large sync no longer floods them.
- **Background tagging orders received memories by creation time.**
- **Sync is unchanged.** A repaired or received date changes nothing sync compares, so no memory is sent again.
- **Mixed versions keep working.** A device that has not updated still dates arrivals by arrival; once updated, its upkeep repairs them.
- **`timestamp` changes meaning for received memories.** On a receiving device it no longer says when the memory arrived there. Nothing uses that today.

## Invalidation conditions

- If something needs the arrival time (for example "what sync brought today"), then add an arrival field rather than redating `timestamp`.
- If a device's clock was wrong when it recorded memories, so that `recorded_at` is far off, then prefer the sender's `timestamp` for that device's memories, or ask the user.

## Validation

- `tests/test_upkeep.py`, on Windows Python 3.13:
  - `first_stored` prefers the origin's time, then the sender's, never returns a time past arrival, and refuses non-numbers.
  - A sync exported from device A and imported on device B keeps October for three memories: one recorded in October, one A itself received late (`recorded_at` wins), and one without `recorded_at` (the sender's time, which also fills `recorded_at`).
  - The repair step: see ADR 0201.
- `test_device_provenance`, `test_sync_deletions` and `test_background_tagging` pass unchanged.
- A read-only dry run of the repair on the owner's store would date back 152,317 memories from paired devices.
