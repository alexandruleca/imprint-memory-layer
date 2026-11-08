---
title: ADR 0149 — Graph search is a scope of its own
description: The Graph page's search box runs the dashboard's hybrid search and shows its hits, with their sources, projects and topics, as a `search:<query>` scope built in the browser from the search response, beside a ranked results list.
---

- Status: Accepted
- Date: 2026-11-08
- Scope: the dashboard's Graph and Search pages; no API change
- Related: [ADR 0148](0148-the-graph-draws-itself-on-a-canvas-and-as-a-star-system.md), [ADR 0008](0008-dashboard-retrieval-and-accessibility.md) (dashboard search), [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md) (hybrid retrieval), [dashboard](../dashboard.md#graph)

## Context

The Graph page's box only matched the names of the nodes already on screen, at most about 110 of them (ADR 0148), so most memories could not be found from the graph. The user approved integrating search into the graph: the real hybrid search, its results as their own scope with a breadcrumb and a URL, a ranked list beside them linked to the nodes both ways, hits that glow in the star view with impulses from the query to them, and an **Open in graph** action on the Search page, which stays for reading-heavy work.

`GET /api/search` (the Search page's call, `searchMemories`) already returns each hit's id, label, project, source, chunk index, topics and content, in rank order.

## Decision

**A graph search is the scope `search:<query>`, built in the browser from `GET /api/search`'s hits.** [`graph-search.ts`](../../imprint/ui/src/app/graph/_components/graph-search.ts) turns each hit into a chunk node carrying its rank, linked to its source, the source to its project, and the hit to its topics, so hits that share a conversation, file, project or topic cluster. The cached overview scope lends the projects their colours.

- **The box keeps finding names and adds search.** Typing still marks the names on screen and Enter steps through them; when nothing on screen matches, or with Ctrl/⌘+Enter or the box's search button, Enter searches all memories. Searching opens the scope after the overview: `?scope=search:<query>`, with the Search page's `project`, `type` and `mode` filters as further URL parameters, so back, forward and links work as for other scopes. "Items per group" asks for 30, 50 or 80 hits.
- **Results and nodes are linked.** A panel on the left lists the hits by rank with an excerpt and their project, file and chunk number; the graph is framed in the space beside it (on phones it is a bottom sheet). Clicking a result selects and focuses its node; selecting a node marks its result and scrolls to it; double-clicking either reads the chunk. The panel links to the Search page with the same query and filters.
- **Hits stand out.** In the graph view hits are drawn in ink with their rank inside, larger the better they rank, and the rest recedes. In the star view hits glow and everything else dims; a pale star at the middle of the hits stands for the query, and most impulses run from it to the hits, more often and faster to better-ranked ones.
- **The Search page** gets **Open in graph** for its query and filters, and runs a search given in its URL (`/search/?q=…&project=…`).

## Rejected alternatives

- **A graph-shaped search endpoint.** Every field the scope needs is already in the search response, and the hits' neighbours (other chunks of their sources, other projects of their topics) would multiply the request's cost without being asked for. Building in the browser keeps one retrieval path (ADR 0005) behind both pages.
- **Replacing name finding with search.** Finding a node on screen is instant and needs no request, and the existing keyboard flow (`/`, Enter, Shift+Enter, Escape) is relied on; search takes over only when finding has nothing to offer or is asked for.
- **Search hits highlighted inside the current scope.** Most hits are not in a capped scope at all; a scope of their own shows all of them, and the trail leads back.

## Consequences

- Any memory can be reached from the graph, and the clusters of a result set (one conversation, one project) are visible at a glance.
- A search scope is as fresh as the search: it is not cached, and it costs one search request plus the (cached) overview.
- A hit without a source hangs directly off its project; a hit without either stands alone.
- Project colours depend on the overview being loadable; without it they fall back to a colour of their own.

## Invalidation conditions

- If the search response stops carrying provenance or topics, then the scope needs a server-side builder.
- If people want hits in their wider context (the rest of their conversation or file), then add expansion on demand rather than enlarging every search.
- If the graph search and the Search page drift apart in ranking or filters, then route both through one shared client function and options type.

## Validation

- **Playwright** (`imprint/ui/tests/graph.spec.ts`, headless Chromium, Linux, with a mocked `/api/search`): a query with no names on screen opens `?scope=search:…`, the breadcrumb, the ranked list and 9 nodes for 3 hits; a result selects its node, a node marks its result, double-clicking a result reads it, back returns to the overview; Ctrl+Enter searches although names match; filters in the URL reach the search request and the link to the Search page; star view tooltips and result clicks in a search scope; the Search page's **Open in graph** and the link back, which searches on arrival.
- Screenshots of a search scope in both views and both themes were reviewed.
- **Not verified:** search scopes against a real workspace's search results.
