"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { getGraphScope, getMemory } from "@/lib/api";
import { GraphSkeleton } from "@/components/loaders";
import type {
  GraphEdge,
  GraphNode,
  GraphScopeData,
  GraphNodeKind,
} from "@/lib/types";
import {
  GraphBreadcrumbs,
  type ScopeCrumb,
} from "./_components/breadcrumbs";
import {
  GraphControls,
  type GraphForces,
  type GraphToggles,
} from "./_components/controls";
import { PanelRight, Search as SearchIcon } from "lucide-react";

const DEFAULT_FORCES: GraphForces = { repel: 1200, collide: 24, centerStrength: 0.05 };
const DEFAULT_TOGGLES: GraphToggles = {
  showLabels: true,
  showEdges: true,
  showChunks: true,
  showSources: true,
  showTopics: true,
  showProjects: true,
  localMode: false,
};
const STORAGE_KEY = "imprint:graph:state:v1";

const KIND_SIZE: Record<GraphNodeKind, { base: number; scale: number; min: number; max: number }> = {
  project: { base: 28, scale: 60, min: 32, max: 110 },
  topic: { base: 18, scale: 36, min: 22, max: 70 },
  source: { base: 14, scale: 22, min: 18, max: 44 },
  chunk: { base: 10, scale: 8, min: 10, max: 18 },
};

function sizeFor(n: GraphNode, maxCount: number): number {
  const cfg = KIND_SIZE[n.kind];
  const ratio = maxCount > 0 ? Math.sqrt(Math.max(1, n.count) / maxCount) : 0;
  return Math.max(cfg.min, Math.min(cfg.max, cfg.base + ratio * cfg.scale));
}

function scopeLabel(scope: string): string {
  const [kind, ...rest] = scope.split(":");
  const val = rest.join(":");
  if (kind === "root" || !val) return "root";
  if (kind === "src") return val.split("/").pop() || val;
  return val;
}

