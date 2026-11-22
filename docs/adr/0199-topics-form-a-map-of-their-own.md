---
title: ADR 0199 — Topics form a map of their own
description: The graph's root can be drawn as topics alone, linked when memories carry them together more often than their sizes predict, grouped by label propagation around hub topics, with projects only as an overlay; a topic scope links its most related topics.
---

- Status: Accepted
- Date: 2026-11-22
- Related: [ADR 0148](0148-the-graph-draws-itself-on-a-canvas-and-as-a-star-system.md) (graph, limits, star view), [ADR 0149](0149-graph-search-is-a-scope-of-its-own.md) (search scope), [ADR 0198](0198-every-spelling-of-a-topic-counts-under-one-name.md) (topic names, which this builds on), [ADR 0195](0195-data-files-are-kept-whole-and-found-by-a-digest.md) (data files no longer dominating topics), [API](../api.md), [MCP](../mcp.md)

## Context

- **Today's root graph.** It holds the 50 largest projects and 60 largest topics. Its only edges are project→topic, up to three per topic (`cli_viz.build_graph_scope`).
- **Nothing links topics.** No topic↔topic edge exists in any scope.
- **Hiding Projects breaks the graph.** When the owner hides Projects in the legend, the dashboard filters those nodes and their edges, leaving 60 topics with no edges, held together only by the layout's centre pull.
- **What the owner asked for:** topics connected to each other without projects at the centre, with some topics becoming the main nodes.
- **What the index offers.** It already holds what relates topics: `tags.topics` is a keyword payload index, and a facet filtered by one topic counts the topics stored with it. Measured on the owner's live store (388,255 memories):
  - 60 such facets take 0.54 s in all;
  - building the whole map below takes 0.34 s once the vocabulary is read.
- **Why raw counts don't work.** The audit's strongest raw pairs were all one project's floor-plan fixtures: `coordinate-data` + `input-validation` 20,222, `coordinate-data` + `dwg-parsing` 18,947. Large topics co-occur with everything, so raw counts link size, not meaning.

## Decision

1. **A lens.** The root takes `lens=topics`, on:
   - `/api/graph` (`?lens=topics`; the response then carries `lens`);
   - the MCP tool `graph_scope` (`lens="topics"`);
   - `build_graph_scope`.

   Without a lens, every scope is built as before, apart from ADR 0198's merged topic names.
2. **Nodes.**
   - The 60, 80 or 100 largest topics (depth 1, 2 or 3), by ADR 0198's merged counts. `more` reports a cap.
   - Each topic carries up to three projects holding most of it (`projects`), counted by the same per-project facet as today's root. Projects are an overlay, not nodes.
3. **Edges** (`kind: "related"`).
   - **Strength:** two topics are linked by normalized pointwise mutual information over all memories, `log(p(a,b) / (p(a) p(b))) / -log p(a,b)`, kept when positive and carried by at least 3 memories.
   - **Pruning:** each topic keeps its 6 strongest links, and a link stays when either end keeps it.
   - **Fields:** `strength` (0–1), `count` (memories with both) and `weight` (`round(strength × 10)`, 1–10).
4. **Groups and hubs.**
   - **Grouping:** weighted label propagation, visiting topics largest first, with ties to the larger and then alphabetically first label, so the same data always gives the same groups.
   - **Hub:** each group's largest topic. Every topic carries `group` (its hub's node id) and `hub`, and is coloured by its hub.
5. **The topic scope links related topics.** `topic:<name>` adds its 8 most related topics as `related` edges. Each must share at least 3 memories, and at least 0.5% of the focus topic's, so a tiny topic always found with a large one does not crowd out real neighbours.
6. **The MCP output marks hubs.** `graph_scope` prints `[hub]`, its root suggestions start from a hub, and a plain root suggests the lens.
7. **The dashboard.**
   - **Switching:** at the root, hiding Projects in the legend requests the lens. The hidden set persists, so a reload keeps the map. Showing Projects again returns to the usual root, and the layout reheats fully when switching either way.
   - **Header and legend:** a "Topic map" chip appears only when the response carries `lens`, so a server without the lens behaves as before. Projects stays in the legend, crossed out, to switch back.
   - **Graph view:** hubs are drawn larger, solid and always labelled. Related edges take their group's colour, grow with `weight`, and pull members weakly toward their hub.
   - **Star view:** hubs are suns on the centre plane.
   - **Tooltip and details:** they name a topic's top projects (each opens its project scope) and say when a topic is the main one of a group of N.
   - Search scopes never send the lens. See [the dashboard docs](../dashboard.md).

