---
title: ADR 0188 — Chrome hands search and meeting setup to the dashboard
description: Open scoped searches and prefilled recording forms in the local dashboard from the Chrome popup.
---

- Status: Accepted
- Date: 2026-11-19
- Amends:
  - [ADR 0185](0185-local-chrome-capture.md) — the Chrome popup can open the dashboard for workspace search and meeting recording
  - [ADR 0131](0131-meetings-are-recorded-by-channel-and-transcribed-locally.md) — recording setup can start from the Chrome toolbar with a suggested title and explicit transcript workspace

## Context

Chrome users want to look up memories and start recording Google Meet or
Microsoft Teams calls. The local dashboard already has the full reader,
recording consent, persistent capture controls and audio transcription.

## Decision

Use URLs on the configured local dashboard to hand off a search query or
meeting setup. Put the query, suggested title and destination in the fragment.
Use the selected workspace for search results, memory details and source
reading without switching the dashboard's active workspace. User-initiated
recording still requires confirmation and the browser's interactive capture
permissions. Persist the workspace explicitly selected for a new recording so
transcription and later retries can find the intended destination.

## Rejected alternatives

Returning workspace memories directly to the unpaired extension API would
expand its read authority. Capturing audio in the popup would tie call
recording to a surface that closes on blur and duplicate the dashboard recorder.

## Consequences

Search results and recording controls live in a separate dashboard tab. A
browser meeting title is a suggestion and might contain only the service name
or meeting code. The user chooses the call's audio source in Chrome. Chrome
does not acquire background audio permissions as part of extension installation.

## Invalidation conditions

- If search results must be shown in the popup, design a read authorization
  mechanism before exposing them through the extension API.
- If Chrome must capture the call tab without a sharing picker, add an
  explicitly permissioned recording surface that can outlive the popup.

## Validation

On Linux the packaged extension smoke verified search and recording handoff
URLs. The dashboard tests passed scoped search and memory reads, and prefilled
recording setup with consent and synthetic audio. The Python recording suite
verified that transcription preserves the chosen workspace. Live Meet and
Teams calls and native Windows/macOS execution were untested.
