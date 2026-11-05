---
title: ADR 0134 — Placement rules and Organize's proposals sync between paired devices
description: Placement rules carry when they last changed and keep removals, and travel to paired devices that offer them; a device takes the newer rule and moves its own copies by it. Organize's proposals and decisions travel the same way.
---

- Status: Accepted
- Date: 2026-11-05
- Amends:
  - [ADR 0036](0036-move-memories-between-projects.md) — placement rules travel to paired devices, which move their own copies by them
  - [ADR 0130](0130-reorganization-is-proposed-by-a-background-job-and-reviewed.md) — Organize's proposals and decisions travel to paired devices
- Related: [ADR 0016](0016-sync-direction-and-workspace-sharing.md), [ADR 0020](0020-explicit-pairing-and-automatic-workspace-sync.md), [ADR 0116](0116-paired-devices-send-only-what-the-other-lacks-in-stored-chunks.md), [ADR 0129](0129-core-memories-are-user-approved-and-start-every-session.md), [ADR 0132](0132-organize-runs-in-the-idle-slot-after-background-tagging.md), [ADR 0133](0133-strong-placements-apply-by-themselves.md), [sync](../sync.md)

## Context

The user asked whether what Organize does reaches their other devices, and chose that moves should apply on every device.

ADR 0036 kept placement rules on each device. Its rejected alternative was "Sync the rules ... Imports would then rewrite and delete existing memories, which ADR 0016 and ADR 0020 rule out ('sync never deletes'). It would be a separate change-log protocol." Its invalidation condition was a change-log protocol that may rewrite and delete.

Since then, delta sync (ADR 0116) exchanges per-record manifests in which the newer version wins, and core memories (ADR 0129) added a dataset sent only to peers whose handshake offers it. Those are the pieces a change log needs.

A move does not lose memories. It rewrites each one under its placed project's ID and removes the old copy (ADR 0036), the same on whichever device makes it.

Organize's proposals lived in `data/organize.sqlite3` on the device that ran it. With background Organize on one device (ADR 0132), the review list would exist only there.

## Decision

**Rules carry their change time.** Each placement rule records when it last changed. A removed rule stays as an empty target with its time, and placement ignores it, so a removal is a change like any other. A move writes only the rules it changed.

**Rules travel.** Delta sync gains a `placements` dataset for each paired workspace. The manifest key is the rule's origin and source, and the version is its change time. Like core memories, it goes only to a device whose handshake offers it. A device takes a peer's rule when it is newer than its own. It then moves its own memories and source snapshots that the changed rules govern, as if the move had been made there: same IDs, same rewrite, holding the ingestion lock. When a job holds the lock, an `apply-placements` job is queued to run after it. The new rules already place everything written meanwhile, so the result is the same.

Rules stream before memories, so memories that arrive after them are placed by them. Memories that arrive before are moved when the rules come.

**Proposals travel.** An `organize` dataset carries Organize's proposals, keyed by origin and source, with their status, evidence and change time, and the newer change wins. A device shows proposals another device made, and a decision made on either device reaches the other. A proposal whose source left the catch-alls becomes "nothing to propose" rather than being deleted, so that change travels too. A peer's quotes and previews are redacted again on arrival (ADR 0060).

Peers on older versions offer neither dataset. They are sent neither, and keep their own layout, as before.

## Rejected alternatives

- **Keeping rules per device (ADR 0036).** The user chose that moves apply on every device. Each device would otherwise need every move made again by hand.
- **Placement as a last-writer-wins payload field.** ADR 0036 rejected it: the ID includes the project, so a relabel without a new ID breaks sync's ID check.
- **Sending only Organize's decisions and letting each device decide again.** That would be a second rule store beside the placement rules, and a manual move (`imprint move`) would still stay on one device.
- **Rewriting moved memories as part of the memory transfer.** Each device computes IDs from its own placement (ADR 0036). Sending rules and letting each device move its own copies keeps that and needs no new record format.

## Consequences

- A move made on one device, by hand or by Organize, reaches every paired device that runs this version, and so does moving back.
- "Sync never deletes" (ADR 0020) still holds for memories. A received rule moves this device's copies to another project, and the copy under the old ID goes, as in a move made here.
- Rules conflict per origin and source, and the later change wins. Clocks that disagree can let an older change win, as for core memories.
- A device without the memories a received rule governs only stores the rule. Memories that arrive later are placed by it.
- Rules made before this change carry time 0. A peer that lacks a rule takes it, and one that holds the same key keeps its own.
- Profile notes are memories. One rewritten on another device leaves both texts here, because sync does not delete.

## Invalidation conditions

- If sync gains deletions for memories, send the old copy's removal with a move instead of each device moving its own.
- If clock disagreement makes rules flip between devices in practice, version rules by a per-device counter instead of wall-clock time.
- If a workspace's rules grow beyond what a manifest carries comfortably (the manifest is capped at 256 MiB), page them like memories.

## Validation

- `tests/test_placement_sync.py`:
  - a move reaches the peer through delta sync's streams and moves its copies, with identical IDs, and nothing is sent again;
  - a source snapshot moves with its memories and stays complete;
  - moving back travels as a removed rule;
  - the newer rule wins, and malformed rules are skipped;
  - a busy queue gets an `apply-placements` job, which places everything;
  - rules and proposals go only to a peer that offers them, and the importer checks the stream's datasets.
- `tests/test_organize.py`, `ProposalSyncTests`: proposals travel and the newer decision wins; a peer's proposal is validated and redacted.
- `tests/test_sync_delta.py`: manifests carry and decode the new datasets, and an older peer's manifest decodes without them.
- Not verified: two real paired devices syncing rules over the relay.
