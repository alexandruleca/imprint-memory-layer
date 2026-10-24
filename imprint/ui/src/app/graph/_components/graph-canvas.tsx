"use client";

import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import type { EdgeData, Graph, IPointerEvent, NodeData } from "@antv/g6";
import type { GraphEdge, GraphNode, GraphNodeKind } from "@/lib/types";
import { CANVAS, EDGE_KINDS, KINDS, nodeColor, nodeSize, shortLabel } from "./graph-style";

export interface GraphForces {
  repel: number;
  collide: number;
  centerStrength: number;
}

export interface GraphCanvasHandle {
  zoomBy(ratio: number): void;
  fit(): void;
  relayout(): void;
  /** Center a node, zooming in to a readable size if needed. */
  focus(id: string): void;
}

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  showLabels: boolean;
  showEdges: boolean;
  forces: GraphForces;
  selectedId: string | null;
  /** Nodes matching the find query; null when there is no query. */
  matches: Set<string> | null;
  /** Fraction of the canvas height covered from below (a bottom sheet);
   * focused nodes are centered in the part above it. */
  insetBottom: number;
  onSelect: (id: string | null) => void;
  onOpen: (id: string) => void;
  ref?: Ref<GraphCanvasHandle>;
}

interface Hover {
  node: GraphNode;
  /** Pointer position and canvas size, in px. */
  x: number;
  y: number;
  width: number;
  height: number;
}

const LINK_DISTANCE: Record<string, number> = { contains: 90, relates: 150, sequence: 36, similar: 90 };
const KIND_DEPTH: Record<GraphNodeKind, number> = { chunk: 1, source: 2, topic: 3, project: 4 };
const VIEW_ANIMATION = { duration: 350, easing: "ease-in-out" };

const PADDING = 48;

/** Show a graph at its natural size, zooming out only when it does not
 * fit, so a small scope is not blown up to fill the screen. */
async function frame(graph: Graph, animate: boolean) {
  const animation = animate ? VIEW_ANIMATION : false;
  await graph.zoomTo(1, false);
  const [width, height] = graph.getSize();
  let [minX, minY, maxX, maxY] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const n of graph.getNodeData()) {
    const { min, max } = graph.getElementRenderBounds(n.id);
    [minX, minY, maxX, maxY] = [Math.min(minX, min[0]), Math.min(minY, min[1]), Math.max(maxX, max[0]), Math.max(maxY, max[1])];
  }
  const fits = maxX - minX <= width - 2 * PADDING && maxY - minY <= height - 2 * PADDING;
  await (fits ? graph.fitCenter(animation) : graph.fitView(undefined, animation));
}

// d3-force hands the forces its own node and link copies; `_original` is
// the graph element each one was made from (@antv/layout's LayoutNode).
interface ForceNode { _original: { data?: { size?: number } } }
interface ForceLink { _original: { data?: { kind?: string } } }

function layoutOptions(forces: GraphForces) {
  return {
    type: "d3-force" as const,
    link: { distance: (link: ForceLink) => LINK_DISTANCE[link._original.data?.kind ?? ""] ?? 110, strength: 0.45 },
    // Larger nodes push harder, so hubs get room around them.
    manyBody: { strength: (node: ForceNode) => -forces.repel * (0.35 + (node._original.data?.size ?? 20) / 80) },
    // Collision uses each node's rendered size plus this spacing.
    nodeSpacing: forces.collide * 2,
    collide: { strength: 0.9 },
    // Gravity toward the middle keeps disconnected nodes from drifting off.
    x: { strength: forces.centerStrength },
    y: { strength: forces.centerStrength },
    alphaDecay: 0.035,
    animation: false,
  };
}

