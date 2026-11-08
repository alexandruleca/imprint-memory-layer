import { FileText, FolderGit2, Puzzle, Tag, type LucideIcon } from "lucide-react";
import type { GraphEdgeKind, GraphNode, GraphNodeKind } from "@/lib/types";

/** Steps of ink over the page, as the theme defines them: the light
 * theme's #404040 / #737373 / #a3a3a3 greys, and the same steps on a dark
 * page. DOM swatches use these expressions; the canvas resolves them. */
const ink = (percent: number) => `color-mix(in oklab, var(--foreground) ${percent}%, var(--background))`;

export interface KindStyle {
  label: string;
  plural: string;
  icon: LucideIcon;
  /** Fixed colour; projects use their own identity colour instead. */
  color?: string;
  /** Diameter range in px, scaled by memory count. */
  size: [number, number];
}

export const KINDS: Record<GraphNodeKind, KindStyle> = {
  project: { label: "Project", plural: "Projects", icon: FolderGit2, size: [30, 76] },
  topic: { label: "Topic", plural: "Topics", icon: Tag, color: ink(78), size: [14, 46] },
  source: { label: "Source", plural: "Sources", icon: FileText, color: ink(57), size: [12, 28] },
  chunk: { label: "Chunk", plural: "Chunks", icon: Puzzle, color: ink(40), size: [9, 14] },
};

export const KIND_ORDER: GraphNodeKind[] = ["project", "topic", "source", "chunk"];

export const EDGE_KINDS: Record<GraphEdgeKind, { label: string; color?: string; dash?: number[]; arrow?: boolean }> = {
  contains: { label: "Contains" },
  relates: { label: "Tagged with" },
  sequence: { label: "Next chunk", color: "#f59e0b", arrow: true },
  similar: { label: "Similar meaning", color: "#0891b2", dash: [4, 4] },
};

export function nodeColor(node: GraphNode): string {
  return KINDS[node.kind].color ?? node.color;
}

/** Diameter: square-root scaled within the kind's range, so area tracks
 * the number of memories. The scope's focus node is drawn at its kind's
 * full size, and never smaller than a readable 24px. */
export function nodeSize(node: GraphNode, maxCount: Record<GraphNodeKind, number>): number {
  const [min, max] = KINDS[node.kind].size;
  const top = maxCount[node.kind] || 1;
  // Context nodes (a scope's parent project, say) report no count.
  const size = min + Math.sqrt(Math.max(0, node.count) / top) * (max - min);
  return node.focus ? Math.max(max, 24) : size;
}

/** The canvas's colours, read from the page's theme tokens (re-read when
 * the theme changes) so the canvas matches the rest of the dashboard. */
export interface GraphTheme {
  dark: boolean;
  label: string;
  labelHalo: string;
  focusRing: string;
  edge: string;
  /** Kind colours on this background; projects keep their own. */
  kind: Record<GraphNodeKind, string>;
}

let probeCanvas: CanvasRenderingContext2D | null = null;

/** Any CSS colour (oklch, color-mix, var()) as [r, g, b] bytes, or null. */
export function resolveColor(css: string, within: Element = document.body): [number, number, number] | null {
  const probe = document.createElement("span");
  probe.style.color = css;
  probe.style.display = "none";
  within.appendChild(probe);
  const computed = getComputedStyle(probe).color;
  probe.remove();
  probeCanvas ??= Object.assign(document.createElement("canvas"), { width: 1, height: 1 }).getContext("2d", { willReadFrequently: true });
  if (!probeCanvas || !computed) return null;
  probeCanvas.clearRect(0, 0, 1, 1);
  probeCanvas.fillStyle = "#000";
  probeCanvas.fillStyle = computed;
  probeCanvas.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = probeCanvas.getImageData(0, 0, 1, 1).data;
  return a ? [r, g, b] : null;
}

const rgb = (c: [number, number, number]) => `rgb(${c[0]} ${c[1]} ${c[2]})`;

export function readGraphTheme(within: Element): GraphTheme {
  const bg = resolveColor("var(--background)", within) ?? [250, 250, 250];
  const fg = resolveColor("var(--foreground)", within) ?? [10, 10, 10];
  const dark = (0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]) / 255 < 0.45;
  const kind = (k: GraphNodeKind) => rgb(resolveColor(KINDS[k].color!, within) ?? [115, 115, 115]);
  const mix = (t: number) => rgb([0, 1, 2].map((i) => Math.round(fg[i] * t + bg[i] * (1 - t))) as [number, number, number]);
  return {
    dark,
    label: mix(0.86),
    labelHalo: rgb(bg),
    focusRing: rgb(fg),
    edge: mix(0.55),
    kind: { project: "", topic: kind("topic"), source: kind("source"), chunk: kind("chunk") },
  };
}

export function shortLabel(label: string, max = 28): string {
  return label.length > max ? `${label.slice(0, max - 1)}…` : label;
}
