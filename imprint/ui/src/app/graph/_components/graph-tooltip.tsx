"use client";

import { useImperativeHandle, useRef, useState, type Ref } from "react";
import type { GraphNode } from "@/lib/types";
import { KINDS, nodeColor } from "./graph-style";

export interface TooltipHandle {
  /** Show a node at a pointer position (px within the canvas), or hide. */
  show(node: GraphNode | null, x?: number, y?: number): void;
  /** Follow the pointer without re-rendering. */
  move(x: number, y: number): void;
}

const WIDTH = 240;

/** The hovered node's name and what a double-click does. React renders it
 * only when the node changes; pointer moves restyle the element directly. */
export function GraphTooltip({ ref }: { ref?: Ref<TooltipHandle> }) {
  const [node, setNode] = useState<GraphNode | null>(null);
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
    show(next, x, y) {
      if (x !== undefined && y !== undefined) at.current = { x, y };
      setNode((prev) => (prev?.id === next?.id ? prev : next));
      place();
    },
    move(x, y) {
      at.current = { x, y };
      place();
    },
  }), []);

  if (!node) return <div ref={el} hidden />;
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
      <p className="mt-1 text-[11px] text-muted-foreground/70">
        {node.kind === "chunk" ? "Double-click to read" : "Double-click to open"}
      </p>
    </div>
  );
}
