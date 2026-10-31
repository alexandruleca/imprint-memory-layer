---
title: ADR 0123 — A paired sender prepares the next chunk while the other device stores the last
description: During a paired sync the sender reads and filters chunk n+1 in a thread while the other device stores chunk n, one chunk ahead at most, with the frames and their order unchanged.
---

- Status: Accepted
- Date: 2026-10-31
- Amends: [ADR 0122](0122-the-sending-device-filters-on-its-gpu.md) — the sender no longer waits for a chunk's acknowledgement before filtering the next
- Related: [ADR 0116](0116-paired-devices-send-only-what-the-other-lacks-in-stored-chunks.md) (chunks stored and acknowledged one at a time, unchanged), [ADR 0120](0120-a-device-storing-a-chunk-says-so.md)

## Context

Since ADR 0122 a device whose Privacy Filter runs on a GPU filters the memories it sends. `sync_delta._send` read and filtered a chunk, sent it, then waited for the other device's storing and ack frames before reading the next one, so a paired sync took the sender's filtering plus the receiver's storing for every chunk. On bedrock the CUDA sender filtered about 0.15 s a memory and a CPU receiver stored marked memories at about 0.07 s, an estimated 3.6 hours for a first sync of 58,563 memories where overlapping the two would take about 2.4. ADR 0122 recorded that this overlap was not done.

## Decision

`sync_delta._send` starts reading and filtering the next chunk (`export_chunks`, in a worker thread) as soon as it has sent the current one, and only then waits for that chunk's storing and ack frames. At most one chunk is prepared ahead. Frames, their order, sequence numbers and acknowledgements are those of the previous sender, so a peer on an older release sees no difference.

When the send ends early (the task is cancelled, the session's cancel event is set, the other device refuses a chunk, or reading fails), the prepared chunk is dropped unsent and the send waits for its thread. A worker thread cannot be interrupted, so `export_chunks` takes a stop event and checks it between slices of 25 records of the Privacy Filter's pass, raising `Stopped`; no thread outlives the send. The wait for the next chunk is shielded from task cancellation, so cancelling never leaves the thread running unawaited, and it also ends when the cancel event is set. An error preparing a chunk surfaces when that chunk is due, after the previous chunk's acknowledgement.

A full sync (`_send_export`) is not changed: its receiver buffers the whole stream and stores it after the last frame, so there is no per-chunk store to overlap; its sender already sends each batch as it is made (ADR 0122).

## Rejected alternatives

- **Preparing more than one chunk ahead.** The sender is the slower side when it filters; more chunks ahead only hold more vectors (about 4 MB a chunk) in memory.
- **Sending the next chunk before the acknowledgement.** It would change the protocol ADR 0116 defines and older peers expect.

## Consequences

- Measured on bedrock with 200 memories in 8 chunks of 25 (the steady state of a long sync), a CUDA sender and a CPU receiver as separate processes over a local socket, each with its own data directory and Qdrant, pinned to disjoint physical cores with 4 threads each: 61.3 and 65.6 s before, 57.2 and 55.4 s after, about 10% less. Unpinned on the same cores, both took 49–56 s, because the receiver's storing competed with the sender's filter, whose expert layers run on the CPU (ADR 0122).
- The saving is at most the smaller side's share. Here the sender's filter took 6–9 s a chunk and the receiver's store about 2 s, and the first chunk's store also creates the collection, so a sync costs about the sender's filtering time. The 2.4-hour estimate assumed 0.07 s against 0.15 s a memory; the measured gain is smaller.
- A cancelled send may take up to one slice of the Privacy Filter (25 memories, a few seconds) to end.

## Invalidation conditions

- If the receiver becomes the slower side (a fast sender, a slow receiver disk), then preparing one chunk ahead is enough and nothing changes; if the protocol allows several chunks in flight, then preparing more becomes useful.
- If ADR 0116's acknowledgement per chunk changes, then this overlap must be redesigned with it.

## Validation

- `tests/test_sync_pipeline.py`, with a fake receiver and a fake slow filter: chunk n+1 is prepared before chunk n is acknowledged, at most one chunk ahead, and four chunks of 0.4 s filtering and 0.4 s storing take less than 80% of their sum (through `slack()`); an older peer without storing frames receives the same frames in the same order and an unmarked stream; cancelling the task mid-prefetch, setting the cancel event and a refused chunk each stop the preparing thread and send nothing more; an error preparing the next chunk fails the send after the last acknowledgement; a stop ends `export_chunks` at the next slice. The same tests fail against the previous `_send`.
- The sync test modules and the full Python suite pass.
- The measurements above; not measured between two machines.
