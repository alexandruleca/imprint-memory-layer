---
title: ADR 0187 — Chrome sends explicit file captures to the ingestion queue
description: Bound uploaded browser files and reuse the native extractors and OCR in the selected workspace.
---

- Status: Amended
- Date: 2026-11-19
- Amends: [ADR 0185](0185-local-chrome-capture.md) — adds bounded binary file submission to the capture API
- Amended by:
  - [ADR 0189](0189-captured-files-are-kept-only-while-a-job-can-use-them.md) — a stored file is deleted once its capture is indexed, or a week after it failed
  - [ADR 0191](0191-chrome-full-page-screenshots-are-stitched-in-the-popup.md) — a screenshot can cover the whole page, not only the visible tab
- Related: [ADR 0076](0076-default-ocr-with-wheel-installed-models-and-renderer.md)

## Context

Private PDFs, attachments, and Google documents may require a Chrome session.
Some contain image text that DOM capture cannot read. Imprint already extracts
supported file types and runs OCR locally.

## Decision

Accept an explicit binary upload on the existing loopback extension boundary.
Require an existing workspace, a safe supported filename, and a byte limit of
25 MiB or the configured document limit, whichever is smaller. Persist the
bytes, keyed by SHA-256, with the capture job in one SQLite transaction. Set
the queue database to owner-only on POSIX. Workers materialize a file with a
fixed temporary filename and the supported suffix, run the existing extractor
under the ingestion lock, and index the text with browser provenance.

Use Chrome's open session for same-origin file and Google Docs/Sheets export
requests. Offer file selection for downloads and a preview of the visible tab
for image OCR. Request no permanent site access beyond the Imprint listener.

## Rejected alternatives

Server-side URL refetch alone cannot use the browser's authenticated session.
Putting base64 bytes in job JSON makes the job history and status machinery
decode large attachments even when only status or metadata is needed.

## Consequences

Files persist locally in the queue for retries. Uploading the same bytes
again reuses the stored blob. Google exports and tab file access depend on
browser and provider permissions, so some captures require a download. OCR
uses the existing configuration and runtime. Browser captures inherit the
workspace's normal indexing and sync behavior.

## Invalidation conditions

- If supported file sizes grow beyond the bounded upload, replace the
  in-memory receipt and worker read with a streamed, private spool.
- If queue retention changes, collect file blobs with no remaining job
  references.

## Validation

Browser API and worker regression tests cover bounded binary streaming, origin
checks, retry through the stored blob, workspace pinning, and spreadsheet
extraction. On Linux, the packaged Chromium smoke exercised signed-in file
capture, the upload picker, and the screenshot preview with a fixture image.
The worker extracted a real DOCX and ran local OCR on a generated image and
scanned PDF. Google exports were exercised with synthetic sessions.
Native Windows and macOS, and live Google accounts, were untested.
