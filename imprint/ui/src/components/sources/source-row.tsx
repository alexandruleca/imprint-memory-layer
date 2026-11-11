"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowRightLeft, BookOpenText, ChevronLeft, ChevronRight, Copy, Ellipsis, Link2, ListCollapse, Network, Trash2,
} from "lucide-react";
import { DeviceProvenance } from "@/components/device-provenance";
import { ActionButton, type RowContext, type RowParts } from "@/components/review/review-list";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Kbd } from "@/components/ui/kbd";
import { Skeleton } from "@/components/ui/skeleton";
import { getSourceLineage, getSourceSummary, type SourceRow } from "@/lib/api";
import { ago } from "@/lib/queue";
import type { MemoryNode, SourceLineage, SourceSummary } from "@/lib/types";

/** What a snapshot's kind is called, in the order the Kind filter lists them. */
export const KINDS: { value: string; label: string; one: string }[] = [
  { value: "conversation", label: "Conversations", one: "Conversation" },
  { value: "text", label: "Files", one: "File" },
  { value: "extracted", label: "Documents", one: "Document" },
  { value: "url", label: "Web pages", one: "Web page" },
  { value: "inline", label: "Pasted", one: "Pasted content" },
  { value: "", label: "No snapshot", one: "No snapshot" },
];
const KIND_ONE = new Map(KINDS.map((kind) => [kind.value, kind.one]));
export const kindOf = (row: SourceRow) => (KIND_ONE.has(row.kind ?? "") ? row.kind ?? "" : "");

/** A row's name: a conversation's question, else its key. */
export const rowName = (row: SourceRow) => row.title || row.source;

/** A key's folder and last segment: a file reads by its name first. */
export function splitKey(source: string) {
  const at = source.lastIndexOf("/", source.length - 2);
  return at < 0 ? { dir: "", base: source } : { dir: source.slice(0, at + 1), base: source.slice(at + 1) };
}

/** Why a row cannot move from here, or null. A project's own list knows the
 * project of a source without a snapshot; the list of every source does not. */
export function cannotMove(row: SourceRow, scope?: string) {
  if (row.linked) return `Linked from ${row.project || "no project"}: it moves with that project`;
  if (!scope && !row.project) return "Its project is not recorded with a snapshot; move it from Projects";
  return null;
}

export interface SourceRowOptions {
  /** The project the list is scoped to; rows then leave it out. */
  scope?: string;
  onRead: (row: SourceRow, chunk?: number) => void;
  onMove: (row: SourceRow) => void;
  onLink: (row: SourceRow) => void;
  onMigrate: (row: SourceRow) => void;
  onPurge: (row: SourceRow) => void;
  onDetails: (row: SourceRow) => void;
  onGraph: (row: SourceRow) => void;
  /** The simple dashboard (ADR 0182): Read and Move only, no chunks. */
  simple?: boolean;
}

/** One source as a two-line row: the question or the file's name, then its
 * folder or key, project, kind and age; its chunks beside the actions. */
