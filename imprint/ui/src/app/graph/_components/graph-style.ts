import { FileText, FolderGit2, Puzzle, Tag, type LucideIcon } from "lucide-react";
import type { GraphEdgeKind, GraphNode, GraphNodeKind } from "@/lib/types";

/** Canvas colours, matched to the dashboard's light tokens (#fafafa
 * page, #0a0a0a ink). The canvas cannot read CSS variables, so they
 * live here. */
export const CANVAS = {
  label: "#262626",
  labelHalo: "#ffffff",
  focusRing: "#0a0a0a",
  edge: "#737373",
};

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
  topic: { label: "Topic", plural: "Topics", icon: Tag, color: "#404040", size: [14, 46] },
  source: { label: "Source", plural: "Sources", icon: FileText, color: "#737373", size: [12, 28] },
  chunk: { label: "Chunk", plural: "Chunks", icon: Puzzle, color: "#a3a3a3", size: [9, 14] },
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

export function shortLabel(label: string, max = 28): string {
  return label.length > max ? `${label.slice(0, max - 1)}…` : label;
}
