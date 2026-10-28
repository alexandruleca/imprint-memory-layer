---
title: ADR 0109 — Automatic OCR runtime repair
description: Retry missing bundled OCR dependencies after one noninteractive bootstrap attempt per process.
---

- Status: Accepted
- Date: 2026-10-28
- Amends: [ADR 0076](0076-default-ocr-with-wheel-installed-models-and-renderer.md) — automatically repair missing bundled OCR dependencies

## Context

Ingestion reported missing RapidOCR and asked the user to run bootstrap for each affected image. The user requested that Imprint try that repair itself once before failing.

## Decision

The managed ingest, refresh, ingest-url and refresh-urls commands opt their extraction workers into a dedicated repair request. File and MIME extraction signal a missing bundled image, PDF-renderer or OCR import with exit code 75. The Go CLI waits for the worker to exit, releases its queue lock, runs its own executable with `bootstrap --non-interactive`, reacquires the queue lock, then retries the original worker once in a fresh interpreter with repair disabled. This avoids waiting for a lock held by the requesting job and avoids reusing loaded native modules. Direct Python callers report the original missing-runtime exception.

Bootstrap has no interactive input, a ten-minute timeout and output on stderr. It uses the existing runtime and queue locking, installation policy and profile; it does not register client profiles. The one-attempt limit covers the entire managed command, including bootstrap failure and a retry whose imports remain missing.

Explicit OCR opt-outs, custom Tesseract requirements and document parsing failures do not trigger installation. Failed bootstrap remains a visible extraction failure with manual recovery instructions. A successful bootstrap that leaves imports unavailable also remains a failure. Existing source retention on extraction failure is unchanged.

## Rejected alternatives

Requiring manual bootstrap for every missing-runtime error was rejected because the user explicitly requested automatic recovery. Retrying installation for every image was rejected because one damaged environment could repeatedly provision dependencies during a large ingest.

## Consequences

The first affected document may wait for installation and network downloads. The retried run reuses already-stored sources and embeddings through normal fingerprint checks. Another queued job may run while the original command releases its lock. Bootstrap may restart the dashboard under its existing runtime policy. Independent commands may each attempt repair; there is no persistent machine-wide cooldown.

## Invalidation conditions

If repair requests can arise from non-managed ingestion entry points, add an owning process that can release their locks and restart their worker safely. If independent commands repeatedly repair the same environment, add an installation-wide cooldown.

## Validation

Python tests cover repair signaling, disabled retries, opt-outs, parse failures and no-text success. Go subprocess tests cover queue-lock release before bootstrap, a fresh worker after repair, a bounded retry and failed bootstrap. Actual package installation during ingestion is not exercised against contributor profiles. Native Windows and Linux execution remain unverified.
