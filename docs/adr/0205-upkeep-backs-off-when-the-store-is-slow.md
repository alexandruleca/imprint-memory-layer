---
title: ADR 0205 — Upkeep backs off when the store is slow
description: Upkeep writes at most 200 points per request, rests up to 30 seconds after a slow piece, and treats a store too busy to answer as a reason to end the run, not a failure, unless it happens three runs in a row.
---

- Status: Accepted
- Date: 2026-11-22
- Amends: [ADR 0201](0201-upkeep-brings-what-is-stored-up-to-date-in-the-idle-slot.md) — the rest after a piece is at most 30 seconds, not 5; writes go in requests of at most 200 points; a timeout ends the run without failing it
- Related: [ADR 0200](0200-synced-memories-keep-the-time-they-were-first-stored.md) (the dates being repaired), [ADR 0204](0204-qdrant-logs-its-own-events-and-its-log-stays-bounded.md) (found in the same investigation), [queue](../queue.md)

## Context

- **The report.** On 2026-11-22 the owner reported that the background job kept failing on `bedrock`, their Linux device. Its queue log showed `Upkeep in default: memories from paired devices dated to when they were first stored`, then `failed: timed out`, then exit 1.
- **What bedrock's logs showed.**
  - Its collection holds 378,716 points, with payload on disk and payload indexes on `timestamp` and on full-text `content`.
  - The step had dated 257,561 memories so far, with a cursor saved part-way.
  - Of six recent runs, two failed this way, two used their 10-minute share, and two were stopped by other jobs.
- **Why the writes slowed.** The step sent each page of 1,000 points as one `points/batch?wait=true` request. Almost every point gets its own timestamp, so the request carries about 1,000 separate payload updates. Within one run Qdrant took 4.8, 5.6, 9.0, 10.9, 16.6 and then 38.5 seconds for these requests. The 38.5-second one finished at 18:40:46 UTC. The client's 30-second timeout had already raised at about 18:40:38, when the job ended. Updating a point in an optimized segment makes Qdrant copy it, with its payload, and index it again.
- **The pacing did not keep up.** ADR 0201 rests after a piece as long as it took, at most 5 seconds. A 38-second piece therefore rested 5 seconds, so upkeep took most of the store's time, not about half. A timeout counted as a failed step, so the job exited 1, although Qdrant completed the write.

## Decision

- **Smaller writes.** The timestamp step writes at most 200 points per request (`_WRITE`). Between requests it may step aside; the page is read again from its start, and points already dated are passed over.
- **A longer rest.** The rest after a piece is still as long as the piece took, but capped at 30 seconds (`REST_MAX`) instead of 5. A queued job still ends the rest within half a second.
- **A busy store ends the run.** When a step fails because the store did not answer in time (the client's read timeout, however qdrant-client wraps it), upkeep keeps what the ledger already holds. It prints that the store was too busy and ends this run, returning success. The step's ledger entry counts such runs (`timeouts`). The third in a row fails as before, so a step that can never finish shows up. A run that gets through clears the count.
- **Other errors fail at once,** as before.

## Rejected alternatives

- **A longer client timeout.** It hides a slow store from every caller and lets upkeep hold the store longer. The client is shared, and searches should not wait 2 minutes.
- **`wait=false` writes.** Qdrant acknowledges a write before applying it, so nothing would slow upkeep down while the store's update queue grew.
- **Leaving the failure as it was.** The step still advances each run. But the Queue page shows a failed job several times a day, and the store stays saturated while it runs.

## Consequences

- The timestamp step makes about five times as many requests, each a fifth the size. On a fast store the total time barely changes.
- On a slow store upkeep takes longer overall, but leaves about half the store's time to everything else, as ADR 0201 meant.
- A run that ends on a timeout shows as a successful job whose log names the busy store. Only three in a row show as failed.

## Invalidation conditions

- If Qdrant gains a bulk update that sets a different value per point in one operation, use it and revisit the request size.
- If bedrock-sized stores still time out with 200-point requests, make the request size follow how long the previous request took.

## Validation

- `tests/test_upkeep.py` added three tests to the existing ones, against an in-memory Qdrant. They cover requests of at most `_WRITE` points, stepping aside between writes and passing over what was dated, and a store raising qdrant-client's wrapped `httpx.ReadTimeout`. That last test checks two runs ending without failing, the third failing, and a run that gets through clearing the count; another error still fails at once. All 24 tests in the file passed on Windows (Python 3.13, the install's venv, from a native copy).
- The timing evidence comes from bedrock's `qdrant.log` and queue logs for 18:33–18:41 UTC on 2026-11-22.
- Not run: the change on bedrock itself. It applies after `imprint update` there.
