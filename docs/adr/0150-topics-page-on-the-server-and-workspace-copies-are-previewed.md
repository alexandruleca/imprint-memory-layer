---
title: ADR 0150 — Topics page on the server, and copies between workspaces are previewed
description: GET /api/topics searches, sorts and pages every topic on the server and GET /api/topic/{name} can page its sources; POST /api/migrate/preview counts what copying or moving projects, topics or sources to another workspace takes before the migrate job runs.
---

- Status: Accepted
- Date: 2026-11-08
- Related: [ADR 0036](0036-move-memories-between-projects.md) (moves between projects, the move-back undo), [ADR 0094](0094-the-dashboard-reads-whole-sources-from-their-snapshots.md) (a topic's sources), [ADR 0134](0134-placement-rules-and-organize-proposals-sync.md) (moves reach paired devices), [ADR 0139](0139-projects-and-sources-browse-in-paged-tabs.md) (paged lists), [dashboard](../dashboard.md), [API](../api.md)

## Context

The user asked for a pass over Topics, Sync and the other list pages "and pagination", and for the copy/move feature between workspaces to make curating workspaces easy. Their workspace holds about 120,000 memories.

- `build_topic_overview` (`imprint/cli_viz.py`) asked Qdrant's facet for 100 topics and then ran two more facets for each of them. The Topics page showed "100" as the count and filtered those 100 in the browser, so every topic past the hundredth by size was unreachable, however it was searched.
- `GET /api/topic/{name}` scrolled and returned every memory of a topic with its content. A large topic sent thousands of passages to a page that shows 20 sources at a time.
- The copy/move dialog (`migrate-dialog.tsx`) moved one project, topic or source per run and offered a dry run whose only result was a line of CLI text. It never said how many memories a choice held, how many the target already had, or that the source workspace loses its full-source snapshots on a move. Its option lists came from the active workspace while its "From" could name another one.

## Decision

**Topics are paged on the server.** `GET /api/topics` takes `q` (case-insensitive substring of the name), `sort` (`count`, largest first, or `name`), `offset` and `limit` (1–500, default 100). Every topic comes from one facet request bounded at 100,000 values; only the topics of the page get their project and language breakdown. The response adds `matched` (topics the search kept), `topic_count` (every topic), `offset`, `limit`, `sort` and `q`; `total` sums the matched topics' counts. The Topics page asks for 50 at a time.

**A topic's sources can be paged.** `GET /api/topic/{name}` takes `offset`, `limit` (1–200) and `q` (substring of a source's key or conversation title). With `limit`, the grouping reads ids and source fields only and then fetches the passages of the page's groups; `source_count` is the number of groups that matched and `total` stays the topic's memory count. Without `limit` the response is as before plus `source_count`, so the Graph page's caller is unchanged. Choosing a project on the page asks the server again with `project`.

**Copies and moves between workspaces are counted first.** `POST /api/migrate/preview` takes `from`, `to`, `mode` (`project`, `topic` or `source`) and `values` (1–200 names). It counts each value with the filter `imprint migrate` uses and the union of all of them once, and reports `existing`: how many of those point ids the target already holds, which a copy rewrites instead of duplicating. It changes nothing and does not create a missing target. The dashboard's dialog previews as the choices change, then queues one `migrate` job per selected value, in order, and shows each one's progress and result. After a move it offers **Move back…**, the same selection the other way, previewed again. The dialog copies from the workspace the dashboard shows.

## Rejected alternatives

- **Raise the facet limit and keep filtering in the browser.** The page would receive thousands of topics, each with two more facet requests, before showing any.
- **A new endpoint for paged topics.** The old response stays valid with the new fields; the two callers are the dashboard's, and the legacy `cli_viz` server keeps calling `build_topic_overview` with its defaults.
- **Keep the dry run as the preview.** It is a queued job that scans and prints one line of text; it cannot say what the target already holds, and a multi-value selection would queue one dry run per value.
- **One migrate job for several values.** The CLI and its `migrate` command take one project, topic or source; queuing one job per value keeps the job contract, and each job's progress and failure stay separate.
- **An undo that deletes a copy from the target.** The target may have held some of those memories before the copy (`existing`); deleting them would lose data. Moving back is offered only after a move, and its preview shows what it takes.

## Consequences

- Every topic is reachable by search or by page; the count in the header is the real one.
- A large topic loads one page of sources (20 by default, 10 or 50 per choice) instead of all of its passages.
- The topic list asks Qdrant for every topic name on each page request; the cost grows with the number of distinct topics, not with memories.
- Selecting sources on Topics, Projects or Sources can copy or move them to another workspace together; on Topics, sources of one project can also move to another project (ADR 0036).
- `existing` counts by point id. A memory whose id differs in the target (written there under another project) counts as new, and the copy adds it, as `imprint migrate` always has.

## Invalidation conditions

- If a workspace's distinct topics grow past the facet bound (100,000), then topics past it are unreachable again and the listing needs a cursor over the topic index.
- If `imprint migrate` changes its filter or stops preserving point ids, then the preview's counts and `existing` no longer describe the run and must change with it.
- If Qdrant gains substring matching on facet values, then the search can move into the facet request.

## Validation

- `tests/test_curation_pages.py` (in-memory Qdrant) covers the topic page, search, sort and bounds, a topic's paged and searched sources, the preview's per-value and union counts, `existing` after a copy, a preview to a workspace that does not exist (which stays absent), and the refused bodies.
- `tests/test_source_reader.py` and `tests/test_sync_direction.py` still pass.
- `imprint/ui/tests/curation.spec.ts` (Playwright, mocked API) covers the Topics page's count, pages, sort and search, a topic's paged sources and project filter, picking sources and moving them to a project or opening the copy dialog with them, the copy/move dialog with several topics, a new target workspace, the preview, two queued jobs, the result and Move back, and many paired devices searched, filtered and paged. `sync.spec.ts`, `source-reader.spec.ts`, `page-scroll.spec.ts`, `projects.spec.ts`, `workspaces.spec.ts`, `workspace-sources.spec.ts`, `library.spec.ts`, `settings.spec.ts` and `graph.spec.ts` pass.
- Not run: the endpoints against the managed Qdrant server or a workspace of the user's size, and the dashboard on Windows or macOS.
