---
title: UI and UX review
description: Dashboard interaction findings, implemented changes and the next product improvements.
---

## Product goal

A user should understand whether Imprint is connected, what was indexed, how fresh a result is, and what an action will change. The primary journey is connect → select sources → ingest → search/inspect → maintain/delete. A graph is useful exploration, but first successful retrieval is the more important onboarding outcome.

## Implemented improvements

| Finding | Result |
| --- | --- |
| Fixed backend port in dashboard helpers | Same-origin API access works with another local port; explicit build-time override remains. |
| Empty and failed searches looked alike | Idle, loading, no matches and server failure have distinct messages; users can retry. |
| Older async work could overwrite a new search | Superseded search/detail requests are cancelled and guarded. |
| Limited retrieval control | Project/type filters and hybrid/semantic mode; first result selected for inspection. |
| Similarity presented without its limits | Score explanation, match type and available source hash/mtime support source verification. |
| Full-memory ID lookup failed for public logical IDs | Shared lookup accepts public IDs and legacy UUIDs. |
| Memory/destructive dialogs lacked dependable focus behavior | Native modal, labels, Escape, focus trap/restoration and confirmation reset on reopen. |
| Connection failure was hidden | Visible service banner with retry and bounded health requests. |
| Navigation consumed narrow screens | Compact mobile sidebar, labels/current page, responsive search/stats and skip link. |
| Existing React lifecycle lint failures | Derived state, keyed request results and deliberate client hydration replace synchronous effect resets. Lint is a CI gate. |

Browser checks exercise mobile overflow, empty/error/retry, filters, stale responses, full-memory modal and reconnection. They mock API responses to test interaction deterministically; backend/native-model smoke provides separate retrieval evidence. This is not a full accessibility certification or native desktop GUI test.

## Next improvements

| Priority | Improvement | Acceptance criterion |
| --- | --- | --- |
| P1 | Guided onboarding and integration health | New install can select one source, see a completed job, confirm a host MCP connection and run a known query. No invisible model download. |
| P1 | Freshness and queue progress | Show last successful source refresh, retained stale chunks after failure, extractor/model download phase, bytes/progress and actionable retry. |
| P1 | Workspace clarity | Persistent visible workspace in every read/write flow; warn when switching with queued jobs; show each job's captured workspace. |
| P1 | Destructive action review | Screen-reader/keyboard walkthrough for wipe, project/source deletion, migration and workspace removal; cancellation and error recovery retain context. |
| P1 | Accessible graph alternative | Searchable list/table exposing the same projects/topics/sources without relying on canvas, color or pointer interaction. |
| P2 | Search refinement | Project picker, source filter, result pagination/diversity and provenance-to-live-source comparison; preserve query/filter state in navigation. |
| P2 | Consistent async UX | Apply visible loading/empty/error/retry conventions to all pages; graph topic errors currently need richer distinction from no data. |
| P2 | Resource and privacy controls | Show selected paths/exclusions, model disk budget, optional remote-provider use and retention controls before work starts. |
| P2 | Desktop polish | Native first-launch, tray, resize, zoom, reduced motion, light/dark contrast and high-DPI checks on supported OS targets. |

Measure time to first result, ingest recovery success, error discoverability and keyboard task completion. Test with both first-time users and developers with large existing workspaces. See [ADR 0008](adr/0008-dashboard-retrieval-and-accessibility.md).