export default function GraphPage() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [crumbs, setCrumbs] = useState<ScopeCrumb[]>([{ scope: "root", label: "root" }]);
  const [data, setData] = useState<GraphScopeData | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [memoryPreview, setMemoryPreview] = useState<{ id: string; content: string } | null>(null);
  const [depth, setDepth] = useState(1);
  const [forces, setForces] = useState<GraphForces>(DEFAULT_FORCES);
  const [toggles, setToggles] = useState<GraphToggles>(DEFAULT_TOGGLES);
  const [panelOpen, setPanelOpen] = useState(true);
  const [query, setQuery] = useState("");

  const graphRef = useRef<unknown>(null);
  const resizeObsRef = useRef<ResizeObserver | null>(null);
  const adjacencyRef = useRef<Map<string, Set<string>>>(new Map());

  // Restore persisted state
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const s = JSON.parse(raw);
      if (s.depth) setDepth(s.depth);
      if (s.forces) setForces({ ...DEFAULT_FORCES, ...s.forces });
      if (s.toggles) setToggles({ ...DEFAULT_TOGGLES, ...s.toggles });
      if (s.panelOpen !== undefined) setPanelOpen(s.panelOpen);
    } catch {}
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ depth, forces, toggles, panelOpen }),
      );
    } catch {}
  }, [depth, forces, toggles, panelOpen]);

  const scope = crumbs[crumbs.length - 1]?.scope || "root";

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getGraphScope(scope, depth)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [scope, depth]);

  // Filter nodes/edges based on toggles + local mode + search
  const filtered = useMemo(() => {
    if (!data) return { nodes: [] as GraphNode[], edges: [] as GraphEdge[] };
    const allowKind = (k: GraphNodeKind) =>
      (k === "project" && toggles.showProjects) ||
      (k === "topic" && toggles.showTopics) ||
      (k === "source" && toggles.showSources) ||
      (k === "chunk" && toggles.showChunks);

    let nodes = data.nodes.filter((n) => allowKind(n.kind) || n.focus);
    const q = query.trim().toLowerCase();
    if (q) {
      const match = new Set(
        nodes.filter((n) => n.label.toLowerCase().includes(q)).map((n) => n.id),
      );
      const neighbors = new Set<string>(match);
      data.edges.forEach((e) => {
        if (match.has(e.source)) neighbors.add(e.target);
        if (match.has(e.target)) neighbors.add(e.source);
      });
      nodes = nodes.filter((n) => neighbors.has(n.id));
    }
    if (toggles.localMode && data.center) {
      const ring = new Set<string>([data.center]);
      data.edges.forEach((e) => {
        if (e.source === data.center) ring.add(e.target);
        if (e.target === data.center) ring.add(e.source);
      });
      nodes = nodes.filter((n) => ring.has(n.id));
    }
    const nodeIds = new Set(nodes.map((n) => n.id));
    const edges = data.edges.filter(
      (e) => nodeIds.has(e.source) && nodeIds.has(e.target),
    );
    return { nodes, edges };
  }, [data, toggles, query]);

  // Build adjacency for hover fade
  useEffect(() => {
    const map = new Map<string, Set<string>>();
    for (const e of filtered.edges) {
      if (!map.has(e.source)) map.set(e.source, new Set());
      if (!map.has(e.target)) map.set(e.target, new Set());
      map.get(e.source)!.add(e.target);
      map.get(e.target)!.add(e.source);
    }
    adjacencyRef.current = map;
  }, [filtered.edges]);

  const drill = useCallback(
    (node: GraphNode) => {
      const scopeStr =
        node.kind === "project"
          ? `project:${node.label}`
          : node.kind === "topic"
            ? `topic:${node.label}`
            : node.kind === "source"
              ? `source:${node.fullPath || node.label}`
              : `chunk:${node.id.replace(/^chunk:/, "")}`;
      setCrumbs((c) => [...c, { scope: scopeStr, label: node.label }]);
      setSelected(null);
    },
    [],
  );

  const goToCrumb = useCallback((index: number) => {
    setCrumbs((c) => c.slice(0, index + 1));
    setSelected(null);
  }, []);

  const resetDefaults = useCallback(() => {
    setForces(DEFAULT_FORCES);
    setToggles(DEFAULT_TOGGLES);
    setDepth(1);
  }, []);

  // Render G6
  useEffect(() => {
    if (!containerRef.current || !data) return;
    let cancelled = false;

    import("@antv/g6").then(({ Graph }) => {
      if (cancelled || !containerRef.current) return;

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const prev = graphRef.current as any;
      if (prev?.destroy) prev.destroy();
      resizeObsRef.current?.disconnect();

      const rect = containerRef.current.getBoundingClientRect();
      const width = rect.width || 800;
      const height = rect.height || 600;
      const cx = width / 2;
      const cy = height / 2;

      if (filtered.nodes.length === 0) {
        graphRef.current = null;
        return;
      }

      const maxCount = Math.max(...filtered.nodes.map((n) => n.count || 1), 1);
      const spread = Math.min(cx, cy) * 0.75;

      const centerId = data.center;
      const nodes = filtered.nodes.map((n, i) => {
        const size = sizeFor(n, maxCount);
        const isCenter = n.id === centerId;
        const angle = (2 * Math.PI * i) / filtered.nodes.length;
        const x = isCenter ? cx : cx + spread * Math.cos(angle);
        const y = isCenter ? cy : cy + spread * Math.sin(angle);
        const isRing = n.kind === "topic";
        return {
          id: n.id,
          data: { ...n, _size: size },
          style: {
            x,
            y,
            size,
            fill: isRing ? "transparent" : n.color + (n.kind === "project" ? "55" : "44"),
            stroke: n.color,
            lineWidth: isCenter ? 4 : isRing ? 3 : 2,
            lineDash: isRing ? [6, 4] : undefined,
            labelText: toggles.showLabels ? n.label : "",
            labelFontSize: n.kind === "project" ? 12 : n.kind === "topic" ? 10 : 9,
            labelFill: "#e2e8f0",
            labelPlacement: "center" as const,
            labelWordWrap: true,
            labelMaxLines: 2,
            labelBackground: true,
            labelBackgroundFill: "rgba(10,10,26,0.75)",
            labelBackgroundRadius: 3,
            labelPadding: [1, 4] as [number, number],
            cursor: "pointer" as const,
          },
        };
      });

      const maxWeight = Math.max(...filtered.edges.map((e) => e.weight || 1), 1);
      const edges = toggles.showEdges
        ? filtered.edges.map((e) => ({
            id: e.id,
            source: e.source,
            target: e.target,
            data: { ...e },
            style: {
              stroke:
                e.kind === "sequence"
                  ? "#f59e0b"
                  : e.kind === "similar"
                    ? "#22d3ee"
                    : e.kind === "contains"
                      ? "#6366f1"
                      : "#475569",
              lineWidth: Math.max(
                0.5,
                Math.min(3, (e.weight / maxWeight) * 2.5),
              ),
              strokeOpacity: 0.45,
              endArrow: e.kind === "sequence",
            },
          }))
        : [];

      const graph = new Graph({
        container: containerRef.current,
        width,
        height,
        data: { nodes, edges },
        autoFit: "view",
        layout: {
          type: "d3-force",
          centerX: cx,
          centerY: cy,
          centerStrength: forces.centerStrength,
          collide: {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            radius: (d: any) => (d?.data?._size ?? 30) / 2 + forces.collide,
            strength: 1,
          },
          manyBody: { strength: -forces.repel },
          link: {
            distance: 140,
            strength: 0.3,
          },
          alphaDecay: 0.03,
          animation: false,
        },
        node: { style: {} },
        behaviors: ["drag-canvas", "zoom-canvas", "drag-element"],
      });

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      graph.on("node:click", (evt: any) => {
        const id = evt?.target?.id;
        if (!id) return;
        const n = filtered.nodes.find((x) => x.id === id);
        if (!n) return;
        if (evt?.originalEvent?.shiftKey) {
          setToggles((t) => ({ ...t, localMode: true }));
          setSelected(n);
          return;
        }
        setSelected(n);
      });

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      graph.on("node:dblclick", (evt: any) => {
        const id = evt?.target?.id;
        if (!id) return;
        const n = filtered.nodes.find((x) => x.id === id);
        if (!n) return;
        if (n.kind === "chunk") {
          const mid = n.id.replace(/^chunk:/, "");
          getMemory(mid)
            .then((m) => {
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              const mm = m as any;
              setMemoryPreview({ id: mid, content: mm?.content || n.content || "" });
            })
            .catch(() =>
              setMemoryPreview({ id: mid, content: n.content || "" }),
            );
          return;
        }
        drill(n);
      });

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      graph.on("node:pointerenter", (evt: any) => {
        const id = evt?.target?.id;
        if (!id) return;
        const adj = adjacencyRef.current.get(id) || new Set();
        const keep = new Set<string>([id, ...adj]);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const g = graph as any;
        filtered.nodes.forEach((n) => {
          g.updateNodeData?.([
            {
              id: n.id,
              style: { opacity: keep.has(n.id) ? 1 : 0.18 },
            },
          ]);
        });
        filtered.edges.forEach((e) => {
          g.updateEdgeData?.([
            {
              id: e.id,
              style: {
                strokeOpacity:
                  e.source === id || e.target === id ? 0.85 : 0.08,
              },
            },
          ]);
        });
        g.draw?.();
      });

      graph.on("node:pointerleave", () => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const g = graph as any;
        filtered.nodes.forEach((n) => {
          g.updateNodeData?.([{ id: n.id, style: { opacity: 1 } }]);
        });
        filtered.edges.forEach((e) => {
          g.updateEdgeData?.([{ id: e.id, style: { strokeOpacity: 0.45 } }]);
        });
        g.draw?.();
      });

      graph.render();
      graphRef.current = graph;

      const ro = new ResizeObserver(() => {
        const r = containerRef.current?.getBoundingClientRect();
        if (!r || r.width === 0 || r.height === 0) return;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const g = graph as any;
        if (typeof g.setSize === "function") g.setSize(r.width, r.height);
        if (typeof g.fitView === "function") g.fitView();
      });
      ro.observe(containerRef.current);
      resizeObsRef.current = ro;
    });

    return () => {
      cancelled = true;
      resizeObsRef.current?.disconnect();
      resizeObsRef.current = null;
    };
  }, [data, filtered, forces, toggles.showLabels, toggles.showEdges, drill]);

  return (
    <div className="flex flex-col h-screen">
      <div className="p-3 border-b border-border flex items-center gap-3 flex-wrap">
        <h2 className="text-lg font-bold">Graph</h2>
        <GraphBreadcrumbs crumbs={crumbs} onGo={goToCrumb} />
        <div className="ml-auto flex items-center gap-2">
          <div className="flex items-center gap-1 bg-muted px-2 py-1 rounded">
            <SearchIcon className="h-3 w-3 text-muted-foreground" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="filter"
              className="bg-transparent outline-none text-sm w-32"
            />
          </div>
          <button
            onClick={() => setPanelOpen((v) => !v)}
            className="p-1.5 rounded hover:bg-muted"
            aria-label="Toggle controls"
          >
            <PanelRight className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="flex-1 relative">
        {loading && !data ? (
          <GraphSkeleton />
        ) : (
          <div ref={containerRef} className="absolute inset-0 bg-[#0a0a1a]" />
        )}

        {panelOpen && (
          <div className="absolute top-3 left-3 w-64 bg-background/95 backdrop-blur border border-border rounded-lg p-3 shadow-lg z-10 max-h-[calc(100%-1.5rem)] overflow-y-auto">
            <GraphControls
              depth={depth}
              onDepth={setDepth}
              forces={forces}
              onForces={setForces}
              toggles={toggles}
              onToggles={setToggles}
              onReset={resetDefaults}
            />
          </div>
        )}

        {selected && (
          <div className="absolute top-3 right-3 w-72 bg-background/95 backdrop-blur border border-border rounded-lg p-4 space-y-3 shadow-lg z-10">
            <div className="flex items-center justify-between">
              <h3 className="font-medium truncate" title={selected.label}>
                {selected.label}
              </h3>
              <button
                onClick={() => setSelected(null)}
                className="text-muted-foreground hover:text-foreground text-lg leading-none px-1"
              >
                &times;
              </button>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="secondary" className="capitalize">
                {selected.kind}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {selected.count.toLocaleString()} memories
              </span>
            </div>
            {selected.fullPath && (
              <p className="text-xs text-muted-foreground break-all">
                {selected.fullPath}
              </p>
            )}
            {selected.content && (
              <p className="text-xs text-foreground/80 line-clamp-6 whitespace-pre-wrap">
                {selected.content}
              </p>
            )}
            <div className="flex gap-2 pt-1">
              {selected.kind !== "chunk" ? (
                <button
                  onClick={() => drill(selected)}
                  className="text-xs px-2 py-1 rounded bg-primary text-primary-foreground hover:opacity-90"
                >
                  Open
                </button>
              ) : (
                <button
                  onClick={() => {
                    const mid = selected.id.replace(/^chunk:/, "");
                    getMemory(mid)
                      .then((m) =>
                        // eslint-disable-next-line @typescript-eslint/no-explicit-any
                        setMemoryPreview({ id: mid, content: (m as any)?.content || "" }),
                      )
                      .catch(() =>
                        setMemoryPreview({ id: mid, content: selected.content || "" }),
                      );
                  }}
                  className="text-xs px-2 py-1 rounded bg-primary text-primary-foreground hover:opacity-90"
                >
                  View memory
                </button>
              )}
              <button
                onClick={() =>
                  setToggles((t) => ({ ...t, localMode: !t.localMode }))
                }
                className="text-xs px-2 py-1 rounded bg-muted hover:bg-muted/70"
              >
                {toggles.localMode ? "Exit local" : "Local graph"}
              </button>
            </div>
          </div>
        )}

        {memoryPreview && (
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm z-20 flex items-center justify-center p-6"
            onClick={() => setMemoryPreview(null)}
          >
            <div
              className="bg-background border border-border rounded-lg max-w-2xl w-full max-h-[80vh] overflow-y-auto p-5 shadow-xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm font-mono text-muted-foreground">
                  {memoryPreview.id}
                </span>
                <button
                  onClick={() => setMemoryPreview(null)}
                  className="text-muted-foreground hover:text-foreground text-lg leading-none px-1"
                >
                  &times;
                </button>
              </div>
              <pre className="text-sm whitespace-pre-wrap font-sans">
                {memoryPreview.content || "(empty)"}
              </pre>
            </div>
          </div>
        )}

        <div className="absolute bottom-3 right-3 z-10 text-xs text-muted-foreground bg-background/80 backdrop-blur border border-border rounded px-2 py-1 pointer-events-none">
          {filtered.nodes.length} nodes · {filtered.edges.length} edges
          {loading && " · loading…"}
        </div>
      </div>
    </div>
  );
}
