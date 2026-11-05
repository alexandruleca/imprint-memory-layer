---
title: ADR 0139 — Projects and Sources browse every source in paged tabs
description: The Projects and Sources pages use the review pages' tabs, paged lists, filters and keys; the browser lists every source of the workspace or project and pages, filters and sorts it, after the source listing reads only the snapshot columns it needs.
---

- Status: Accepted
- Date: 2026-11-05
- Related: [ADR 0136](0136-review-pages-use-paged-tabs-and-decisions-reopen.md) (the pattern and the shared list), [ADR 0015](0015-workspace-roots-and-source-purge.md) (roots, purge and exclusions), [ADR 0036](0036-move-memories-between-projects.md) (moves), [ADR 0094](0094-the-dashboard-reads-whole-sources-from-their-snapshots.md) (the reader), [ADR 0099](0099-sources-can-belong-to-several-projects.md) (associations), [dashboard](../dashboard.md), [API](../api.md)

## Context

The user asked for the Projects and Sources pages to work like the Organize and Core memories tabs: "it's just a long scroll now".

Both pages were a list beside a column of cards. Projects showed one card of facet bars, then a table of every source of the project, then dialogs for moves, migration and associations. Sources showed one source's summary and every chunk it was split into, below a collapsed panel of roots, purge and exclusions that sat above every source. Neither list could be filtered except by a substring, sorted or paged.

The lists were also cut short. Sources asked `GET /api/sources` for 500 sources and a project for 1,000. On the maintainer's machine, measured read-only against the running dashboard and a copy of its snapshot database on 2026-11-05:

- The workspace holds 79,287 memories in 51 projects and 17,782 sources: 15,706 files, 1,475 extracted documents, 384 conversations and 217 sources stored without a snapshot. One project holds 7,561 sources.
- So Sources listed 500 of 17,782 sources, and that project 1,000 of its 7,561.
- `GET /api/sources?limit=500` took 1.6 s, `limit=3000` 2.2 s, and `limit=20000` 10.9 s for all 17,782 (2.9 MB).
- The time went to the owner lookup. For every listed source the handler walked all 17,599 snapshot heads (2.35 s per 2,000 sources), after `list_metadata` parsed 38 MB of head metadata, manifests included. Qdrant's facet over the `source` field returned all 17,800 values in 0.05 s.

## Decision

**Both pages use the review pages' parts (ADR 0136).** A one-line header with ⓘ; hash-linked tabs with counts; a list of 20 rows a page, with 10 or 50 kept per browser under the same key as Organize; a toolbar with a search, facet chips, selects, a sort and a compact pager; checkboxes and a selection bar; two-line rows whose actions fold to icons below 620 px of content; a rail that becomes a select below 760 px. The same single-key shortcuts and per-browser switch apply: `j`/`k`, `x`, Enter, `o` read, `m` move, `[`/`]`, `/`, the tab digits and `?`.

**Sources** has three tabs:

- **Indexed** (`#indexed`) is one list of every source of the workspace, largest first. A row names a conversation by its first question and a file by its name, then its folder or key, project, kind, age and chunks. The toolbar filters by kind (conversations, files, documents, web pages, pasted content, and no snapshot) and by project, and sorts by chunks, recency or name.
- Each row has **Read** and a menu: details, Show in graph, Move to another project…, Link to another project…, Copy or move to a workspace…, Purge from this workspace…. Opening a row shows where it was recorded, its tags and topics, and its chunks eight at a time, each readable in place. Selected sources of one project move together.
- **Excluded** (`#excluded`) lists what purges keep out of ingestion, each with **Allow re-ingestion**. **Purge a path…** opens the existing preview and typed-name confirmation as a dialog. A row's **Purge…** opens the same dialog with the source already previewed.
- **Roots** (`#roots`) is the existing roots editor.

**Projects** has a rail of projects by memory count beside the open project:

- The open project is in the address (`?project=NAME`); without one, the last project opened in this browser opens, else the largest. `J`/`K` step through them.
- Its line carries **Graph**, **Move or merge…** and **Migrate…**.
- Tab **Overview** (`#overview`) shows Organize's profile of the project when one exists, its sources by kind, and its facet bars.
- Tab **Sources** (`#sources`) is the Indexed list scoped to the project. A linked source shows its own project and does not move from here.
- **Project associations…** stays the existing dialog; a source's menu opens it for that source.

**The browser lists every source and pages, filters, sorts and counts in memory.** Sources asks for every source of the workspace (`limit=100000`), and a project for all of its own; there is no server paging.

**The listing reads only what it needs.** `GET /api/sources` reads each head's source, project, time, kind and origin project with `source_snapshots.list_heads`, which uses SQL `json_extract` and keeps the result until the snapshot database changes. The handler indexes the heads by source once. Each entry gains the snapshot's `kind` and its time, `updated`; both are "" and 0 for a source without a snapshot. On the same copy of the database, the handler lists all 17,782 sources in 0.57 s the first time and 0.02 s after, as 3.6 MB of JSON.

