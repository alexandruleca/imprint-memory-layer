"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { ChevronDown, ChevronUp, Network, RotateCw, Search as SearchIcon, X } from "lucide-react";
import { getGraphScope, getMemory, getTopicDetail } from "@/lib/api";
import { ChunkDialog, type ChunkPreview } from "@/components/chunk-dialog";
import { GraphSkeleton, Spinner } from "@/components/loaders";
import type { GraphEdge, GraphEdgeKind, GraphNode, GraphNodeKind, GraphScopeData, MemoryNode } from "@/lib/types";
import { GraphBreadcrumbs, scopeKind, type ScopeCrumb } from "./_components/breadcrumbs";
import { GraphCanvas, type GraphCanvasHandle, type GraphForces } from "./_components/graph-canvas";
import { GraphLegend } from "./_components/graph-legend";
import { GraphToolbar, type GraphSettings } from "./_components/graph-toolbar";
import { NodeDetails } from "./_components/node-details";
import { KINDS } from "./_components/graph-style";

const DEFAULT_FORCES: GraphForces = { repel: 900, collide: 14, centerStrength: 0.06 };
const DEFAULT_SETTINGS: GraphSettings = { showLabels: true, showEdges: true, depth: 1, forces: DEFAULT_FORCES };
const STORAGE_KEY = "imprint:graph:v2";
const ROOT: ScopeCrumb = { scope: "root", label: "Overview" };
const EDGE_ORDER: GraphEdgeKind[] = ["contains", "relates", "sequence", "similar"];
/** Height of the details bottom sheet on narrow screens, as a fraction of the canvas. */
const SHEET_HEIGHT = 0.55;

interface SavedState {
  settings?: Partial<GraphSettings>;
  hidden?: GraphNodeKind[];
}

function savedState(): SavedState {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"); } catch { return {}; }
}

const NARROW = "(max-width: 639px)";

/** Below the sm breakpoint the details panel is a bottom sheet. */
function useNarrow(): boolean {
  return useSyncExternalStore(
    (notify) => { const q = window.matchMedia(NARROW); q.addEventListener("change", notify); return () => q.removeEventListener("change", notify); },
    () => window.matchMedia(NARROW).matches,
    () => false,
  );
}

function scopeFromUrl(): string {
  return new URLSearchParams(window.location.search).get("scope") || "root";
}

function crumbFor(scope: string): ScopeCrumb {
  const value = scope.slice(scope.indexOf(":") + 1);
  const kind = scopeKind(scope);
  return { scope, label: kind === "source" ? value.split("/").pop() || value : kind === "chunk" ? "Chunk" : value };
}

function scopeOf(node: GraphNode): string {
  if (node.kind === "project") return `project:${node.label}`;
  if (node.kind === "topic") return `topic:${node.label}`;
  if (node.kind === "source") return `source:${node.fullPath || node.label}`;
  return `chunk:${node.id.replace(/^chunk:/, "")}`;
}

export default function GraphPage() {
  const mounted = useSyncExternalStore(() => () => {}, () => true, () => false);
  return mounted ? <GraphPageContent /> : <GraphSkeleton />;
}