## Rejected alternatives

- **Raw co-occurrence counts or Jaccard.** On this store every strong pair was a large fixture topic; normalising for size is the point.
- **Topic centroids by embedding similarity.** This would read the vectors of every memory of 60–100 topics (hundreds of thousands of points) per build. It also measures similar wording, where co-use measures what the work actually combines.
- **Edges from shared sources.** A source's chunks share most of their tags, so one large file would link all its topics strongly. Memory-level co-occurrence counts each chunk once.
- **Louvain or Leiden through networkx.** That is a new dependency for at most 100 nodes. Label propagation is about twenty lines and deterministic as ordered here.
- **Precompute a co-occurrence table in the idle slot.** Building on demand takes under a second and is cached against the data fingerprint like every graph scope (ADR 0148).

## Consequences

- **Hiding Projects gives a connected map.** On the owner's store the 60-topic map had 184 links in eight groups, among them:
  - `unit-testing` with mocking, dependency injection and error handling;
  - `state-management` with UI components, forms and internationalization;
  - `shopware` with customer data and user profiles;
  - `infra` with build, ingest and logging;
  - `load-testing` with HTTP metrics and the staging environment.

  The floor-plan fixture group (`coordinate-data`, `import-fixtures`, `entity-schema`) shrinks once ADR 0195's conversion runs.
- **Topics can be walked topic to topic.** For example, `error-handling` → `http-client`, `retry-logic`, `http-status-codes`.
- **Cost.** A topic-map build reads the vocabulary plus one facet per topic and one per project: about 0.3–0.6 s on the owner's store, then cached until the data changes.
- **Compatibility.**
  - Responses gain only optional fields and an edge kind. Without a lens, older clients see the same shapes.
  - The topic scope's extra nodes are additive.
  - The MCP tool's new argument is optional.
- **Groups are recomputed on every build.** A small change in the data can move a borderline topic to another group.

## Invalidation conditions

- If groups visibly flicker between refreshes of nearly identical data, then seed label propagation with the previous build's groups.
- If the map needs more than about 100 topics, then precompute the co-occurrence table and lay it out in a worker (ADR 0148's budget).
- If topics get a controlled vocabulary (for example, the prompt lists names, see ADR 0198), then revisit the thresholds: links would get stronger and fewer.
- If owners ask why two topics are linked, then add the shared memories' sources to the edge detail.

## Validation

- `tests/test_topics.py` checks the following, on an in-memory Qdrant with two groups of topics, one topic spelled two ways, and one memory bridging the groups:
  - strength and grouping, as unit tests;
  - the lens links each group, leaves the one-memory bridge out, and names the two largest topics hubs, with group ids, shared colours and top projects;
  - the default root has no `related` edges;
  - a topic scope reached by either spelling links exactly its related topics;
  - the cache keeps lenses apart and an unknown lens is refused;
  - the MCP tool marks hubs and refuses unknown lenses;
  - the API reports the lens.
- `test_graph_scope` passes unchanged.
- The live figures above come from a read-only run against the owner's store.
- Dashboard, with Node 22 from WSL:
  - `npx tsc --noEmit`, and `eslint` on the touched files, pass.
  - `next build` generated all 25 pages.
  - `tests/graph.spec.ts` passes 18 tests, 3 of them new. With a mocked API, they cover:
    - hiding Projects requests `root?lens=topics`; the chip, the legend entry, the related edges, the hub labels, the tooltip and the details show; Show more keeps the lens; a reload keeps the map; showing Projects returns to the overview;
    - a topic scope shows related topics without sending the lens;
    - star-view hubs keep their labels.
  - With the source-reader, desktop, page-tour and search specs: 72 passed.
- Not checked: the dashboard against a live server response, WebKit, and the Electron window.
