---
title: ADR 0148 — The graph draws itself on a canvas, and as a star system
description: The dashboard's Graph page renders from one sliced d3-force model with its own Canvas 2D renderer instead of G6, offers an animated three.js star view of the same model, and its scopes are built in fewer Qdrant requests and cached.
---

- Status: Accepted
- Date: 2026-11-08
- Scope: the dashboard's Graph page (`imprint/ui/src/app/graph/`) and `GET /api/graph`; the MCP `graph_scope` tool gets the same scope builder. Measured in headless Chromium on Linux, not on a MacBook (see Validation)
- Related: [ADR 0149](0149-graph-search-is-a-scope-of-its-own.md), [ADR 0043](0043-live-voice-and-echo-companion.md) (the Echo avatar whose flow the star view borrows), [dashboard](../dashboard.md#graph), [API](../api.md)

## Context

The user reported that the Graph page "lags a bit" on a MacBook with 120k memories, and asked for an animation mode that shows the memories as a star system with electric impulses among them, like the Echo avatars. Measurements before the change:

- **Scopes are small; building them was not.** `build_graph_scope` caps every scope (the overview at 50 projects and 60 topics, a project at 20–40 topics and 15–35 sources, a source at 40–80 chunks), so the page never draws more than about 110 nodes. On a synthetic 120,000-memory workspace in a scratch Qdrant (18 projects, 400 topics, about 8,000 sources), the overview took **730–940 ms**: it linked each of its 60 topics to its projects with a project count per topic, 321 sequential Qdrant requests. It was not cached. The topic scope raised `NameError` (`client` was never defined there since commit 75b62ff), so every topic graph failed. A source scope read only the first 500 points in id order, so a source longer than that showed a scattered sample of its chunks joined by "next chunk" arrows.
- **G6 cost hundreds of milliseconds per interaction for ~110 nodes.** With a production build, a 1512×945 viewport at device pixel ratio 2 and the CPU throttled 4× (a script driving Playwright, mocked overview of 110 nodes and 180 edges): loading had a longest task of 1.1–1.5 s (G6 ran 300 force ticks synchronously after its layout, then created a display object per element); 20 wheel steps produced 13–16 long tasks totalling 1.0–1.2 s; a hover sweep produced long tasks of 230–350 ms, because hover restates every element. Turning off `auto-adapt-label` or `hover-activate` individually moved the cost elsewhere rather than removing it.

## Decision

**The graph's views draw from one force model of their own, stepped a few milliseconds per frame; the flat graph is drawn on a Canvas 2D by Imprint's code, and a star view draws the same model with three.js.**

- **Force model** ([`graph-model.ts`](../../imprint/ui/src/app/graph/_components/graph-model.ts)): a d3-force simulation with the forces the G6 layout had (link distances per edge kind, charge growing with node size, collision with the padding setting, gravity to the middle). Views call `step(budget)`; new data settles off screen for up to 12 ms a frame (1.5 s at most, 3 s for graphs over 800 nodes) and is then shown, framed, with a short fade. Nodes the graph already showed keep their positions across scopes and views.
- **Graph view** ([`graph-canvas.tsx`](../../imprint/ui/src/app/graph/_components/graph-canvas.tsx)): immediate-mode drawing, only while something changes (the layout, the camera, a hover, the selection, a find). Strokes and fills are batched into one path per style. Labels are drawn in screen space, by priority, skipping those that would overlap. Above 800 nodes: tiny nodes are squares without outlines, the backing store is 1.25× instead of 2×, and labels pause while the camera moves. Pan, wheel and trackpad-pinch zoom, two-finger pinch, node drag with the force following, click, double-click, focus and fit are handled directly; the tooltip follows the pointer without re-rendering React. Colours are read from the theme's CSS variables and read again when `<html>`'s class or the OS scheme changes.
- **Star view** ([`star-canvas.tsx`](../../imprint/ui/src/app/graph/_components/star-canvas.tsx)), a toolbar toggle remembered with the other graph settings (`mode` in `imprint:graph:v2`; saved settings without it open the graph). three.js loads only when it is on. Stars are one `Points` geometry (projects as suns in their own colour, with a corona; topics, sources and chunks in the Echo hologram's blues and violets; depth by kind), connections one `LineSegments`, and the impulses a fixed pool of 160 points with 8-point trails whose motion runs in the vertex shader: brightest at the head and dying away behind it like the avatar's `exp(-fract(t - moment) / afterglow)` flow, with a little crackle across the line. A few impulses are re-aimed every 400 ms, towards heavier connections and those of the hovered or selected star. The system sways slowly and dragging turns it. Hover, click, double-click, find, focus and fit work as in the graph view. It renders at 30 fps at rest and 60 while handled, at 10 fps on software WebGL; nothing while the page is hidden; with reduced motion it stands still, draws only on change and has no impulses. Without WebGL the page returns to the graph view and says why.
- **Scopes** ([`cli_viz.py`](../../imprint/cli_viz.py)): the overview links topics to their projects from one topic facet per project (the project's members including related ones), four at a time, instead of a project count per topic. `GET /api/graph` serves scopes through the cache that already keeps overview and project views against the data fingerprint. The topic scope's count is fixed. A source's chunks are read in pages to the end before sorting. Each scope lists the kinds it showed only up to their cap in `more`, and the legend says so with a **Show more** that raises "Items per group".
- `@antv/g6` is removed from the dashboard; `d3-force` (already installed under G6) is a direct dependency. The old built-in HTML viewer in `cli_viz.py` still loads G6 from a CDN and is not changed.

## Rejected alternatives

- **Keep G6 and tune it.** Disabling `auto-adapt-label` or `hover-activate` one at a time did not remove the long tasks under the same harness (zoom stayed at 1.0–1.7 s of long tasks per 20 steps; without `hover-activate` load went up), because each G6 state or label change updates a retained display object per element. The synchronous 300-tick layout is in G6's runtime (`graphLayout` with `animation: false` calls `execute`, then `tick(300)`), with no supported way to step it across frames while keeping `drag-element-force`.
- **G6 with its WebGL renderer (`@antv/g-webgl`).** It changes the rasterizer, not the per-element updates that the profile showed (`internalUpdateElement`, `syncHierarchy`); not tried further.
- **The layout in a Web Worker.** Slicing the simulation across frames already keeps every frame under its budget for these sizes (about 200 ticks of ~0.5 ms for 110 nodes), and a worker would need its own bundling path in the static export and a copy of positions per tick for dragging.
- **Raising the server caps.** The lag was not the number of nodes; larger scopes would make every view slower without answering the report.
- **A star view in the same 2D canvas.** Depth, additive glow and hundreds of moving impulses per frame are what the GPU does well; three.js is already a dashboard dependency (the Echo avatar) and loads only when the view is on.

## Consequences

- Measured with the same harness after the change (production build, DPR 2, CPU 4×): overview load long tasks total 0.4–1.3 s with the longest 180–310 ms (page start-up included), **no long tasks while zooming**, one long task of 70–120 ms during a hover sweep. A 2,000-node stress scope (beyond any server cap) is ready in 3.5 s instead of 15.5 s unthrottled. Backend on the 120k workspace: overview **85–140 ms** to build (from 730–940 ms) and about **1.4 ms** from the cache; the topic scope works (95–140 ms); a 1,488-chunk source shows chunks 0–39 in order (34 ms).
- 176 of the 180 overview topic–project links are the same as before; the others are ties for third place under Qdrant's approximate facet counts, which both versions use.
- The renderer is Imprint's code to maintain: picking, label placement, camera, gestures and theming. `graphDebug` on each view's container (positions, label count, renderer, theme) is a hook for tests.
- The graph view drew topics, sources and chunks in fixed greys; they are now steps of the theme's ink, so the dark theme draws them light on dark. The star view keeps its own dark sky in both themes.
- The star view on a laptop's GPU and its power use are unmeasured.

## Invalidation conditions

- If the graph has to show thousands of nodes routinely (caps raised, or a "whole workspace" view), then revisit WebGL for the graph view and a worker layout, and measure again.
- If the 2D renderer's interaction code grows to need features G6 or another library gives for free (e.g. combos, edge bundling, accessibility of nodes), then weigh a library again.
- If the star view costs noticeable battery or CPU on laptops at rest, then lower its rest frame rate or stop its ambient motion after a period without input.
- If Qdrant facets on `tags.topics` become slow per project (many projects, very large workspaces), then precompute topic–project counts instead of faceting per request.

## Validation

- **Python** (`tests/test_graph_scope.py`, Linux): the overview links each topic to its three largest projects with one topic facet per project (6 facet requests for 4 projects instead of one per topic), and reports `more` at the cap; the topic scope counts its memories; a 1,200-point source read in 500-point pages is shown from chunk 0 in order with `more: ["chunk"]`; `get_graph_scope` builds once per fingerprint. Tests touching `cli_viz` and the API pass; `test_audio_ingestion`, `test_recordings` and one `test_project_associations` case need optional packages (`av`, `llama_cpp`) absent from the test environment.
- **Benchmark** on a scratch Qdrant v1.17.1 with 120,002 synthetic memories (64-dimensional vectors; facet and count cost does not depend on the vector size): the timings above, from scripts that call `build_graph_scope` directly.
- **Playwright** (`imprint/ui/tests/graph.spec.ts`, headless Chromium with SwiftShader WebGL, Linux): canvas hover, click and double-click; the star view toggle, its persistence and old saved settings; star tooltips, selection, opening and find; reduced motion; the fallback without WebGL; a 1,501-node scope (labels culled, find reaches every node, **Show more**); the theme class on `<html>`. Screenshots of both views in light and dark themes were reviewed. The rest of the specs that open the graph or Search pass.
- `npm run lint` (graph and search files) and `tsc --noEmit` pass; `next build` succeeds.
- **Not verified:** a MacBook or any machine with a GPU (both views' frame rates on real hardware), Safari's WebKit, and the desktop app's Electron window.
