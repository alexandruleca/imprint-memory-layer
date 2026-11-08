import type { GraphEdge, GraphNode, GraphScopeData, MemoryNode } from "@/lib/types";

/** The Search page's filters, carried in the graph's URL beside the scope. */
export interface SearchFilters {
  project: string;
  type: string;
  mode: "hybrid" | "semantic";
}

export const NO_FILTERS: SearchFilters = { project: "", type: "", mode: "hybrid" };

export const SEARCH_PREFIX = "search:";

export const isSearchScope = (scope: string) => scope.startsWith(SEARCH_PREFIX);

export function searchQuery(scope: string): string {
  return isSearchScope(scope) ? scope.slice(SEARCH_PREFIX.length) : "";
}

export function filtersFrom(params: URLSearchParams): SearchFilters {
  return {
    project: params.get("project") ?? "",
    type: params.get("type") ?? "",
    mode: params.get("mode") === "semantic" ? "semantic" : "hybrid",
  };
}

/** The graph URL of a search: the scope plus whichever filters are set. */
export function searchHref(query: string, filters: Partial<SearchFilters> = {}, base = "/graph/"): string {
  const params = new URLSearchParams({ scope: `${SEARCH_PREFIX}${query}` });
  if (filters.project) params.set("project", filters.project);
  if (filters.type) params.set("type", filters.type);
  if (filters.mode === "semantic") params.set("mode", "semantic");
  return `${base}?${params}`;
}

const SOURCE_COLOR = "#94a3b8";
const CHUNK_COLOR = "#64748b";
const PROJECT_COLORS = ["#ff6b6b", "#4ecdc4", "#45b7d1", "#96ceb4", "#ffeaa7", "#dfe6e9", "#fd79a8", "#a29bfe", "#6c5ce7", "#00b894", "#e17055", "#0984e3"];

function fallbackColor(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
  return PROJECT_COLORS[Math.abs(h) % PROJECT_COLORS.length];
}

/**
 * Search hits as a graph scope: each hit a chunk node ranked by the search,
 * linked to its source, the source to its project, and the hit to its
 * topics, so hits that share a conversation, file, project or topic
 * cluster. Built from the search response alone: every hit carries its
 * provenance and tags.
 */
export function searchScopeData(scope: string, hits: MemoryNode[], colors: Map<string, string> = new Map()): GraphScopeData {
  // Projects keep the colour the rest of the graph gives them.
  const projectColor = (name: string) => colors.get(name) ?? fallbackColor(name);
  const nodes = new Map<string, GraphNode>();
  const edges = new Map<string, GraphEdge>();
  const link = (source: string, target: string, kind: GraphEdge["kind"]) => {
    const id = `${kind}:${source}->${target}`;
    const existing = edges.get(id);
    if (existing) existing.weight += 1;
    else edges.set(id, { id, source, target, kind, weight: 1 });
  };
  const bump = (node: GraphNode) => {
    const existing = nodes.get(node.id);
    if (existing) existing.count += 1;
    else nodes.set(node.id, node);
  };
  hits.forEach((hit, index) => {
    const id = `chunk:${hit.id}`;
    nodes.set(id, {
      id,
      kind: "chunk",
      label: hit.label || hit.source?.split("/").pop() || hit.content.slice(0, 80),
      count: 1,
      color: CHUNK_COLOR,
      chunk_index: hit.chunk_index,
      project: hit.project,
      content: hit.content,
      fullPath: hit.source || undefined,
      rank: index + 1,
    });
    if (hit.source) {
      const src = `src:${hit.source}`;
      bump({ id: src, kind: "source", label: hit.source.split("/").pop() || hit.source, fullPath: hit.source, count: 1, color: SOURCE_COLOR, project: hit.project });
      link(src, id, "contains");
      if (hit.project) {
        bump({ id: `proj:${hit.project}`, kind: "project", label: hit.project, count: 1, color: projectColor(hit.project) });
        link(`proj:${hit.project}`, src, "contains");
      }
    } else if (hit.project) {
      bump({ id: `proj:${hit.project}`, kind: "project", label: hit.project, count: 1, color: projectColor(hit.project) });
      link(`proj:${hit.project}`, id, "contains");
    }
    for (const topic of hit.tags?.topics ?? []) {
      if (!topic) continue;
      bump({ id: `topic:${topic}`, kind: "topic", label: topic, count: 1, color: "#a78bfa" });
      link(id, `topic:${topic}`, "relates");
    }
  });
  return { scope, center: "", nodes: [...nodes.values()], edges: [...edges.values()] };
}

/** How strongly each hit is lit: 1 for the best, fading with rank. */
export function hitStrengths(nodes: GraphNode[]): Map<string, number> | null {
  const ranked = nodes.filter((n) => n.rank !== undefined);
  if (!ranked.length) return null;
  const worst = Math.max(...ranked.map((n) => n.rank!));
  return new Map(ranked.map((n) => [n.id, worst <= 1 ? 1 : 1 - ((n.rank! - 1) / (worst - 1)) * 0.75]));
}
