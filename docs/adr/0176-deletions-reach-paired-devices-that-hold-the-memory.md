---
title: ADR 0176 — Deletions reach paired devices that hold the memory
description: A device records the memories it deleted on purpose and, after paired devices swap manifests, names to the other only those of its deletions the other holds; the other deletes copies older than the deletion and passes it on, and no device imports a memory older than a deletion it holds.
---

- Status: Accepted
- Date: 2026-11-11
- Amends:
  - [ADR 0020](0020-explicit-pairing-and-automatic-workspace-sync.md) — sync carries deletions; it is no longer a pure merge
  - [ADR 0116](0116-paired-devices-send-only-what-the-other-lacks-in-stored-chunks.md) — after the manifests, the devices swap deletions, which change what each then sends
- Related: [ADR 0060](0060-redact-secrets-before-memories-are-stored.md) (no hash of a removed secret), [ADR 0015](0015-workspace-roots-and-source-purge.md) (purge), [ADR 0018](0018-single-sync-implementation-and-end-to-end-encryption.md) (logical IDs), [ADR 0134](0134-placement-rules-and-organize-proposals-sync.md) (moves), [sync](../sync.md)

## Context

Sync is a deduplicating merge (ADR 0020): a memory deleted on one device comes back from any paired device that still holds it. Issue #32 tracks this, and the user asked for sync to delete as well. It happens in two everyday ways:

- the user deletes a memory, purges a folder or deletes a meeting recording, and the memories return at the next sync;
- a file changes, refresh replaces its chunks, and the other device sends the old chunks back, so both versions pile up on both devices.

The issue sets the constraints. Deletions must not leak deleted content: a memory's ID hashes its content, so sending IDs to a device that never held them can be an offline guessing oracle for short secrets (ADR 0060 rejected old-ID records for that reason). They must survive a device that was offline when the delete happened. They must respect workspace isolation, purge exclusions and logical IDs. And they need a retention rule.

## Decision

**Each device records what it deleted on purpose**, per workspace and dataset (memories, for now): the key and the time, in `data/sync-deletions.sqlite3`, kept 180 days. A key deleted again keeps the later time. What is recorded is decided where the deletion happens:

| Deletion | Recorded |
| --- | --- |
| The user's: `delete`, deleting a recording, purge | every memory deleted |
| A newer version of a source replacing its chunks (refresh, URL refresh, inline content), an emptied file, a rewritten project profile | the chunks this device recorded (`recorded_device`); another device's chunks of its own version of the source are not |
| A file gone from disk | nothing: its memories may still matter on the other device |
| Scrub and redaction supersede | nothing: the record would be a hash of the removed secret; the redacted version already supersedes it (ADR 0060) |
| Moves, wipes, removing a workspace, migrations | nothing: placement rules carry moves (ADR 0134) |

A deletion is recorded before the memory is removed, so a failure leaves one record too many, which is harmless, rather than one too few.

**Paired devices swap deletions after their manifests**, when both handshakes offer `deletions`. Each device sends, of the other's own manifest, the keys it deleted and does not hold again, with their times. It sends them only when the pairing lets it send data, and the receiver ignores them otherwise. A device never sends a key the other did not list, so no ID reaches a device that never held the memory.

**The receiver deletes its copy when the copy is older than the deletion** (its `recorded_at`, the origin's time, against the deletion's). It records the deletion with the original time, so the deletion reaches its other pairings, and it leaves those keys out of what it sends. A copy made after the deletion stays and is sent back.

**No device imports a memory older than a deletion it holds**, from any sender, an older release or a full-workspace import included. A device that was away cannot bring it back while the deletion is kept.

**One sync deletes at most half of a workspace's memories** (at least up to 200). A larger request is refused whole and reported on the paired device ("kept N memories the other device deleted: too many at once"), in case the other device is broken rather than its user thorough. The paired devices list shows what the last sync deleted.

## Rejected alternatives

- **Sending every deletion record to every peer**, as a dataset of its own. Peers that never held a memory would learn its ID, the guessing oracle the issue rules out.
- **Keyed hashes of IDs instead of IDs.** Paired devices share keys, so a peer could still hash its guesses; it adds nothing over naming only keys the peer listed.
- **Propagating every deletion, refresh pruning and other devices' chunks included.** A device that drops a deleted local clone, or holds a newer version of a shared file, would delete memories, or another device's chunks of its own version, everywhere. Two devices with different versions of one file would delete each other's chunks at every refresh.
- **Comparing against the time the receiver stored its copy.** That time differs per device; the origin's `recorded_at` travels with the memory and orders a deletion against a copy made again later.
- **No cap on one sync's deletions.** One faulty device could empty every workspace it is paired with.

## Consequences

- A memory the user deletes is deleted on every device it reached, through chains of pairings, once each device syncs.
- After a file changes, the other device drops this device's old chunks, so versions stop piling up.
- A device offline for more than 180 days can bring deleted memories back.
- Memories deleted by a device on an older release still return from it, as before, until it updates; it neither sends nor honours deletions.
- A refresh that drops a file gone from disk leaves the memories on paired devices, which send them back. That is the old behaviour, now on purpose, for that case only.
- More than half of a workspace deleted at once on one device is not mirrored; the other devices keep those memories until the user deletes them there.
- The deletion records hold the IDs of deleted memories for 180 days. Since redaction runs before IDs are made (ADR 0060), they hash redacted content, like the IDs in the manifests.

## Invalidation conditions

- If redaction ever stops running before IDs are made, keyed or no deletion records for memories that may hold secrets are needed.
- If devices go offline longer than the retention in practice, lengthen it, or keep deletions until every pairing has synced past them.
- If store-and-forward (issue #32) lets devices that are never online together converge, deletions must travel through it too, and the rule that only keys the other holds are named must hold there.
- If legitimate deletions of more than half a workspace are common, let the user confirm a refused request instead of only reporting it.

## Validation

- Windows 11, Python 3.13 (the installed runtime's venv). `tests/test_sync_deletions.py`, on an in-memory Qdrant, covers:
  - deletion records per workspace, retention and the later time winning;
  - only keys the other holds being named, never one held here again;
  - copies older than the deletion going, a copy made again later staying, and the deletion being recorded with its original time;
  - the cap refusing a request whole;
  - the importer skipping deleted memories;
  - which local deletions are recorded: the user's, a source with all, own or none, and a new version recording only this device's replaced chunks.
- `tests/test_sync_pairs.py`, two devices through a relay, covers a deletion reaching the other device naming only what it holds and not coming back, and a device that may not send being unable to make the other delete.
- All sync tests and the full suite pass.
- Not run: a sync between two real installations, or a chain of three.
