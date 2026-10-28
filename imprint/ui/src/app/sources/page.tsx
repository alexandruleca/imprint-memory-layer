"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { BookOpenText, ChevronRight, FileCode, FileText, MessagesSquare } from "lucide-react";
import { toast } from "sonner";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { listSources, getSourceSummary, getSourceLineage } from "@/lib/api";
import { ListSkeleton } from "@/components/loaders";
import { Skeleton } from "@/components/ui/skeleton";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { PageHeader, PaneCount, PaneFilter, PaneItem, SplitPage } from "@/components/split-page";
import { MigrateDialog } from "@/components/migrate-dialog";
import { ChunkDialog } from "@/components/chunk-dialog";
import { SourceReader, type ReaderTarget } from "@/components/source-reader";
import { WorkspaceSources } from "@/components/workspace-sources";
import { DeviceProvenance } from "@/components/device-provenance";
import type { SourceEntry, SourceSummary, SourceLineage, MemoryNode } from "@/lib/types";

const DESCRIPTION = "Every conversation and file indexed in this workspace. Read one whole, inspect its chunks, or clean up what was indexed.";

export default function SourcesPage() {
  const [sources, setSources] = useState<SourceEntry[]>([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [summary, setSummary] = useState<SourceSummary | null>(null);
  const [lineage, setLineage] = useState<SourceLineage | null>(null);
  const [expandedChunk, setExpandedChunk] = useState<string | null>(null);
  const [chunkDialog, setChunkDialog] = useState<MemoryNode | null>(null);
  const [reading, setReading] = useState<ReaderTarget | null>(null);
  const [migrateOpen, setMigrateOpen] = useState(false);
  const [error, setError] = useState("");
  const [opening, setOpening] = useState(false);
  // The source whose detail is wanted now; a slower earlier answer is dropped.
  const requested = useRef<string | null>(null);

  useEffect(() => {
    listSources({ limit: 500 })
      .then((data) => {
        setSources(data.sources);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  const q = filter.trim().toLowerCase();
  const filtered = q ? sources.filter((s) => `${s.title ?? ""} ${s.source}`.toLowerCase().includes(q)) : sources;
  const titled = sources.find((s) => s.source === selected)?.title;

  async function selectSource(sourceKey: string) {
    requested.current = sourceKey;
    setSelected(sourceKey);
    setExpandedChunk(null);
    setOpening(true);
    try {
      const [sum, lin] = await Promise.all([
        getSourceSummary(sourceKey),
        getSourceLineage(sourceKey),
      ]);
      if (requested.current !== sourceKey) return;
      setSummary(sum);
      setLineage(lin);
    } catch (e) {
      if (requested.current !== sourceKey) return;
      setSelected(null);
      setSummary(null);
      setLineage(null);
      toast.error(`Could not load ${sourceKey}`, { description: e instanceof Error ? e.message : String(e) });
    } finally {
      if (requested.current === sourceKey) setOpening(false);
    }
  }

  const header = <PageHeader title="Sources" count={loading ? undefined : sources.length} description={DESCRIPTION} />;

  if (loading) return (
    <SplitPage header={header} list={<ListSkeleton count={16} />} listWidth="22rem">
      <Skeleton className="h-64 w-full rounded-xl" />
    </SplitPage>
  );

  const conversation = summary?.source_type === "conversation";
  const read = (chunk?: number) => summary && setReading({ source: summary.source, project: summary.project, chunk, title: titled });

  return (
    <SplitPage
      header={header}
      listWidth="22rem"
      filter={<PaneFilter label="Filter sources" value={filter} onChange={setFilter} />}
      list={
        <nav aria-label="Sources" className="space-y-0.5">
          {filtered.map((s) => (
            <PaneItem key={s.source} selected={selected === s.source} onClick={() => selectSource(s.source)} title={s.source}>
              {s.title ? (
                <span className="min-w-0">
                  <span className="block truncate">{s.title}</span>
                  <span className="block truncate font-mono text-[11px] font-normal text-muted-foreground">{s.source}</span>
                </span>
              ) : <span className="min-w-0 truncate font-mono text-xs">{s.source}</span>}
              <PaneCount value={s.chunks} />
            </PaneItem>
          ))}
          {filtered.length === 0 && (
            <Empty className="py-8">
              <EmptyHeader>
                <EmptyMedia variant="icon"><FileCode aria-hidden /></EmptyMedia>
                <EmptyDescription>
                  {sources.length === 0
                    ? <>Nothing is indexed yet. Ingest a project or learn conversations from <Link href="/commands/">Commands</Link>.</>
                    : "No source matches the filter."}
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          )}
        </nav>
      }
    >
      {error && <p role="alert" className="text-sm text-destructive">Sources could not load: {error}</p>}
      <WorkspaceSources selectedSource={selected} onPurged={async () => {
        requested.current = null; setOpening(false);
        setSelected(null); setSummary(null); setLineage(null); setChunkDialog(null);
        const data = await listSources({ limit: 500 });
        setSources(data.sources);
      }} />

      {opening ? (
        <div className="space-y-4" aria-busy="true">
          <Skeleton className="h-40 w-full rounded-xl" />
          <Skeleton className="h-64 w-full rounded-xl" />
        </div>
      ) : summary ? (
        <>
          <Card>
            <CardHeader>
              <div className="flex min-w-0 items-start gap-3">
                <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
                  {conversation ? <MessagesSquare className="size-4" aria-hidden /> : <FileText className="size-4" aria-hidden />}
                </span>
                <div className="min-w-0 space-y-0.5">
                  <CardTitle className={titled ? "line-clamp-2 leading-snug" : "font-mono text-sm break-all"}>{titled || summary.source}</CardTitle>
                  <CardDescription className="flex flex-wrap gap-x-1.5 text-xs">
                    {titled && <span className="font-mono">{summary.source}</span>}
                    <span>{titled && "· "}{summary.project || "No project"}</span>
                    <span>· {summary.chunk_count.toLocaleString()} chunks</span>
                    {summary.source_mtime > 0 && <span>· modified {new Date(summary.source_mtime * 1000).toLocaleString()}</span>}
                  </CardDescription>
                </div>
              </div>
              <CardAction className="flex flex-wrap justify-end gap-2">
                <Button size="sm" onClick={() => read()}>
                  <BookOpenText aria-hidden /> Read {conversation ? "conversation" : "source"}
                </Button>
                <Button size="sm" variant="outline" onClick={() => setMigrateOpen(true)}>Migrate…</Button>
              </CardAction>
            </CardHeader>
            <CardContent className="space-y-3">
              <DeviceProvenance origin={summary.recorded_device} paths={summary.path_context} />
              <div className="flex flex-wrap gap-1">
                {[summary.tags?.lang, summary.tags?.layer, summary.tags?.kind].filter(Boolean).map((t) => (
                  <Badge key={t} variant="outline">{t}</Badge>
                ))}
                {summary.tags?.domain?.map((d: string) => <Badge key={d} variant="secondary">{d}</Badge>)}
                {summary.tags?.topics?.map((t: string) => (
                  <Badge key={t} variant="secondary" render={<Link href={`/topics/?topic=${encodeURIComponent(t)}`} />}>{t}</Badge>
                ))}
              </div>
            </CardContent>
          </Card>

          {lineage && (
            <Card>
              <CardHeader>
                <CardTitle>Chunks</CardTitle>
                <CardDescription>{lineage.total.toLocaleString()} pieces this source was split into for search.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-1">
                {lineage.chunks.map((chunk: MemoryNode) => (
                  <Collapsible
                    key={chunk.id}
                    open={expandedChunk === chunk.id}
                    onOpenChange={(open) => setExpandedChunk(open ? chunk.id : null)}
                    className="rounded-lg transition-colors data-[open]:bg-muted/50"
                  >
                    <CollapsibleTrigger className="group/chunk flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50">
                      <ChevronRight className="size-3 shrink-0 text-muted-foreground transition-transform group-data-[panel-open]/chunk:rotate-90" aria-hidden />
                      <span className="w-8 shrink-0 font-mono text-xs text-muted-foreground">#{chunk.chunk_index}</span>
                      <span className="flex-1 truncate text-xs">{chunk.label}</span>
                      <span className="flex shrink-0 gap-1">
                        {chunk.tags?.topics?.slice(0, 2).map((t: string) => (
                          <Badge key={t} variant="secondary" className="px-1 py-0 text-[10px]">{t}</Badge>
                        ))}
                      </span>
                    </CollapsibleTrigger>
                    <CollapsibleContent className="space-y-2 px-2 pb-2">
                      <pre className="max-h-80 overflow-auto rounded-lg border border-border bg-background p-3 text-xs whitespace-pre-wrap">
                        {chunk.content}
                      </pre>
                      <div className="flex gap-2">
                        <Button variant="secondary" size="xs" onClick={() => read(chunk.chunk_index)}>Read in context</Button>
                        <Button variant="ghost" size="xs" onClick={() => setChunkDialog(chunk)}>View chunk</Button>
                      </div>
                    </CollapsibleContent>
                  </Collapsible>
                ))}
              </CardContent>
            </Card>
          )}
        </>
      ) : (
        <Empty className="min-h-64 border border-dashed">
          <EmptyHeader>
            <EmptyMedia variant="icon"><FileText aria-hidden /></EmptyMedia>
            <EmptyTitle>Select a source</EmptyTitle>
            <EmptyDescription>Read it whole, see where it came from and the chunks it was split into.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}

      {summary && (
        <MigrateDialog
          open={migrateOpen}
          preset={{ mode: "source", value: summary.source }}
          onClose={() => setMigrateOpen(false)}
          onDone={() => setMigrateOpen(false)}
        />
      )}
      <ChunkDialog chunk={chunkDialog} onClose={() => setChunkDialog(null)} />
      <SourceReader target={reading} onClose={() => setReading(null)} />
    </SplitPage>
  );
}
