---
title: ADR 0120 — A device storing a synced chunk says so
description: While a paired device stores a chunk it sends a storing frame every 30 seconds, so a store slower than the 10-minute wait no longer fails the sync, and both devices show "storing" instead of "comparing".
---

- Status: Accepted
- Date: 2026-10-31
- Amends: [ADR 0116](0116-paired-devices-send-only-what-the-other-lacks-in-stored-chunks.md) — what counts as progress while a chunk is stored
- Related: [ADR 0060](0060-redact-secrets-before-memories-are-stored.md) (received records are redacted again, with Privacy Filter when it is on), [sync](../sync.md#automatic-sync-between-paired-devices)

## Context

After the update to v0.36.0 the owner paired the Ubuntu machine (58,563 memories) with the Mac again. The sync "did nothing for 10 minutes", showing *comparing*, then failed with *The sync made no progress for 10 minutes*, and every later attempt did the same. Byte counts on the Ubuntu side's relay connection during one attempt (2026-10-31 07:45) showed the manifests swapped and the first 200-memory chunk (3.7 MB) sent within 10 seconds, then nothing but keepalive pings: the Mac never acknowledged the chunk.

Storing that chunk, exported from the Ubuntu workspace and imported into a scratch data directory on the same machine, took 259 s. Nearly all of it was `redact.privacy_filter`: every received record is redacted again before it is stored (ADR 0060), and Privacy Filter runs a token classifier on the CPU, about 0.94 s for a memory of 2,600 characters on that machine under load. On the Mac the chunk took longer than `sync_delta.WAIT` (600 s). The sending device gave up waiting for the acknowledgement; the storing one gave up on `ATTEMPT_LIMIT`, because `_receive` reported nothing while it stored. Each next attempt sent the same first chunk, since the Mac had stored none, so the sync could never advance. The paired-device list showed *comparing* throughout, because the stages a receiving device reports had no stage for storing.

A second, older gap: a one-time sync's receiving side waited for the next frame of a stream without a limit, so a peer that stopped mid-stream left the session open indefinitely. The Ubuntu dashboard still held one such session, from the 07:12 pairing, with an empty receive file 30 minutes later.

## Decision

- **A store reports that it is alive.** `sync_ws._store` yields a `storing` status every `STORE_HEARTBEAT` (30 s) without a progress event. In a paired sync the receiving device yields it, which restarts its own `ATTEMPT_LIMIT`, and sends `{"kind": "storing", "seq": n}` for the chunk it is storing. The sending device, waiting for that chunk's acknowledgement, takes the frame as progress and waits `WAIT` again. A store is therefore bounded by its own progress, not by 10 minutes; a store that hangs without its thread advancing still sends heartbeats, which is the same trade-off as a slow one, and the user can pause the pairing.
- **Only to devices that take them.** 0.36.0 treats any frame other than the acknowledgement as a failed store. Each device advertises `storing: true` in the paired exchange (`sync_delta.storing_offered`), and a device sends `storing` frames only to one that advertised it. Between 0.36.0 and this release the old limits apply.
- **The stage says storing.** The receiving device reports `storing` as soon as a chunk has arrived, and the sending device reports it while the other side stores, so the paired-device list shows *Storing what arrived* instead of *comparing*.
- **A stream that stops fails.** `_receive_stream` waits at most `STREAM_WAIT` (600 s) for each frame and then fails with *The other device sent nothing for 10 minutes*, which the one-time sync paths already report as an error.

## Rejected alternatives

- **Smaller chunks.** With Privacy Filter at about a second a memory, 50 memories still take a minute on a fast machine and more on a slow or busy one; any fixed size can outlast a fixed wait. Heartbeats make the chunk size irrelevant to liveness.
- **A longer `WAIT`.** It would only move the limit, and it also delays noticing a device that really went away.
- **Skipping redaction for records from a paired device.** ADR 0060 redacts every write whatever the sender did, so a device with stricter settings never stores a looser copy. Changing that is a separate decision.

## Consequences

- A first sync with Privacy Filter on completes, at the speed the receiving device can redact: about a second a memory on the Ubuntu machine's CPU, so its 58,563 memories take hours to store on the other device. Making Privacy Filter faster, or deciding when it needs to run on received records, is left open.
- Two devices on this release show *storing* while a chunk is stored, on both sides.
- A device on 0.36.0 paired with one on this release still fails on slow stores until it is updated.

## Invalidation conditions

- If received records are no longer redacted again, or redaction gets fast enough that a chunk always stores within `WAIT`, then the heartbeat is only a safety net and could go.
- If a store can block without its thread making progress (for example on a lock), then the heartbeat should come from the import's progress, not from a timer, or a hung store would hold the sync open.

## Validation

- `tests/test_sync_pairs.py`: a store that takes 2.5 times the attempt limit and the wait finishes on both devices, with `storing` frames sent by each; a device that does not advertise `storing` receives none while the other stores; the receiving device's stage while storing is now `storing`. The existing test of an attempt past its limit, whose store sends no heartbeat within the limit, still fails as before.
- The new slow-store test fails without this change.
- `tests/test_sync_delta.py`: a stream whose sender goes silent fails with *sent nothing* after `STREAM_WAIT`.
- All 90 sync tests pass on Linux.
- **Not verified:** a real sync between the two devices on this release.