export function sourceRow(row: SourceRow, ctx: RowContext, o: SourceRowOptions): RowParts {
  const kind = KIND_ONE.get(kindOf(row));
  const { dir, base } = splitKey(row.source);
  const name = rowName(row);
  const unmovable = cannotMove(row, o.scope);
  // A key that starts with its project already names it.
  const where = row.linked ? `Linked from ${row.project || "no project"}`
    : !o.scope && row.project && !row.source.startsWith(`${row.project}/`) ? row.project : "";
  return {
    body: (
      <>
        <p className="truncate text-sm font-medium" title={row.title ? `${row.title}\n${row.source}` : row.source}>
          {row.title ? row.title : <span className="font-mono text-[12.5px]">{base}</span>}
        </p>
        <p className="truncate text-xs text-muted-foreground">
          {row.title ? <span className="font-mono">{row.source}</span> : dir && <span className="font-mono">{dir}</span>}
          {(row.title || dir) && " · "}
          {where && <>{row.linked ? <span className="font-medium text-foreground">{where}</span> : where}{" · "}</>}
          {kind}
          {!!row.updated && <> · {ago(row.updated)}</>}
          {!o.simple && <span className="hidden @max-[620px]/page:inline"> · {row.chunks.toLocaleString()} {row.chunks === 1 ? "chunk" : "chunks"}</span>}
        </p>
      </>
    ),
    meta: o.simple ? null : <>{row.chunks.toLocaleString()} {row.chunks === 1 ? "chunk" : "chunks"}</>,
    actions: o.simple ? (
      <>
        <ActionButton icon={<BookOpenText />} label="Read" hint="Read it whole (O)" tabIndex={ctx.tabIndex} onClick={() => o.onRead(row)} />
        <ActionButton icon={<ArrowRightLeft />} label="Move" hint={unmovable || "Move to another project (M)"} tabIndex={ctx.tabIndex}
          disabled={!!unmovable} onClick={() => o.onMove(row)} />
      </>
    ) : (
      <>
        <ActionButton icon={<BookOpenText />} label="Read" hint="Read it whole (O)" tabIndex={ctx.tabIndex} onClick={() => o.onRead(row)} />
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" tabIndex={ctx.tabIndex} aria-label={`More for ${name}`} />}>
            <Ellipsis />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-60">
            <DropdownMenuGroup>
              <DropdownMenuItem onClick={() => o.onDetails(row)}>
                <ListCollapse /> {ctx.expanded ? "Hide details" : "Details and chunks"}<DropdownMenuShortcut>↵</DropdownMenuShortcut>
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => o.onGraph(row)}><Network /> Show in graph</DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuItem disabled={!!unmovable} onClick={() => o.onMove(row)}>
                <ArrowRightLeft /> Move to another project…<DropdownMenuShortcut>M</DropdownMenuShortcut>
              </DropdownMenuItem>
              <DropdownMenuItem disabled={!row.project && !o.scope} onClick={() => o.onLink(row)}><Link2 /> Link to another project…</DropdownMenuItem>
              <DropdownMenuItem onClick={() => o.onMigrate(row)}><Copy /> Copy or move to a workspace…</DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onClick={() => o.onPurge(row)}><Trash2 /> Purge from this workspace…</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </>
    ),
    details: ctx.expanded ? <SourceDetails row={row} project={o.scope ?? row.project ?? ""} tabIndex={ctx.tabIndex} onRead={o.onRead} simple={o.simple} /> : undefined,
  };
}

const CHUNKS_SHOWN = 8;

/** An open row: where the source was recorded, its tags and topics, and its
 * chunks a few at a time, each readable in place. Loaded when opened. */
