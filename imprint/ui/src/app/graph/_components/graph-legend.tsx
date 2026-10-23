"use client";

import { Eye, EyeOff } from "lucide-react";
import type { GraphEdgeKind, GraphNodeKind } from "@/lib/types";
import { EDGE_KINDS, KIND_ORDER, KINDS } from "./graph-style";

/** Node swatch: projects in their own colours (a spectrum stands for
 * "each project has its colour"), topics as rings, the rest solid. */
function Swatch({ kind }: { kind: GraphNodeKind }) {
  const color = KINDS[kind].color;
  if (kind === "project") {
    return <span className="size-3 rounded-full bg-[conic-gradient(#ff6b6b,#fbbf24,#34d399,#38bdf8,#a78bfa,#f472b6,#ff6b6b)]" aria-hidden />;
  }
  if (kind === "topic") {
    return <span className="size-3 rounded-full border-[1.5px]" style={{ borderColor: color, background: `${color}1a` }} aria-hidden />;
  }
  return <span className="size-2.5 rounded-full" style={{ background: color }} aria-hidden />;
}

function EdgeSample({ kind }: { kind: GraphEdgeKind }) {
  const style = EDGE_KINDS[kind];
  return (
    <svg width="18" height="8" viewBox="0 0 18 8" aria-hidden className="shrink-0">
      <line x1="1" y1="4" x2={style.arrow ? 13 : 17} y2="4" stroke={style.color ?? "#737373"} strokeWidth="1.5" strokeDasharray={style.dash?.join(" ")} />
      {style.arrow && <path d="M12 1 L17 4 L12 7 Z" fill={style.color} />}
    </svg>
  );
}

export function GraphLegend({
  counts,
  hidden,
  onToggle,
  edgeKinds,
  shown,
}: {
  /** Nodes of each kind in this scope, before hiding. */
  counts: Record<GraphNodeKind, number>;
  hidden: Set<GraphNodeKind>;
  onToggle: (kind: GraphNodeKind) => void;
  edgeKinds: GraphEdgeKind[];
  shown: { nodes: number; edges: number };
}) {
  const kinds = KIND_ORDER.filter((k) => counts[k] > 0);
  return (
    <div className="w-48 rounded-xl border border-border bg-card/90 p-2 text-xs shadow-lg backdrop-blur">
      <ul aria-label="Node kinds" className="space-y-0.5">
        {kinds.map((kind) => {
          const visible = !hidden.has(kind);
          const Toggle = visible ? Eye : EyeOff;
          return (
            <li key={kind}>
              <button
                type="button"
                aria-pressed={visible}
                onClick={() => onToggle(kind)}
                title={visible ? `Hide ${KINDS[kind].plural.toLowerCase()}` : `Show ${KINDS[kind].plural.toLowerCase()}`}
                className="group flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left transition-colors hover:bg-muted"
              >
                <span className={`grid size-3 place-items-center ${visible ? "" : "opacity-40"}`}><Swatch kind={kind} /></span>
                <span className={`flex-1 ${visible ? "text-foreground" : "text-muted-foreground line-through"}`}>{KINDS[kind].plural}</span>
                <span className="tabular-nums text-muted-foreground">{counts[kind]}</span>
                <Toggle className="size-3.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" aria-hidden />
              </button>
            </li>
          );
        })}
      </ul>
      {edgeKinds.length > 0 && (
        <ul aria-label="Connections" className="mt-1.5 space-y-1 border-t border-border px-1.5 pt-2">
          {edgeKinds.map((kind) => (
            <li key={kind} className="flex items-center gap-2 text-muted-foreground">
              <EdgeSample kind={kind} />
              {EDGE_KINDS[kind].label}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2 border-t border-border px-1.5 pt-1.5 text-[11px] text-muted-foreground">
        {shown.nodes} nodes · {shown.edges} connections
      </p>
    </div>
  );
}