function GraphPageContent() {
  const canvas = useRef<GraphCanvasHandle>(null);
  const findRef = useRef<HTMLInputElement>(null);
  const narrow = useNarrow();
  const [saved] = useState(savedState);
  const [trail, setTrail] = useState<ScopeCrumb[]>(() => {
    const scope = scopeFromUrl();
    return scope === "root" ? [ROOT] : [ROOT, crumbFor(scope)];
  });
  const [settings, setSettings] = useState<GraphSettings>(() => {
    const s = { ...DEFAULT_SETTINGS, ...saved.settings };
    return { ...s, depth: [1, 2, 3].includes(s.depth) ? s.depth : 1, forces: { ...DEFAULT_FORCES, ...saved.settings?.forces } };
  });
  const [hidden, setHidden] = useState<Set<GraphNodeKind>>(() => new Set((saved.hidden ?? []).filter((k) => k in KINDS)));
  const [graphResult, setGraphResult] = useState<{ key: string; data: GraphScopeData | null; error?: string }>({ key: "", data: null });
  const [reload, setReload] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [neighborhood, setNeighborhood] = useState(false);
  const [query, setQuery] = useState("");
  const [matchIndex, setMatchIndex] = useState(-1);
  const [chunkDialog, setChunkDialog] = useState<ChunkPreview | null>(null);
  const [topicResult, setTopicResult] = useState<{ key: string; nodes: MemoryNode[] }>({ key: "", nodes: [] });

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ settings, hidden: [...hidden] })); } catch {}
  }, [settings, hidden]);

  const scope = trail[trail.length - 1].scope;
  const graphKey = `${scope}:${settings.depth}:${reload}`;
  const loading = graphResult.key !== graphKey;
  // While a scope loads, the previous graph stays on screen.
  const data = graphResult.data;

  useEffect(() => {
    let cancelled = false;
    getGraphScope(scope, settings.depth).then((result) => {
      if (!cancelled) setGraphResult({ key: graphKey, data: result });
    }).catch((error) => {
      if (!cancelled) setGraphResult((prev) => ({ key: graphKey, data: prev.data, error: error instanceof Error ? error.message : String(error) }));
    });
    return () => { cancelled = true; };
  }, [scope, settings.depth, graphKey]);

  // Back/forward move along the trail; an unknown scope starts a new one.
  useEffect(() => {
    const onPop = () => {
      const next = scopeFromUrl();
      setTrail((t) => {
        const i = t.findIndex((c) => c.scope === next);
        return i >= 0 ? t.slice(0, i + 1) : next === "root" ? [ROOT] : [ROOT, crumbFor(next)];
      });
      setSelectedId(null);
      setNeighborhood(false);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const goTo = useCallback((next: ScopeCrumb[]) => {
    const target = next[next.length - 1].scope;
    window.history.pushState(null, "", target === "root" ? window.location.pathname : `?scope=${encodeURIComponent(target)}`);
    setTrail(next);
    setSelectedId(null);
    setNeighborhood(false);
    setQuery("");
  }, []);

  const counts = useMemo(() => {
    const c = { project: 0, topic: 0, source: 0, chunk: 0 } as Record<GraphNodeKind, number>;
    for (const n of data?.nodes ?? []) c[n.kind] += 1;
    return c;
  }, [data]);

  // Only the neighbourhood filter depends on the selection, so selecting
  // a node never re-runs the layout otherwise.
  const anchor = neighborhood ? selectedId ?? data?.center ?? "" : "";
  const visible = useMemo(() => {
    if (!data) return { nodes: [] as GraphNode[], edges: [] as GraphEdge[] };
    let nodes = data.nodes.filter((n) => !hidden.has(n.kind) || n.focus);
    if (anchor) {
      const ring = new Set([anchor]);
      for (const e of data.edges) {
        if (e.source === anchor) ring.add(e.target);
        if (e.target === anchor) ring.add(e.source);
      }
      nodes = nodes.filter((n) => ring.has(n.id));
    }
    const ids = new Set(nodes.map((n) => n.id));
    return { nodes, edges: data.edges.filter((e) => ids.has(e.source) && ids.has(e.target)) };
  }, [data, hidden, anchor]);

  const matchList = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? visible.nodes.filter((n) => n.label.toLowerCase().includes(q) || n.fullPath?.toLowerCase().includes(q)) : null;
  }, [query, visible.nodes]);
  const matches = useMemo(() => (matchList ? new Set(matchList.map((n) => n.id)) : null), [matchList]);

  const selected = (selectedId && visible.nodes.find((n) => n.id === selectedId)) || null;
  const neighbors = useMemo(() => {
    if (!selected) return [];
    const ids = new Set<string>();
    for (const e of visible.edges) {
      if (e.source === selected.id) ids.add(e.target);
      if (e.target === selected.id) ids.add(e.source);
    }
    return visible.nodes.filter((n) => ids.has(n.id)).sort((a, b) => b.count - a.count);
  }, [selected, visible]);

  const currentProject = useMemo(() => {
    const crumb = [...trail].reverse().find((c) => c.scope.startsWith("project:"));
    return crumb ? crumb.scope.slice("project:".length) : null;
  }, [trail]);
  const topicKey = selected?.kind === "topic" ? `${currentProject}:${selected.id}` : "";
  const topicChunks = topicKey && topicResult.key === topicKey ? topicResult.nodes : null;
  useEffect(() => {
    if (!selected || selected.kind !== "topic") return;
    let cancelled = false;
    getTopicDetail(selected.label).then((result) => {
      if (!cancelled) setTopicResult({ key: topicKey, nodes: currentProject ? result.nodes.filter((n) => n.project === currentProject) : result.nodes });
    }).catch(() => { if (!cancelled) setTopicResult({ key: topicKey, nodes: [] }); });
    return () => { cancelled = true; };
  }, [selected, currentProject, topicKey]);

  const readChunk = useCallback((node: GraphNode) => {
    const fallback = { label: node.label, source: node.fullPath, project: node.project, content: node.content || "" };
    getMemory(node.id.replace(/^chunk:/, ""))
      .then((m) => setChunkDialog({ ...fallback, content: m.content || fallback.content }))
      .catch(() => setChunkDialog(fallback));
  }, []);

  const open = useCallback((node: GraphNode) => {
    if (node.kind === "chunk") readChunk(node);
    else goTo([...trail, { scope: scopeOf(node), label: node.label }]);
  }, [goTo, readChunk, trail]);

  const openById = useCallback((id: string) => {
    const node = visible.nodes.find((n) => n.id === id);
    if (node) open(node);
  }, [open, visible.nodes]);

  const select = useCallback((id: string | null) => {
    setSelectedId(id);
    if (id) canvas.current?.focus(id);
  }, []);

  const stepMatch = useCallback((direction: 1 | -1) => {
    if (!matchList?.length) return;
    const next = (matchIndex + direction + matchList.length) % matchList.length;
    setMatchIndex(next);
    select(matchList[next].id);
  }, [matchIndex, matchList, select]);

  // "/" finds, Escape clears the query, then the selection.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (chunkDialog) return;
      const typing = event.target instanceof HTMLElement && event.target.closest("input, textarea, select, [contenteditable]");
      if (event.key === "/" && !typing) {
        event.preventDefault();
        findRef.current?.focus();
      } else if (event.key === "Escape") {
        if (query) { setQuery(""); setMatchIndex(-1); } else if (selectedId) setSelectedId(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [chunkDialog, query, selectedId]);

  const centerNode = data?.nodes.find((n) => n.id === data.center);
  const crumbs = trail.map((c, i) => (i === trail.length - 1 && centerNode && !loading ? { ...c, label: centerNode.label } : c));
  const edgeKinds = EDGE_ORDER.filter((k) => visible.edges.some((e) => e.kind === k));
  const kind = scopeKind(scope);
  const empty = !loading && !graphResult.error && data && data.nodes.length === 0;
  const allHidden = data && data.nodes.length > 0 && visible.nodes.length === 0;

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-4 py-2.5">
        <h2 className="text-lg font-semibold">Graph</h2>
        <GraphBreadcrumbs crumbs={crumbs} onGo={(i) => goTo(trail.slice(0, i + 1))} />
        <div className="flex w-full items-center gap-1 rounded-lg border border-input bg-input/30 px-2.5 focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50 sm:ml-auto sm:w-72">
          <SearchIcon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <input
            ref={findRef}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setMatchIndex(-1); }}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); stepMatch(e.shiftKey ? -1 : 1); } }}
            placeholder="Find in graph"
            aria-label="Find in graph"
            className="h-8 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          {matchList ? (
            <>
              <span className="shrink-0 text-xs tabular-nums text-muted-foreground" role="status">
                {matchList.length === 0 ? "No matches" : `${matchIndex >= 0 ? matchIndex + 1 : 0}/${matchList.length}`}
              </span>
              <button type="button" aria-label="Previous match" onClick={() => stepMatch(-1)} disabled={!matchList.length} className="grid size-6 place-items-center rounded text-muted-foreground hover:text-foreground disabled:opacity-40"><ChevronUp className="size-3.5" /></button>
              <button type="button" aria-label="Next match" onClick={() => stepMatch(1)} disabled={!matchList.length} className="grid size-6 place-items-center rounded text-muted-foreground hover:text-foreground disabled:opacity-40"><ChevronDown className="size-3.5" /></button>
              <button type="button" aria-label="Clear search" onClick={() => { setQuery(""); setMatchIndex(-1); }} className="grid size-6 place-items-center rounded text-muted-foreground hover:text-foreground"><X className="size-3.5" /></button>
            </>
          ) : (
            <kbd className="hidden shrink-0 rounded border border-border px-1.5 font-mono text-[10px] text-muted-foreground sm:block">/</kbd>
          )}
        </div>
      </header>

      <div className="relative flex-1 overflow-hidden bg-[radial-gradient(ellipse_at_center,oklch(0.19_0_0),var(--background)_70%)]">
        <GraphCanvas
          ref={canvas}
          nodes={visible.nodes}
          edges={visible.edges}
          showLabels={settings.showLabels}
          showEdges={settings.showEdges}
          forces={settings.forces}
          selectedId={selectedId}
          matches={matches}
          insetBottom={narrow && selected ? SHEET_HEIGHT : 0}
          onSelect={narrow ? select : setSelectedId}
          onOpen={openById}
        />

        {!data && !graphResult.error && <div className="absolute inset-0 grid"><GraphSkeleton /></div>}
        {loading && data && (
          <div className="pointer-events-none absolute left-1/2 top-3 z-20 flex -translate-x-1/2 items-center gap-2 rounded-full border border-border bg-card/90 px-3 py-1.5 text-xs text-muted-foreground shadow-lg backdrop-blur" role="status">
            <Spinner className="size-3.5" /> Loading {kind ? KINDS[kind].label.toLowerCase() : "overview"}…
          </div>
        )}
        {!loading && graphResult.error && (
          <div role="alert" aria-label="Graph error" className="absolute left-1/2 top-3 z-20 flex w-[min(28rem,calc(100%-1.5rem))] -translate-x-1/2 items-center gap-3 rounded-xl border border-destructive/50 bg-card/95 p-3 text-sm shadow-lg backdrop-blur">
            <span className="min-w-0 flex-1">The graph could not load: {graphResult.error}</span>
            <button type="button" onClick={() => setReload((r) => r + 1)} className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs hover:bg-muted">
              <RotateCw className="size-3.5" aria-hidden /> Retry
            </button>
          </div>
        )}
        {empty && (
          <CenteredNote title={scope === "root" ? "No memories in this workspace yet" : "Nothing to show here"}>
            {scope === "root" ? (
              <>Index a project with <code className="rounded bg-muted px-1 font-mono text-xs">imprint ingest &lt;dir&gt;</code> or use quick ingest on the <Link href="/" className="underline underline-offset-4 hover:text-foreground">dashboard</Link>; projects and their topics appear here.</>
            ) : "This scope has no connected memories."}
          </CenteredNote>
        )}
        {allHidden && (
          <CenteredNote title="Every kind is hidden">
            <button type="button" onClick={() => setHidden(new Set())} className="mt-1 rounded-lg border border-border px-3 py-1 text-xs text-foreground hover:bg-muted">Show all</button>
          </CenteredNote>
        )}

        {neighborhood && selected && (
          <div className="absolute left-1/2 top-3 z-10 flex max-w-[calc(100%-1.5rem)] -translate-x-1/2 items-center gap-2 rounded-full border border-border bg-card/90 py-1 pl-3 pr-1 text-xs shadow-lg backdrop-blur">
            <Network className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
            <span className="truncate">Connections of <span className="font-medium">{selected.label}</span></span>
            <button type="button" onClick={() => setNeighborhood(false)} className="shrink-0 rounded-full px-2 py-0.5 text-muted-foreground hover:bg-muted hover:text-foreground">Show all</button>
          </div>
        )}

        {data && data.nodes.length > 0 && (
          <div className={`absolute bottom-3 left-3 z-10 ${selected ? "hidden sm:block" : ""}`}>
            <GraphLegend
              counts={counts}
              hidden={hidden}
              onToggle={(k) => setHidden((h) => { const next = new Set(h); if (next.has(k)) next.delete(k); else next.add(k); return next; })}
              edgeKinds={edgeKinds}
              shown={{ nodes: visible.nodes.length, edges: settings.showEdges ? visible.edges.length : 0 }}
            />
          </div>
        )}

        <div className={`absolute bottom-3 right-3 z-10 ${!data?.nodes.length ? "hidden" : selected ? "hidden sm:block sm:right-[21.75rem]" : ""}`}>
          <GraphToolbar
            settings={settings}
            onSettings={setSettings}
            breadthApplies={kind === "project" || kind === "topic" || kind === "source"}
            onZoom={(r) => canvas.current?.zoomBy(r)}
            onFit={() => canvas.current?.fit()}
            onRelayout={() => canvas.current?.relayout()}
            onReset={() => { setSettings(DEFAULT_SETTINGS); setHidden(new Set()); }}
          />
        </div>

        {selected && (
          <div className="absolute inset-x-2 bottom-2 z-20 max-h-[55%] sm:inset-x-auto sm:bottom-3 sm:right-3 sm:top-3 sm:max-h-none sm:w-80">
            <NodeDetails
              node={selected}
              neighbors={neighbors}
              topicChunks={topicChunks}
              topicContext={currentProject}
              neighborhood={neighborhood}
              onClose={() => setSelectedId(null)}
              onOpen={open}
              onSelect={select}
              onReadChunk={(chunk) => setChunkDialog(chunk)}
              onNeighborhood={() => setNeighborhood((v) => !v)}
            />
          </div>
        )}
      </div>

      <ChunkDialog chunk={chunkDialog} onClose={() => setChunkDialog(null)} />
    </div>
  );
}

function CenteredNote({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center p-6">
      <div className="pointer-events-auto max-w-sm rounded-xl border border-border bg-card/90 p-5 text-center shadow-lg backdrop-blur">
        <Network className="mx-auto size-6 text-muted-foreground" aria-hidden />
        <p className="mt-2 font-medium">{title}</p>
        <div className="mt-1 text-sm text-muted-foreground">{children}</div>
      </div>
    </div>
  );
}