export function GraphCanvas({
  nodes, edges, showLabels, showEdges, forces, selectedId, matches, insetBottom, onSelect, onOpen, ref,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);
  const [ready, setReady] = useState(false);
  const [hover, setHover] = useState<Hover | null>(null);
  const rendered = useRef(false);

  // Latest props for the G6 callbacks and effects that must not re-run
  // the layout when they change.
  const latest = useRef({ nodes, edges, showLabels, showEdges, forces, selectedId, matches, insetBottom, onSelect, onOpen });
  useEffect(() => {
    latest.current = { nodes, edges, showLabels, showEdges, forces, selectedId, matches, insetBottom, onSelect, onOpen };
  });

  useImperativeHandle(ref, () => ({
    zoomBy: (ratio) => { void graphRef.current?.zoomBy(ratio, VIEW_ANIMATION); },
    fit: () => { void graphRef.current?.fitView(undefined, VIEW_ANIMATION); },
    relayout: () => {
      const graph = graphRef.current;
      if (graph) void graph.layout().then(() => frame(graph, true));
    },
    focus: (id) => {
      const graph = graphRef.current;
      if (!graph) return;
      void (async () => {
        if (graph.getZoom() < 1) await graph.zoomTo(1, VIEW_ANIMATION);
        await graph.focusElement(id, VIEW_ANIMATION);
        const inset = latest.current.insetBottom * graph.getSize()[1];
        if (inset) await graph.translateBy([0, -inset / 2], VIEW_ANIMATION);
      })();
    },
  }), []);

  // One G6 instance for the component's lifetime.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let disposed = false;
    let graph: Graph | null = null;
    const font = getComputedStyle(document.body).fontFamily;

    import("@antv/g6").then(({ Graph }) => {
      if (disposed) return;
      graph = new Graph({
        container,
        width: container.clientWidth || 800,
        height: container.clientHeight || 600,
        theme: "light",
        background: "transparent",
        padding: PADDING,
        zoomRange: [0.15, 4],
        node: {
          type: "circle",
          style: { labelFontFamily: font },
          state: {
            active: { opacity: 1, halo: true, haloLineWidth: 8, haloStrokeOpacity: 0.2 },
            // A fade, not a hide: the rest of the graph stays readable around a hovered node.
            inactive: { opacity: 0.4 },
            dim: { opacity: 0.12 },
            match: { halo: true, haloStroke: "#f59e0b", haloStrokeOpacity: 0.5, haloLineWidth: 12, opacity: 1 },
            selected: { stroke: CANVAS.focusRing, lineWidth: 2.5, halo: true, haloStroke: CANVAS.focusRing, haloStrokeOpacity: 0.22, haloLineWidth: 14, labelFontWeight: 600 },
          },
        },
        edge: {
          type: "line",
          state: {
            active: { strokeOpacity: 0.85 },
            inactive: { strokeOpacity: 0.12 },
            dim: { strokeOpacity: 0.03 },
            related: { strokeOpacity: 0.8 },
          },
        },
        layout: layoutOptions(latest.current.forces),
        behaviors: [
          "drag-canvas",
          "zoom-canvas",
          { type: "drag-element-force" },
          // Only nodes: in a dense graph the pointer crosses edges constantly, and
          // restating every element on each crossing made the whole graph flicker.
          { type: "hover-activate", enable: (event: IPointerEvent) => event.targetType === "node",
            degree: 1, state: "active", inactiveState: "inactive", animation: false },
          {
            type: "auto-adapt-label",
            padding: 2,
            throttle: 80,
            // Bigger nodes keep their labels when labels would overlap.
            sortNode: (a: NodeData, b: NodeData) =>
              Math.sign(Number(b.data?.priority ?? 0) - Number(a.data?.priority ?? 0)) as -1 | 0 | 1,
          },
        ],
      });
      graphRef.current = graph;

      const nodeOf = (event: IPointerEvent) => {
        const id = (event.target as { id?: string } | null)?.id;
        return id ? latest.current.nodes.find((n) => n.id === id) : undefined;
      };
      const pointAt = (event: IPointerEvent) => {
        const rect = container.getBoundingClientRect();
        return { x: event.client.x - rect.left, y: event.client.y - rect.top, width: rect.width, height: rect.height };
      };
      graph.on("node:click", (event: IPointerEvent) => {
        const node = nodeOf(event);
        if (node) latest.current.onSelect(node.id);
      });
      graph.on("canvas:click", () => latest.current.onSelect(null));
      graph.on("node:dblclick", (event: IPointerEvent) => {
        const node = nodeOf(event);
        if (node) latest.current.onOpen(node.id);
      });
      graph.on("node:pointerenter", (event: IPointerEvent) => {
        const node = nodeOf(event);
        if (node) setHover({ node, ...pointAt(event) });
      });
      graph.on("node:pointermove", (event: IPointerEvent) => {
        setHover((h) => (h ? { ...h, ...pointAt(event) } : h));
      });
      graph.on("node:pointerleave", () => setHover(null));
      graph.on("node:dragstart", () => setHover(null));
      graph.on("canvas:dragstart", () => setHover(null));
      setReady(true);
    });

    const resize = new ResizeObserver(() => {
      if (container.clientWidth && container.clientHeight) graph?.resize(container.clientWidth, container.clientHeight);
    });
    resize.observe(container);
    return () => {
      disposed = true;
      resize.disconnect();
      graph?.destroy();
      graphRef.current = null;
      rendered.current = false;
    };
  }, []);

  // New data: restyle everything and run the layout, starting nodes the
  // graph already showed from where they were.
  useEffect(() => {
    const graph = graphRef.current;
    if (!ready || !graph) return;
    const { showLabels, showEdges, forces } = latest.current;
    const known = new Map<string, { x: number; y: number }>();
    if (rendered.current) {
      for (const n of graph.getNodeData()) {
        const [x, y] = graph.getElementPosition(n.id);
        known.set(n.id, { x, y });
      }
    }
    const maxCount = { project: 1, topic: 1, source: 1, chunk: 1 } as Record<GraphNodeKind, number>;
    for (const n of nodes) maxCount[n.kind] = Math.max(maxCount[n.kind], n.count);
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const maxWeight: Record<string, number> = {};
    for (const e of edges) maxWeight[e.kind] = Math.max(maxWeight[e.kind] ?? 1, e.weight);

    const nodeData: NodeData[] = nodes.map((n) => {
      const size = nodeSize(n, maxCount);
      const color = nodeColor(n);
      const ring = n.kind === "topic";
      const strong = n.kind === "project" || !!n.focus;
      return {
        id: n.id,
        data: { kind: n.kind, size, priority: (n.focus ? 10 : 0) + KIND_DEPTH[n.kind] + size / 100 },
        style: {
          ...known.get(n.id),
          size,
          // The base every state change returns to.
          opacity: 1,
          fill: color,
          fillOpacity: ring ? 0.1 : n.kind === "project" ? 0.95 : 0.85,
          stroke: n.focus ? CANVAS.focusRing : color,
          lineWidth: n.focus ? 2.5 : ring ? 1.5 : 0,
          halo: strong,
          haloStroke: color,
          haloStrokeOpacity: 0.16,
          haloLineWidth: n.focus ? 16 : 10,
          labelText: showLabels ? shortLabel(n.label) : "",
          labelPlacement: "bottom",
          labelOffsetY: 3,
          labelFill: CANVAS.label,
          labelFontSize: strong ? 12 : 10.5,
          labelFontWeight: strong ? 600 : 400,
          labelStroke: CANVAS.labelHalo,
          labelLineWidth: 3,
          zIndex: KIND_DEPTH[n.kind] + (n.focus ? 10 : 0),
          cursor: "pointer",
        },
      };
    });
    const edgeData: EdgeData[] = edges.map((e) => {
      const kind = EDGE_KINDS[e.kind];
      const from = byId.get(e.source);
      const fromProject = from?.kind === "project";
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        data: { kind: e.kind },
        style: {
          stroke: kind.color ?? (fromProject ? from.color : CANVAS.edge),
          strokeOpacity: kind.color ? 0.7 : fromProject ? 0.45 : 0.3,
          lineWidth: 0.6 + (e.weight / (maxWeight[e.kind] ?? 1)) * 1.6,
          lineDash: kind.dash,
          endArrow: !!kind.arrow,
          endArrowSize: 6,
          visibility: showEdges ? "visible" : "hidden",
        },
      };
    });

    graph.setLayout(layoutOptions(forces));
    graph.setData({ nodes: nodeData, edges: edgeData });
    let current = true;
    const animate = rendered.current;
    void graph.render().then(async () => {
      if (!current) return;
      rendered.current = true;
      applyStates(graph, latest.current.selectedId, latest.current.matches, edges);
      await frame(graph, animate);
    });
    return () => { current = false; };
  }, [ready, nodes, edges]);

  // Labels and edges change styles only: no new layout.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || !rendered.current) return;
    graph.updateNodeData(latest.current.nodes.map((n) => ({ id: n.id, style: { labelText: showLabels ? shortLabel(n.label) : "" } })));
    graph.updateEdgeData(latest.current.edges.map((e) => ({ id: e.id, style: { visibility: showEdges ? "visible" : "hidden" } })));
    void graph.draw();
  }, [showLabels, showEdges]);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || !rendered.current) return;
    graph.setLayout(layoutOptions(forces));
    void graph.layout();
  }, [forces]);

  useEffect(() => {
    const graph = graphRef.current;
    if (graph && rendered.current) applyStates(graph, selectedId, matches, latest.current.edges);
  }, [selectedId, matches]);

  return (
    <div className="absolute inset-0">
      <div ref={containerRef} className="absolute inset-0" />
      {hover && <NodeTooltip hover={hover} />}
    </div>
  );
}

