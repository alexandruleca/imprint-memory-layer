"use client";

import { useState } from "react";
import { ArrowRight, Check, Copy, Network, X } from "lucide-react";
import type { GraphNode, MemoryNode } from "@/lib/types";
import { KIND_ORDER, KINDS, nodeColor } from "./graph-style";

const SHOWN_PER_KIND = 6;

export function NodeDetails({
  node,
  neighbors,
  topicChunks,
  topicContext,
  neighborhood,
  onClose,
  onOpen,
  onSelect,
  onReadChunk,
  onNeighborhood,
}: {
  node: GraphNode;
  neighbors: GraphNode[];
  /** For a topic: its chunks (null while loading). */
  topicChunks: MemoryNode[] | null;
  /** Project the topic's chunks are limited to, if any. */
  topicContext: string | null;
  neighborhood: boolean;
  onClose: () => void;
  onOpen: (node: GraphNode) => void;
  onSelect: (id: string) => void;
  onReadChunk: (chunk: MemoryNode) => void;
  onNeighborhood: () => void;
}) {
  const kind = KINDS[node.kind];
  const Icon = kind.icon;
  const color = nodeColor(node);
  const groups = KIND_ORDER.map((k) => ({ kind: k, nodes: neighbors.filter((n) => n.kind === k) })).filter((g) => g.nodes.length);

  return (
    <aside aria-label={`${kind.label} details`} className="flex max-h-full flex-col overflow-hidden rounded-xl border border-border bg-card/95 shadow-2xl backdrop-blur">
      <div className="flex items-start gap-3 border-b border-border p-4">
        <span className="grid size-9 shrink-0 place-items-center rounded-lg" style={{ background: `${color}26`, color }}>
          <Icon className="size-4.5" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="line-clamp-2 break-words font-semibold leading-snug" title={node.label}>{node.label}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {kind.label}
            {node.count > 0 && node.kind !== "chunk" && ` · ${node.count.toLocaleString()} ${node.count === 1 ? "memory" : "memories"}`}
            {node.kind === "chunk" && node.chunk_index !== undefined && ` #${node.chunk_index}`}
            {node.kind === "chunk" && node.project && ` · ${node.project}`}
          </p>
        </div>
        <button type="button" onClick={onClose} aria-label="Close details" className="-m-1 grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground">
          <X className="size-4" aria-hidden />
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        {node.fullPath && <PathRow path={node.fullPath} />}

        {node.content && (
          <p className="line-clamp-6 whitespace-pre-wrap rounded-lg bg-muted/50 p-3 text-xs leading-relaxed text-foreground/85">{node.content}</p>
        )}

        {node.kind === "topic" && (
          <section>
            <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              {topicContext ? `Chunks in ${topicContext}` : "Chunks"}
              {topicChunks && ` · ${topicChunks.length}`}
            </h4>
            {topicChunks === null ? (
              <p className="text-xs text-muted-foreground">Loading…</p>
            ) : topicChunks.length === 0 ? (
              <p className="text-xs text-muted-foreground">No chunks found.</p>
            ) : (
              <ul className="max-h-48 overflow-y-auto rounded-lg border border-border">
                {topicChunks.map((chunk) => (
                  <li key={chunk.id} className="border-b border-border/60 last:border-0">
                    <button type="button" onClick={() => onReadChunk(chunk)} title={chunk.label} className="block w-full truncate px-2.5 py-1.5 text-left text-xs hover:bg-muted/60">
                      <span className="mr-1.5 font-mono text-muted-foreground">#{chunk.chunk_index}</span>
                      {chunk.label}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {groups.length > 0 && (
          <section>
            <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Connections</h4>
            <div className="space-y-3">
              {groups.map((g) => <NeighborGroup key={g.kind} kind={g.kind} nodes={g.nodes} onSelect={onSelect} />)}
            </div>
          </section>
        )}
      </div>

      <div className="flex gap-2 border-t border-border p-3">
        <button
          type="button"
          onClick={() => onOpen(node)}
          className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition-opacity hover:opacity-90"
        >
          {node.kind === "chunk" ? "Read chunk" : `Open ${kind.label.toLowerCase()}`}
          <ArrowRight className="size-3.5" aria-hidden />
        </button>
        <button
          type="button"
          onClick={onNeighborhood}
          aria-pressed={neighborhood}
          className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium transition-colors hover:bg-muted aria-pressed:bg-muted"
        >
          <Network className="size-3.5" aria-hidden />
          {neighborhood ? "Show all" : "Only its connections"}
        </button>
      </div>
    </aside>
  );
}

function NeighborGroup({ kind, nodes, onSelect }: { kind: GraphNode["kind"]; nodes: GraphNode[]; onSelect: (id: string) => void }) {
  const [all, setAll] = useState(false);
  const shown = all ? nodes : nodes.slice(0, SHOWN_PER_KIND);
  return (
    <div>
      <p className="mb-1 text-xs text-muted-foreground">{KINDS[kind].plural} · {nodes.length}</p>
      <ul className="flex flex-wrap gap-1">
        {shown.map((n) => (
          <li key={n.id} className="min-w-0 max-w-full">
            <button
              type="button"
              onClick={() => onSelect(n.id)}
              title={n.label}
              className="flex max-w-full items-center gap-1.5 rounded-md border border-border bg-background/60 px-2 py-0.5 text-xs transition-colors hover:border-foreground/30 hover:bg-muted"
            >
              <span className="size-1.5 shrink-0 rounded-full" style={{ background: nodeColor(n) }} aria-hidden />
              <span className="truncate">{n.label}</span>
            </button>
          </li>
        ))}
        {nodes.length > SHOWN_PER_KIND && (
          <li>
            <button type="button" onClick={() => setAll((v) => !v)} className="rounded-md px-2 py-0.5 text-xs text-muted-foreground hover:text-foreground">
              {all ? "Show fewer" : `+${nodes.length - SHOWN_PER_KIND} more`}
            </button>
          </li>
        )}
      </ul>
    </div>
  );
}

function PathRow({ path }: { path: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(path).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }).catch(() => {});
  };
  const Icon = copied ? Check : Copy;
  return (
    <div className="flex items-start gap-2 rounded-lg bg-muted/50 px-3 py-2">
      <code className="min-w-0 flex-1 break-all font-mono text-[11px] leading-relaxed text-muted-foreground">{path}</code>
      <button type="button" onClick={copy} aria-label={copied ? "Path copied" : "Copy path"} title={copied ? "Copied" : "Copy path"} className="-mr-1 grid size-6 shrink-0 place-items-center rounded text-muted-foreground hover:bg-muted hover:text-foreground">
        <Icon className="size-3.5" aria-hidden />
      </button>
    </div>
  );
}
