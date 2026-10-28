---
title: ADR 0105 — Project analysis continues after model timeouts
description: A per-source local model timeout preserves identifier suggestions and lets the remaining project association scan proceed.
---

- Status: Accepted
- Date: 2026-10-28
- Amends: [ADR 0099](0099-sources-can-belong-to-several-projects.md) — timeout failure handling
- Related: [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md), [ADR 0089](0089-one-progress-record-for-the-running-job.md)

## Context

A reported scan of 173 conversations stopped after six sources when the local helper exhausted its 120-second source budget. The exception escaped before saving identifier matches for that source and prevented analysis of all later sources.

## Decision

Keep the existing local inference budget. Catch TimeoutError at the source analysis boundary, report the source and incomplete model analysis in the job log, persist identifier matches and continue scanning. Preserve existing same-version model suggestions. Do not cache the timed-out source as successfully analyzed, so another model analysis retries it. Other helper errors still fail visibly.

## Rejected alternatives

- Remove the time limit: one large source could occupy the shared queue indefinitely.
- Abort the entire scan on timeout: one slow conversation prevents independent sources from receiving suggestions.

## Consequences

A completed queue job can contain warnings for incomplete model analysis. It does not promise full model coverage. Identifier suggestions and manual decisions remain available. Retrying a large source may time out again; identifier-only analysis or manual linking remains available. The scan reports source counts through the existing progress record.

## Invalidation conditions

If repeated timeouts prevent useful coverage on supported hardware, revisit section checkpointing or explicit resource controls while retaining bounded inference.

## Validation

On macOS ARM64, all 14 project association tests and seven ADR structure tests passed. Timeout regressions use mocked inference and disposable storage to check continuation, identifier matches, retention of existing model suggestions and retry eligibility. The dashboard production build passed; 11 Playwright tests passed, including navigation and explicit cancellation before and after queue submission returns. Native model performance and Windows/Linux execution remain unverified for this change.
