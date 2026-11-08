---
title: ADR 0152 — Dashboard summaries are served stale while they refresh
description: The Home page reads one per-workspace summary that the API keeps as a snapshot, answers at once and rebuilds in the background after writes, and the dashboard shows the last answer it had while it asks again, kept per workspace in the browser.
---

- Status: Accepted
- Date: 2026-11-08
- Related: [ADR 0063](0063-auto-spawned-local-qdrant-server.md) (the Qdrant server the counts come from), [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md) (the background tagging whose writes kept invalidating the old cache), [ADR 0150](0150-topics-page-on-the-server-and-workspace-copies-are-previewed.md) (other pages moved to server-side paging), [dashboard](../dashboard.md), [API](../api.md)

## Context

The user: "maybe even a cache that updates data in the background when needed so it loads faster — sometimes it takes a bit now with more memories", with about 120,000 memories in a workspace.

The Home page waited for `GET /api/stats` and `GET /api/overview` together and showed a skeleton until both answered. Measured on a synthetic workspace of 120,481 memories in 60 projects (local Qdrant server, Linux, `imprint/api.py` under uvicorn):

| Request | Idle store, first | Idle, repeated | After a write | During simulated background tagging |
|---|---|---|---|---|
| `/api/overview` | 688 ms | 12 ms | 442 ms (max 926) | 854 ms (max 952) |
| `/api/stats` | 180 ms | 28 ms | 21 ms | 52 ms |

`/api/overview` is cached against a fingerprint of the collection's point count and WAL size (`cli_viz._cached`). Learning and background tagging write continuously, so the fingerprint changes every few seconds and almost every load rebuilt the overview: three facets per project (180 for 60 projects) plus an exact union count per project with related members. The shell's own requests (`/api/workspaces`, `/api/queue`, `/api/version`, `/api/update`, `/api/ping`) took 1–8 ms and were not the problem.

The page also misreported: the Topics and Languages tiles counted the top 20 facet values, so a workspace with 400 topics showed 20.

## Decision

The Home page reads one summary, `GET /api/dashboard`, and both the API and the browser serve the last summary while a newer one is prepared.

**On the server** (`imprint/dashboard_summary.py`), each workspace has one snapshot built from facets and counts over payload indexes (totals, project union counts, types, languages, domains, layers, topics and sources with their real counts, memories per calendar month, memories in the last 7 days, the tagging backlog, and the sources of the newest 400 memories). A read whose snapshot matches the workspace's fingerprint (collection, point count, WAL size and modification time), has not been invalidated and is under 60 s old is answered at once. Otherwise one background rebuild starts (at most one per workspace, at most every 2 s), the read waits up to 0.3 s for it, and then answers with the previous snapshot marked `stale`. The first read of a workspace, and `?fresh=1`, build synchronously so errors such as an embedding profile conflict reach the caller. Any accepted `POST`/`PUT`/`PATCH`/`DELETE` to `/api/` and every finished job invalidate the snapshots; a wipe forgets them. Snapshots are keyed by workspace and every build names its workspace.

**In the browser** (`imprint/ui/src/lib/use-cached.ts`), `useCached(key, fetcher, options)` keeps each answer in memory and in `sessionStorage` under the active workspace, shows it immediately, and fetches again in the background on mount, focus, a job starting or ending, an interval, a reported data change (`/api/stream`) or a `stale` answer. Concurrent requests for a key are shared. Nothing is shown until the tab has asked which workspace is active, and an answer naming another workspace is discarded. The page shows "Updating…" instead of a blank page.

## Rejected alternatives

- **Make `/api/overview` and `/api/stats` cheaper in place.** Other pages (Projects, Sources, Topics, the migrate dialog) rely on `/api/overview` reflecting a move as soon as the request that made it returns; serving it stale would break that, and its per-project facets are what those pages need. The Home page needed neither the per-project breakdown nor read-after-write strictness.
- **Rebuild only on the fingerprint (the existing `_cached`).** Measured above: under continuous writes it misses on nearly every read. It also misses payload-only updates that leave the WAL and count unchanged, such as a move, which is why snapshots also age out and are invalidated by dashboard writes.
- **A periodic background recompute for every workspace.** It would count workspaces nobody is looking at; rebuilding on demand when a read finds the snapshot out of date costs nothing while the dashboard is closed.
- **SWR or TanStack Query in the browser.** Either adds a dependency for a few behaviours we need in a specific form: keys scoped by the active workspace, the workspace check before showing stored answers, revalidation on queue events and the server's `stale` flag. The hook is about 300 lines with no dependency.
- **`localStorage` instead of `sessionStorage`.** A summary is a convenience for reloads and navigation within a session; keeping counts of a workspace on disk after the tab closes is not needed.

## Consequences

- With 120,481 memories, `/api/dashboard` answers in 4–6 ms when the snapshot is current or after a write (the stale snapshot, rebuilt behind it in about 450 ms), against 440–950 ms for `/api/overview` after a write; the first build takes about 700 ms cold.
- The Home page can show counts up to one rebuild old (normally under a second, at most 60 s for a change nothing reported), always labelled with the snapshot's age or "Updating…".
- A read after another process's payload-only change (no point count or WAL change) can show the older summary for up to 60 s unless a dashboard write or a finished job invalidated it.
- Other pages can adopt `useCached` with one line; until they do, they read as before.
- The dashboard's other aggregate endpoints are unchanged.

## Invalidation conditions

- If Qdrant exposes a cheap per-collection change counter that covers payload updates, then use it as the fingerprint and drop the 60 s age limit.
- If a rebuild of the summary takes longer than its maximum age on real workspaces, then count incrementally from the writes instead of rebuilding.
- If the dashboard is served to more than one viewer or across machines, then revisit the in-process snapshot and the `sessionStorage` scope.
- If pages need read-after-write guarantees from cached reads, then make `useCached` callers pass `fresh` after their writes or keep those pages on direct reads.

## Validation

- `tests/test_dashboard_summary.py` (8 tests, in-memory Qdrant): counts, snapshot reuse, stale answer during a slow rebuild and the rebuilt answer after it, invalidation and age rebuilding after a payload-only change, per-workspace snapshots, a failed first build raising, a failed rebuild keeping the older snapshot, and the API endpoint plus write invalidation.
- `imprint/ui/tests/home.spec.ts` and `dashboard-recovery.spec.ts` (Chromium, mocked API): the cached summary showing at once on reload with "Updating…" and replaced in the background, a `stale` summary asked for again, another workspace's stored summary never shown, a finished job and the Refresh button fetching again (`?fresh=1`), connection and server error recovery, the embedding profile conflict, and the phone layout.
- Timings above were measured once on Linux (WSL2) against synthetic data with 64-dimensional vectors; real payloads, 1024-dimensional vectors, Windows and macOS were not measured.
