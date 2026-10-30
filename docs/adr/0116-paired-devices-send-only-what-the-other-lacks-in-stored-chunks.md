---
title: ADR 0116 — Paired devices send only what the other lacks, in stored chunks
description: Automatic sync swaps vector-free manifests and sends only missing or newer records, as small streams each stored and acknowledged before the next, so large workspaces sync and interrupted syncs resume.
---

- Status: Accepted
- Date: 2026-10-30
- Amends: [ADR 0020](0020-explicit-pairing-and-automatic-workspace-sync.md) — what an automatic attempt compares and transfers, and how long it may run
- Related: [ADR 0018](0018-single-sync-implementation-and-end-to-end-encryption.md) (the encrypted session and stream format, unchanged), [ADR 0051](0051-synced-tags-follow-the-latest-retag.md) (which classification wins), [ADR 0081](0081-verifiable-redacted-source-snapshots.md) (snapshot transfer frames), [sync](../sync.md#automatic-sync-between-paired-devices)

## Context

Two paired devices on v0.34.0, an Ubuntu machine and a Mac with over 50,000 memories, never synced: the Mac's pairing had no completed sync, and the Ubuntu one showed a sync but never checkpointed. Reading the code and watching an attempt on 2026-10-30 found four causes, all in the automatic path of ADR 0020:

1. **Snapshot frames were refused.** Since source snapshots were added (`c356924`, 2026-10-26), every export stream carries `source_snapshot` frames after its batches, but the receiver's `_stream_line_ok` accepted only `meta`, `batch` and `done`, so receiving any workspace with snapshots failed with "unexpected frame in data stream". The Ubuntu workspace has 66.
2. **The change check read every vector and waited 60 s.** `sync_pairs.digest` hashed a full export, vectors included (22 s for the Ubuntu device's 6,495 memories, a 129 MB export), and `exchange` waited 60 s for the other device's digest. The Mac's, over 50,000 memories, did not arrive in time; the timeout carried no message, so `last_error` stayed empty. A recording of one attempt showed the pairing exchange at 07:26:04, a few hundred bytes, and the connection gone about a minute later.
3. **A transfer was all or nothing within 10 minutes.** Each attempt had a total limit of `ATTEMPT_LIMIT` (600 s), the receiver kept the whole stream in a file and stored it only at the end, and the paired path passes no resumable buffer, so a cut-off attempt kept nothing and the next one sent the whole workspace again.
4. **A disconnect counted as success on the serving side.** `serve_session` turned `ConnectionClosed` into `done`, so when the joining device gave up, the serving one recorded `last_sync` without the digests (they are set only after the exchange), which is the state the Ubuntu device showed.

The owner asked to "optimize the sync mechanism to only report new things and changes, and if there is more to report then we should chunk the changes to manageable pieces".

## Decision

Automatic sync between paired devices that both speak this version (`sync_delta.VERSION`, advertised as `delta` in the paired exchange) works as follows ([`imprint/sync_delta.py`](../../imprint/sync_delta.py)):

- **Manifest.** Each device lists, without reading vectors, what sync can change: each memory by ID with its classification stamp `tagged_at` (ADR 0051), each fact by subject, predicate, object and `valid_from`, each source snapshot by project and source with its update time. Fields the importer never overwrites (content, provenance, timestamps of an existing memory, a fact's `ended`) are left out, so records that differ only there are not sent again and again. The digest that decides whether anything changed is a hash of the manifest.
- **Only what the other lacks.** The devices swap manifests (compressed, inside the encrypted channel) and each sends the records the other does not hold or holds an older version of: missing keys, a newer `tagged_at` or snapshot update time. Equal or older versions are not sent, so both sides converge and then stay quiet.
- **Stored chunks.** Records travel as complete small streams in the existing format (meta, batch, snapshot parts, done): up to 200 memories with vectors (about 3.7 MB), up to 200 facts, or one source snapshot. The receiver stores each and acknowledges it before the next is sent; the serving device's records go first, then the joining device's. An interrupted sync keeps every stored chunk, and the next attempt sends only the rest.
- **Progress, not a total time, bounds an attempt.** `ATTEMPT_LIMIT` now applies to time without progress: every event restarts it, and each wait for the other device (its manifest, its next chunk, its acknowledgement) allows `sync_delta.WAIT`, 10 minutes. A large first sync runs as long as chunks keep arriving.
- **Only a finished sync counts.** The serving device reports a disconnect before the exchange finished as an error, *The other device disconnected before the sync finished*, and a wait that runs out says *The other device stopped answering* instead of an empty message.

The receiver also accepts `source_snapshot` frames in every stream, fixing cause 1 for one-time syncs and older peers too.

## Rejected alternatives

- **A bucket tree of digests (exchange bucket hashes, then the keys of differing buckets).** It saves sending the whole manifest, but the manifest of 6,495 memories and 66 snapshots encodes to 159 kB, so 50,000 memories come to about 1.2 MB, well within one frame; the extra round trips and code were not worth it.
- **Hashing every exported field per record.** The importer keeps an existing memory's content, provenance and timestamps and only takes a newer classification, so records stored independently on both devices with different timestamps would differ forever and be sent every minute.
- **Raising the total time limit.** A larger limit would still lose everything when an attempt is cut off, and still be too short for a slow link or a larger workspace.
- **Keeping the whole-stream buffer and resuming it**, as the one-time CLI does. The paired worker reopens the room every minute; a buffer would still require receiving the whole workspace once before anything is stored.

## Consequences

- A paired sync moves only new or newer records; an unchanged workspace exchanges manifests (about 1 s to build for 6,495 memories) and a digest, where it previously exported every vector each minute.
- Large first syncs complete in chunks and survive interruptions; progress shows per chunk.
- Both devices need this version for the delta path. A device that does not advertise it gets and sends whole workspaces as before, so mixed versions still sync, with the old limits.
- Deletions still do not propagate, as before.
- Facts are compared by their stored text. Two devices that redacted the same fact differently would each send it every attempt; the importer keeps the more redacted version, so the cost is a re-sent fact, not divergence.
- The first attempt after the update finds the stored digests changed (the digest is now the manifest's) and reconciles once.

## Invalidation conditions

- If manifests grow past a few tens of MB (several hundred thousand memories), then exchange them by buckets instead of whole.
- If the importer starts updating more fields of an existing record, then those fields join the manifest version, or they will never sync.
- If deletions are to propagate, then the manifest needs tombstones and this comparison must change.

## Validation

- `tests/test_sync_delta.py`: which records are sent (missing, newer, not older or equal, so no ping-pong), digest order independence, manifest encoding and its refusal of malformed or oversized input, chunks as complete valid streams of bounded size (memories with vectors, facts, one snapshot each), the manifest reading no vectors, and `source_snapshot` frames passing the stream check.
- `tests/test_sync_pairs.py`, through a local relay with two isolated devices: only the lacking side's record is sent and no whole-workspace export happens; an interrupted sync keeps the stored chunk and records no sync or checkpoint on either side; the serving device reports a joining device that left mid-sync instead of a success; a device without the delta path still gets and sends whole workspaces. The existing pairing tests now run through the delta path.
- Mutations confirmed the tests bite: restoring the disconnect-as-success handling fails the serving-device test, and refusing snapshot frames again fails both stream tests.
- On the Ubuntu device's real workspace (read-only): the manifest of 6,495 memories and 66 snapshots took 1.1 s and encodes to 159 kB; chunks of 200 real memories carry 1024-dimension vectors in 3.7 MB, and snapshot chunks pass the stream check.
- All 85 sync tests pass on Linux.
- **Not verified:** a real sync between the two devices, which needs this version on both; the 50,000-memory Mac's manifest time and first transfer.
