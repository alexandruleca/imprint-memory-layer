---
title: ADR 0008 — Dashboard retrieval and accessibility
description: Same-origin API access, explicit search states, provenance and native modal behavior.
---

- Status: Amended
- Date: 2026-10-23
- Amended by: [ADR 0039](0039-shadcn-overlays-and-desktop-drag-handles.md) — the native dialog primitive is replaced by the shadcn (Base UI) `Dialog` and `AlertDialog`
- Restructured: 2026-10-25

## Context

Hard-coded API ports broke relocated dashboards. Search showed semantic scores without a useful relevance explanation, lacked strong failure states and could display stale async responses. Dialogs did not reliably trap/restore focus. Small viewports lost useful space to fixed navigation.

## Decision

Default dashboard calls to same-origin relative URLs, with an explicit build-time override for development. Surface server errors. Present search idle/loading/empty/error/result states, retry, project/type filters and hybrid/semantic mode. Cancel superseded search/detail requests. Explain semantic scores as ranking signals and show source hash/mtime when available. Full-memory actions resolve public IDs through the shared backend.

Use a native HTML dialog primitive for memory and destructive confirmation dialogs: labelled modal, focus trap, Escape, inert background and focus restoration. Reset confirmation input on reopening. Keep asynchronous modal triggers focusable while busy. Provide a skip link, navigation labels/current page, compact mobile sidebar, responsive layouts, keyboard search focus and a visible reconnectable service banner.

Clear existing lint errors through lifecycle/derived-state fixes, then gate lint, production build and Playwright Chromium behavior in CI. Do not suppress effect rules globally.

## Rejected alternatives

An unrelated visual redesign would not resolve correctness and interaction defects. A custom focus trap adds maintenance that the native browser primitive already provides. Treating empty results and service errors identically hides actionable problems.

## Consequences

Native dialog behavior depends on the supported Electron/browser baseline. Search offers project entry rather than a complete project picker. Keyboard, contrast, screen-reader, graph and long-running ingest UX need broader user testing; these changes are not a claim of complete WCAG conformance.

## Invalidation conditions

- If a supported deployment serves the dashboard from a different origin than its API, same-origin relative URLs no longer work without the build-time override.
- If search gains a calibrated relevance measure, the explanation of semantic scores as ranking signals must change with it.

## Validation

Production export/TypeScript, clean lint, and browser scenarios for mobile overflow, filters, empty results, errors/retry, stale request cancellation, full-memory dialog/Escape/focus, keyboard shortcut and reconnect. Native Electron graphical tests remain pending.

Dialog follow-up: [ADR 0039](0039-shadcn-overlays-and-desktop-drag-handles.md) replaces the native dialog primitive with the shadcn (Base UI) `Dialog` and `AlertDialog`, which keep the labelled modal, focus trap, Escape, inert background and focus restoration required above.
