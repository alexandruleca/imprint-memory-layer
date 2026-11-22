"use client";

import { useImperativeHandle, useRef, useState, type Ref } from "react";
import type { GraphNode } from "@/lib/types";
import { KINDS, nodeColor } from "./graph-style";

export interface TooltipHandle {
  /** Show a node at a pointer position (px within the canvas), or hide;
   * for a topic map's hub, with the number of topics in its group. */
  show(node: GraphNode | null, x?: number, y?: number, members?: number): void;
  /** Follow the pointer without re-rendering. */
  move(x: number, y: number): void;
}

const WIDTH = 240;

/** What the topic map says about a hub: the group it leads. */
export function hubLine(members?: number): string {
  return members && members > 1 ? `Main topic of a group of ${members} topics` : "Main topic of its group";
}

/** The hovered node's name and what a double-click does. React renders it
 * only when the node changes; pointer moves restyle the element directly. */
export function GraphTooltip({ ref }: { ref?: Ref<TooltipHandle> }) {
  const [shown, setShown] = useState<{ node: GraphNode; members?: number } | null>(null);
  const el = useRef<HTMLDivElement>(null);
  const at = useRef({ x: 0, y: 0 });

  const place = () => {
    const div = el.current, parent = div?.parentElement;
    if (!div || !parent) return;
    const { x, y } = at.current;
    const w = parent.clientWidth, h = parent.clientHeight;
    // Open away from the canvas edges.
    const flipX = x + 16 + WIDTH > w, flipY = y + 90 > h;
    div.style.left = flipX ? "" : `${x + 16}px`;
    div.style.right = flipX ? `${w - x + 16}px` : "";
    div.style.top = flipY ? "" : `${y + 16}px`;
    div.style.bottom = flipY ? `${h - y + 16}px` : "";
  };

  useImperativeHandle(ref, () => ({
    show(next, x, y, members) {
      if (x !== undefined && y !== undefined) at.current = { x, y };
      setShown((prev) => (prev?.node.id === next?.id && prev?.members === members ? prev : next ? { node: next, members } : null));
      place();
    },
    move(x, y) {
      at.current = { x, y };
      place();
    },
  }), []);

  if (!shown) return <div ref={el} hidden />;
  const { node, members } = shown;
  const kind = KINDS[node.kind];
  const detail =
    node.kind === "chunk"
      ? [node.chunk_index !== undefined ? `Chunk #${node.chunk_index}` : "Chunk", node.project].filter(Boolean).join(" · ")
      : `${kind.label}${node.count > 0 ? ` · ${node.count.toLocaleString()} ${node.count === 1 ? "memory" : "memories"}` : ""}`;
  return (
    <div
      ref={(div) => { el.current = div; if (div) place(); }}
      role="tooltip"
      className="pointer-events-none absolute z-30 rounded-lg border border-border bg-popover/95 px-3 py-2 text-xs shadow-xl backdrop-blur"
      style={{ maxWidth: WIDTH }}
    >
      <div className="flex items-center gap-2 font-medium text-popover-foreground">
        <span className="size-2 shrink-0 rounded-full" style={{ background: nodeColor(node) }} />
        <span className="line-clamp-2 break-words">{node.label}</span>
      </div>
      <p className="mt-0.5 text-muted-foreground">{detail}</p>
      {/* The topic map: the group a hub leads, and where a topic mostly is. */}
      {node.hub && <p className="mt-0.5 text-muted-foreground">{hubLine(members)}</p>}
      {node.kind === "topic" && !!node.projects?.length && (
        <p className="mt-0.5 line-clamp-2 break-words text-muted-foreground">Mostly in {node.projects.map((p) => p.name).join(", ")}</p>
      )}
      <p className="mt-1 text-[11px] text-muted-foreground/70">
        {node.kind === "chunk" ? "Double-click to read" : "Double-click to open"}
      </p>
    </div>
  );
}
