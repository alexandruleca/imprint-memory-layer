---
title: ADR 0185 — Chrome capture discovers local workspaces
description: Use the native loopback trust boundary for Chrome capture and let the user choose a destination for each submission.
---

- Status: Amended
- Date: 2026-11-19
- Amends: [ADR 0029](0029-paired-browser-capture.md) — connection and workspace selection for new Chrome extensions
- Amended by:
  - [ADR 0187](0187-browser-file-capture.md) — bounded binary files through the capture queue
  - [ADR 0188](0188-browser-dashboard-handoff.md) — dashboard search and recording handoff
  - [ADR 0192](0192-the-capture-api-accepts-firefox-extension-origins.md) — the local routes also accept a Firefox extension's UUID and `moz-extension://` origin
- Related: [ADR 0024](0024-enforce-local-service-boundaries.md) and [ADR 0030](0030-chrome-extension-release-zip.md)

## Context

Connecting the previous Chrome extension required a Python command and a token
for each workspace. Signed-in pages already supported explicit text capture.
Users also need to ingest URLs Imprint can fetch directly and choose the
destination as they capture.

## Decision

Add a dedicated credential-free browser API on the native loopback listener.
Require a loopback peer and Host, a Chrome extension ID header, and an exact
match for the Origin on submissions. Chrome may omit Origin on extension GETs.
Expose workspace names and the active default, submission to an explicit
existing workspace, and status for jobs submitted by the same extension ID.
Pin the selected workspace on each job. Use the inline ingestion pipeline
for rendered text and the existing single-URL pipeline for URL submissions.
Keep the prior paired routes available for older clients and queued jobs.

## Rejected alternatives

The earlier one-workspace pairing model required a manual command and token
and made selecting another workspace require a token rotation. Reusing
general dashboard endpoints would expose more authority than browser capture
needs.

## Consequences

The popup needs only a running local service and offers a custom loopback
port setting. Installed extensions with loopback host access can call these
routes, so extension IDs scope job lookups without serving as credentials.
Website origins are rejected. The native peer restriction excludes
connections arriving through container NAT. Text snapshots use the text
rendered in the browser and do not transfer its session credentials.

## Invalidation conditions

- If local extension callers need to be distinguished by trust level, require an extension authentication mechanism with automatic setup.
- If remote capture becomes a supported use, design authentication for that transport before enabling remote peers.

## Validation

The browser capture, HTTP boundary, and ZIP regression suites passed on Linux.
Disposable Chromium loaded the built ZIP and exercised workspace selection,
editable text from a synthetic signed-in page, and URL submission. The popup
was visually checked in both color schemes. The browser smoke uses a fixture
server; API and worker behavior are exercised separately by the Python suite.
Native Windows and macOS execution remains pending. See the
[regression commands](../browser-extension.md#coming-soon-and-validation).
