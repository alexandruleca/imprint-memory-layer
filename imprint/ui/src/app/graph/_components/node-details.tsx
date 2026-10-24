"use client";

import { useState } from "react";
import { ArrowRight, Check, Copy, Network, X } from "lucide-react";
import { toast } from "sonner";
import { Hint } from "@/components/hint";
import { Spinner } from "@/components/loaders";
import { Button } from "@/components/ui/button";
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
        <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close details" className="-m-1 text-muted-foreground">
          <X aria-hidden />
        </Button>
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
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><Spinner className="size-3" /> Loading…</p>
            ) : topicChunks.length === 0 ? (
              <p className="text-xs text-muted-foreground">No chunks found.</p>
            ) : (
              <ul className="max-h-48 overflow-y-auto rounded-lg border border-border">
                {topicChunks.map((chunk) => (
                  <li key={chunk.id} className="border-b border-border/60 last:border-0">
                    <Button
                      variant="ghost"
                      onClick={() => onReadChunk(chunk)}
                      title={chunk.label}
                      className="h-auto w-full justify-start rounded-none px-2.5 py-1.5 text-left text-xs font-normal"
                    >
                      <span className="truncate">
                        <span className="mr-1.5 font-mono text-muted-foreground">#{chunk.chunk_index}</span>
                        {chunk.label}
                      </span>
                    </Button>
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
        <Button size="sm" onClick={() => onOpen(node)} className="flex-1 text-xs">
          {node.kind === "chunk" ? "Read chunk" : `Open ${kind.label.toLowerCase()}`}
          <ArrowRight aria-hidden />
        </Button>
        <Button variant="outline" size="sm" onClick={onNeighborhood} aria-pressed={neighborhood} className="text-xs aria-pressed:bg-muted">
          <Network aria-hidden />
          {neighborhood ? "Show all" : "Only its connections"}
        </Button>
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
            <Button
              variant="outline"
              size="xs"
              onClick={() => onSelect(n.id)}
              title={n.label}
              className="max-w-full shrink gap-1.5 rounded-md bg-background/60 px-2 font-normal hover:border-foreground/30"
            >
              <span className="size-1.5 shrink-0 rounded-full" style={{ background: nodeColor(n) }} aria-hidden />
              <span className="truncate">{n.label}</span>
            </Button>
          </li>
        ))}
        {nodes.length > SHOWN_PER_KIND && (
          <li>
            <Button variant="ghost" size="xs" onClick={() => setAll((v) => !v)} className="font-normal text-muted-foreground">
              {all ? "Show fewer" : `+${nodes.length - SHOWN_PER_KIND} more`}
            </Button>
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
    }).catch(() => toast.error("Could not copy the path"));
  };
  const Icon = copied ? Check : Copy;
  return (
    <div className="flex items-start gap-2 rounded-lg bg-muted/50 px-3 py-2">
      <code className="min-w-0 flex-1 break-all font-mono text-[11px] leading-relaxed text-muted-foreground">{path}</code>
      <Hint label={copied ? "Copied" : "Copy path"}>
        <Button variant="ghost" size="icon-xs" onClick={copy} aria-label={copied ? "Path copied" : "Copy path"} className="-mr-1 text-muted-foreground">
          <Icon className="size-3.5" aria-hidden />
        </Button>
      </Hint>
    </div>
  );
}