/** Selection and find results as element states; hover-activate adds and
 * removes its own states on top of these. */
function applyStates(graph: Graph, selectedId: string | null, matches: Set<string> | null, edges: GraphEdge[]) {
  const states: Record<string, string[]> = {};
  for (const n of graph.getNodeData()) {
    const s: string[] = [];
    if (matches) s.push(matches.has(n.id) ? "match" : "dim");
    if (n.id === selectedId) s.push("selected");
    states[n.id] = s;
  }
  for (const e of edges) {
    const s: string[] = [];
    if (matches && !(matches.has(e.source) && matches.has(e.target))) s.push("dim");
    if (selectedId && (e.source === selectedId || e.target === selectedId)) s.push("related");
    states[e.id] = s;
  }
  void graph.setElementState(states, false);
}

function NodeTooltip({ hover }: { hover: Hover }) {
  const { node } = hover;
  const kind = KINDS[node.kind];
  const width = 240;
  // Open away from the canvas edges.
  const flipX = hover.x + 16 + width > hover.width;
  const flipY = hover.y + 90 > hover.height;
  const detail =
    node.kind === "chunk"
      ? [node.chunk_index !== undefined ? `Chunk #${node.chunk_index}` : "Chunk", node.project].filter(Boolean).join(" · ")
      : `${kind.label}${node.count > 0 ? ` · ${node.count.toLocaleString()} ${node.count === 1 ? "memory" : "memories"}` : ""}`;
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-30 rounded-lg border border-border bg-popover/95 px-3 py-2 text-xs shadow-xl backdrop-blur"
      style={{
        maxWidth: width,
        left: flipX ? undefined : hover.x + 16,
        right: flipX ? hover.width - hover.x + 16 : undefined,
        top: flipY ? undefined : hover.y + 16,
        bottom: flipY ? hover.height - hover.y + 16 : undefined,
      }}
    >
      <div className="flex items-center gap-2 font-medium text-popover-foreground">
        <span className="size-2 shrink-0 rounded-full" style={{ background: nodeColor(node) }} />
        <span className="line-clamp-2 break-words">{node.label}</span>
      </div>
      <p className="mt-0.5 text-muted-foreground">{detail}</p>
      <p className="mt-1 text-[11px] text-muted-foreground/70">
        {node.kind === "chunk" ? "Double-click to read" : "Double-click to open"}
      </p>
    </div>
  );
}