**A loaded list stays put.** A data change no longer refetches a source list. A note says the data changed and offers **Refresh**. The project rail and a project's facets still follow every change.

## Rejected alternatives

- **Paging and searching on the server.** The kind and project counts, sorting by recency, and select-all over a filter need the whole list. 17,782 rows are 3.6 MB on loopback and filter in milliseconds. The slow part was the owner lookup, which made the full listing take 10.9 s, not the size of the response. ADR 0136 kept `GET /api/organize` whole for the same reasons.
- **Raising the old pages' limits.** With the old lookup a full listing took 10.9 s, and a project of 7,561 sources stayed a single table on one long page.
- **Tabs by kind (Conversations, Files, Documents).** They would sit beside Excluded and Roots, which are not kinds, and a kind is a filter like Organize's evidence. ADR 0136 rejected grouping by source kind for the same reason.
- **A project rail on Sources as well.** Projects is the per-project view. The list needs the width for paths, and a select filters by project.
- **Refetching the lists on every data change**, as the old Projects page refetched a project's sources on every `/api/stream` event. During an ingest that is a project of up to 7,561 sources every 2 s, and rows move under the cursor while selecting.
- **An "All projects" row in the Projects rail.** The rail already lists every project with its count, and Sources is the list across projects.

## Consequences

- Sources lists all 17,782 sources instead of 500, and a project all of its own instead of 1,000. A page load moves about 3.6 MB on loopback.
- `GET /api/sources` entries carry `kind` and `updated`. A single snapshot owner is found by index, not by a scan.
- A source's details list at most its first 500 chunks, as `GET /api/source/{key}` returns them, and say so.
- A source without a snapshot (217 here) has no project on Sources, so it moves only from its project's Sources tab.
- The associations dialog's source select still lists every snapshot head; opening it from a source's menu skips that select.
- The shared review parts gained small options without changing Organize or Core memories:
  - the rail and its select can leave out "All projects" and show project colours;
  - the rail keeps the chosen project in sight;
  - facet counts use thousands separators.
- Organize's **Open in Projects** opens that project.
- The page-specific `workspace-sources` and `project-sources` components are gone.
- Known gaps: no bulk purge (each purge previews by scanning the collection), and a dark theme.

## Invalidation conditions

- If a workspace's listing passes about 100,000 sources, the request's limit, or about 15 MB, page and filter on the server.
- If listing every source takes more than about 2 s on the maintainer's machine after the first request, keep the listing's columns outside the head metadata.
- If sources without a snapshot get a recorded project in the listing, show it on Sources and let them move from there.
- If users want lists to follow an ingest live, replace the Refresh note with merging changes in place.
- If the desktop window's minimum grows past about 1,000 px, the narrow layouts can go (as in ADR 0136).

## Validation

- `tests/test_source_reader.py`:
  - the listing carries each source's kind, project and snapshot time, "" and 0 for a memory stored without a snapshot;
  - it reads the heads again after the snapshot database changes;
  - `list_heads` gives the same source, project, kind, time and origin project as `list_metadata` with `project_associations.origin`.
- `tests/test_project_associations.py` still lists a linked source under its related project as before.
- `imprint/ui/tests/library.spec.ts`, in headless Chromium against the static build with the API mocked at about the maintainer's scale (18,796 made-up sources over 51 projects, one holding 7,462; tests that do not measure scale use a twentieth of it):
  - Sources pages, searches, filters by kind and project, sorts by recency and keeps 50 rows a page;
  - a row opens its details and chunks, reads whole or at a chunk, and the keyboard moves and reads;
  - a row's purge previews at once, and the path is allowed back from Excluded;
  - Move takes sources of one project only;
  - a data change waits for Refresh;
  - tabs switch by key and address;
  - Projects opens the address's, then the last, then the largest project, and `J`/`K` step;
  - the Overview shows the profile and opens sources by kind;
  - a large project's sources page, search and move by keyboard;
  - at 800×600 the rail is a select and nothing scrolls sideways.
- The updated `projects.spec.ts`, `project-associations.spec.ts` and `workspace-sources.spec.ts` cover the moves, the merge that follows its target, linked sources and the associations dialog from a row, purge and roots.
- The Organize and Core memories specs pass. One Core memories test now waits for its tab switch before clicking Add. It clicked while Proposed's empty state still showed its own Add button, which also failed on the unchanged code in 1 of 3 runs.
- Screenshots of both pages at 1360×860, 800×600 and 420×800 in headless Chromium on macOS.
- Timings: the old endpoint against the running dashboard; the new handler on a copy of the maintainer's snapshot and association databases, with the facet replayed from the old endpoint's output; the facet itself against the running Qdrant.
- Not verified: the new endpoint end to end against the live workspace, the Electron window, Windows, and a screen reader.