function SourceDetails({ row, project, tabIndex, onRead, simple }: {
  row: SourceRow; project: string; tabIndex: number; onRead: (row: SourceRow, chunk?: number) => void; simple?: boolean;
}) {
  const [data, setData] = useState<{ summary: SourceSummary | null; lineage: SourceLineage | null; error?: string } | null>(null);
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([getSourceSummary(row.source, project), getSourceLineage(row.source)])
      .then(([summary, lineage]) => {
        if (!live) return;
        const found = summary && !("error" in (summary as object)) ? summary : null;
        setData({ summary: found, lineage: Array.isArray(lineage?.chunks) ? lineage : null });
      })
      .catch((e) => { if (live) setData({ summary: null, lineage: null, error: e instanceof Error ? e.message : String(e) }); });
    return () => { live = false; };
  }, [row.source, project]);

  if (!data) return (
    <div className="mt-1 space-y-2 rounded-lg border border-border bg-background p-3" aria-busy="true">
      <Skeleton className="h-3 w-2/3" /><Skeleton className="h-3 w-1/2" /><Skeleton className="h-16 w-full" />
    </div>
  );
  if (data.error) return <p role="alert" className="mt-1 text-xs text-destructive">Could not load the details: {data.error}</p>;

  const { summary, lineage } = data;
  const chunks: MemoryNode[] = lineage?.chunks ?? [];
  const pages = Math.max(1, Math.ceil(chunks.length / CHUNKS_SHOWN));
  const at = Math.min(page, pages - 1);
  const shown = chunks.slice(at * CHUNKS_SHOWN, (at + 1) * CHUNKS_SHOWN);
  const total = summary?.chunk_count ?? row.chunks;
  const tags = summary?.tags;
  return (
    <div className="mt-1 space-y-3 rounded-lg border border-border bg-background p-3 text-sm">
      <p className="font-mono text-xs break-all text-muted-foreground">{row.source}</p>
      {summary && (
        <>
          <DeviceProvenance origin={summary.recorded_device} paths={summary.path_context} />
          <div className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
            {summary.source_mtime > 0 && <span className="mr-1">Modified {new Date(summary.source_mtime * 1000).toLocaleString()}</span>}
            {!simple && [tags?.lang, tags?.layer, tags?.kind].filter(Boolean).map((t) => <Badge key={t} variant="outline">{t}</Badge>)}
            {!simple && tags?.domain?.map((d) => <Badge key={d} variant="secondary">{d}</Badge>)}
            {tags?.topics?.map((t) => simple ? <Badge key={t} variant="secondary">{t}</Badge> : (
              <Badge key={t} variant="secondary" render={<Link href={`/topics/?topic=${encodeURIComponent(t)}`} tabIndex={tabIndex} />}>{t}</Badge>
            ))}
          </div>
        </>
      )}
      {!simple && chunks.length > 0 && (
        <section aria-label="Chunks" className="space-y-1">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">Chunks</span>
            <span className="tabular-nums">
              {at * CHUNKS_SHOWN + 1}–{at * CHUNKS_SHOWN + shown.length} of {chunks.length.toLocaleString()}
              {total > chunks.length && ` (the first ${chunks.length.toLocaleString()} of ${total.toLocaleString()})`}
            </span>
            <span className="flex-1" />
            <Button variant="ghost" size="icon-xs" tabIndex={tabIndex} disabled={at <= 0} onClick={() => setPage(at - 1)} aria-label="Previous chunks"><ChevronLeft /></Button>
            <Button variant="ghost" size="icon-xs" tabIndex={tabIndex} disabled={at >= pages - 1} onClick={() => setPage(at + 1)} aria-label="Next chunks"><ChevronRight /></Button>
          </div>
          <ul className="divide-y divide-border rounded-md border border-border">
            {shown.map((chunk) => (
              <li key={chunk.id} className="px-2 py-1">
                <div className="flex items-center gap-2">
                  <button type="button" tabIndex={tabIndex} aria-expanded={open === chunk.id} onClick={() => setOpen(open === chunk.id ? null : chunk.id)}
                    className="flex min-w-0 flex-1 items-center gap-2 rounded text-left text-xs outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring">
                    <span className="w-9 shrink-0 font-mono text-muted-foreground">#{chunk.chunk_index}</span>
                    <span className="truncate">{chunk.label}</span>
                  </button>
                  <Button variant="ghost" size="xs" tabIndex={tabIndex} onClick={() => onRead(row, chunk.chunk_index)}>Read here</Button>
                </div>
                {open === chunk.id && (
                  <pre className="mt-1 max-h-48 overflow-auto rounded border border-border bg-card p-2 text-xs whitespace-pre-wrap">{chunk.content}</pre>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      <Button variant="outline" size="sm" tabIndex={tabIndex} onClick={() => onRead(row)}>
        <BookOpenText /> Read {kindOf(row) === "conversation" ? "conversation" : "source"} <Kbd>O</Kbd>
      </Button>
    </div>
  );
}
