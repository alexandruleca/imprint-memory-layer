---
title: ADR 0022 — Retain HTTP clients during model work
description: Remove the embedded-era idle closer that broke slow native installer writes.
---

- Status: Accepted
- Date: 2026-10-23
- Related: ADR 0004, ADR 0007, ADR 0012

## Context

Release run 39969601759 built the Intel macOS package but failed its native
runtime smoke: `Cannot send a request, as the client has been closed`. The
storage operation retained a client, then loaded/ran the embedding model. A
background timer interpreted the inference interval as inactivity and closed
that client before the durable upsert. A local isolated regression reproduces
the same failure with delayed model work.

The timer predates the current HTTP-only Qdrant connection and existed to release
an embedded database's exclusive filesystem lock. HTTP clients have no such
lock. Increasing the idle deadline merely moves the race to slower hardware,
larger batches or cold downloads.

## Decision

Retain the per-process HTTP client until explicit cleanup or interpreter exit.
Remove the MCP idle watcher. Keep `release_idle_client` as a documented no-op for
external callers; its duration argument and legacy `IMPRINT_MCP_IDLE_S` no longer
control client expiration. Continue closing the HTTP client at interpreter exit.
Do not stop the shared Qdrant daemon when a client disconnects.

## Alternatives

A longer deadline remains unsafe. Reacquiring a client only in single-note
store misses batch writes, searches and other retained references. Operation
leases could make an idle closer safe but introduce bookkeeping and nested-call
complexity to recover a lock that the current runtime never holds. Disabling
the native installer smoke would hide a real application defect.

## Consequences

Idle processes retain their HTTP connection pool. Slow embedding no longer
invalidates a retained client. This changes neither ranking, vectors, embedding
identity nor MCP response formatting, and requires no reindex. Benchmark evidence
retains the actual pre-fix runtime revision; no new performance claim is inferred
from the lifecycle correction.

## Validation

The isolated regression fails before the fix and exercises slow single-note
store, batch store and semantic search after it, then verifies explicit cleanup.
Full unit/native checks and package gates are recorded in platform validation.
The failed Intel package must be rebuilt from corrected source; retrying the old
artifact is not validation of this fix. Interactive desktop testing remains
separate from the automated installer/runtime smoke.
