"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronRight, FileCode } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { listSources, getSourceSummary, getSourceLineage } from "@/lib/api";
import { CardGridSkeleton, ListSkeleton } from "@/components/loaders";
import { Skeleton } from "@/components/ui/skeleton";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { MasterDetail } from "@/components/master-detail";
import { MigrateDialog } from "@/components/migrate-dialog";
import { ChunkDialog } from "@/components/chunk-dialog";
import { WorkspaceSources } from "@/components/workspace-sources";
import { DeviceProvenance } from "@/components/device-provenance";
import type { SourceEntry, SourceSummary, SourceLineage, MemoryNode } from "@/lib/types";

export default function SourcesPage() {
  const [sources, setSources] = useState<SourceEntry[]>([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [summary, setSummary] = useState<SourceSummary | null>(null);
  const [lineage, setLineage] = useState<SourceLineage | null>(null);
  const [expandedChunk, setExpandedChunk] = useState<string | null>(null);
  const [chunkDialog, setChunkDialog] = useState<MemoryNode | null>(null);
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

  const filtered = sources.filter((source) => source.source.toLowerCase().includes(filter.trim().toLowerCase()));

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

  if (loading)
    return (
      <div className="p-8 space-y-4">
        <Skeleton className="h-8 w-48" />
        <MasterDetail list={<ListSkeleton count={16} />}>
          <Skeleton className="h-64 w-full rounded-xl" />
        </MasterDetail>
      </div>
    );

  return (
    <div className="p-8 space-y-4">
      <h2 className="text-2xl font-bold">
        Sources ({sources.length.toLocaleString()} files)
      </h2>
      {error && <p role="alert" className="text-sm text-destructive">Sources could not load: {error}</p>}
      <WorkspaceSources selectedSource={selected} onPurged={async () => {
        requested.current = null; setOpening(false);
        setSelected(null); setSummary(null); setLineage(null); setChunkDialog(null);
        const data = await listSources({ limit: 500 });
        setSources(data.sources);
      }} />

      <MasterDetail
        filter={
          <Input
            aria-label="Filter sources"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter sources..."
            className="text-sm"
          />
        }
        list={
          <div className="space-y-0.5 pr-4">
            {filtered.map((s) => (
              <Button
                key={s.source}
                variant="ghost"
                aria-pressed={selected === s.source}
                className={`h-auto w-full justify-between gap-2 p-2 font-normal ${
                  selected === s.source ? "bg-primary/20 border border-primary hover:bg-primary/20" : ""
                }`}
                onClick={() => selectSource(s.source)}
              >
                <span className="font-mono text-xs truncate flex-1 text-left">
                  {s.source}
                </span>
                <Badge variant="secondary" className="shrink-0">
                  {s.chunks}
                </Badge>
              </Button>
            ))}
            {filtered.length === 0 && (
              <Empty className="py-8">
                <EmptyHeader>
                  <EmptyMedia variant="icon"><FileCode aria-hidden /></EmptyMedia>
                  <EmptyDescription>
                    {sources.length === 0
                      ? "No indexed sources. Run 'imprint ingest' to index a project."
                      : "No sources match filter."}
                  </EmptyDescription>
                </EmptyHeader>
              </Empty>
            )}
          </div>
        }
      >
        {opening ? (
          <div className="space-y-4" aria-busy="true">
            <Skeleton className="h-40 w-full rounded-xl" />
            <CardGridSkeleton count={4} />
          </div>
        ) : summary ? (
          <div className="space-y-4">
            {/* Summary card */}
            <Card>
              <CardHeader className="pb-3 flex flex-row items-center justify-between gap-2">
                <CardTitle className="text-sm font-mono truncate">
                  {summary.source}
                </CardTitle>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setMigrateOpen(true)}
                  className="shrink-0"
                >
                  Migrate…
                </Button>
              </CardHeader>
              <CardContent className="space-y-3">
                <DeviceProvenance origin={summary.recorded_device} paths={summary.path_context} />
                <div className="flex gap-4 text-sm">
                  <div>
                    <span className="text-muted-foreground">Project: </span>
                    <span className="font-medium">{summary.project || "—"}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Chunks: </span>
                    <span className="font-medium">{summary.chunk_count}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Modified: </span>
                    <span className="font-medium">
                      {summary.source_mtime
                        ? new Date(summary.source_mtime * 1000).toLocaleString()
                        : "—"}
                    </span>
                  </div>
                </div>

                <div className="flex gap-1 flex-wrap">
                  {summary.tags?.lang && (
                    <Badge variant="outline">{summary.tags.lang}</Badge>
                  )}
                  {summary.tags?.layer && (
                    <Badge variant="outline">{summary.tags.layer}</Badge>
                  )}
                  {summary.tags?.kind && (
                    <Badge variant="outline">{summary.tags.kind}</Badge>
                  )}
                  {summary.tags?.domain?.map((d: string) => (
                    <Badge key={d}>{d}</Badge>
                  ))}
                  {summary.tags?.topics?.map((t: string) => (
                    <Badge key={t} variant="secondary">
                      {t}
                    </Badge>
                  ))}
                </div>

                {summary.first_chunk_preview && (
                  <pre className="text-xs bg-muted p-3 rounded whitespace-pre-wrap max-h-24 overflow-auto">
                    {summary.first_chunk_preview}
                  </pre>
                )}
              </CardContent>
            </Card>

            {/* Chunks list */}
            {lineage && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">
                    Chunks ({lineage.total})
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-1.5">
                    {lineage.chunks.map((chunk: MemoryNode) => (
                      <Collapsible
                        key={chunk.id}
                        open={expandedChunk === chunk.id}
                        onOpenChange={(open) => setExpandedChunk(open ? chunk.id : null)}
                        className={`rounded border transition-colors ${
                          expandedChunk === chunk.id
                            ? "border-primary bg-primary/5"
                            : "border-transparent hover:bg-muted"
                        }`}
                      >
                        <CollapsibleTrigger className="group/chunk flex w-full items-center gap-2 rounded p-2 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
                          <ChevronRight className="size-3 shrink-0 text-muted-foreground transition-transform group-data-[panel-open]/chunk:rotate-90" aria-hidden />
                          <span className="text-xs text-muted-foreground font-mono w-8 shrink-0">
                            #{chunk.chunk_index}
                          </span>
                          <span className="text-xs truncate flex-1">
                            {chunk.label}
                          </span>
                          <span className="flex gap-1 shrink-0">
                            {chunk.tags?.topics
                              ?.slice(0, 2)
                              .map((t: string) => (
                                <Badge
                                  key={t}
                                  variant="secondary"
                                  className="text-[10px] px-1 py-0"
                                >
                                  {t}
                                </Badge>
                              ))}
                          </span>
                        </CollapsibleTrigger>
                        <CollapsibleContent className="mx-2 mb-2 space-y-1.5">
                          <pre className="text-xs bg-muted p-3 rounded whitespace-pre-wrap max-h-80 overflow-auto">
                            {chunk.content}
                          </pre>
                          <Button variant="secondary" size="xs" onClick={() => setChunkDialog(chunk)}>
                            View full
                          </Button>
                        </CollapsibleContent>
                      </Collapsible>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}
          </div>
        ) : (
          <Empty className="h-full">
            <EmptyHeader>
              <EmptyTitle>Select a source file to view details and chunks</EmptyTitle>
            </EmptyHeader>
          </Empty>
        )}
      </MasterDetail>

      {summary && (
        <MigrateDialog
          open={migrateOpen}
          preset={{ mode: "source", value: summary.source }}
          onClose={() => setMigrateOpen(false)}
          onDone={() => setMigrateOpen(false)}
        />
      )}
      <ChunkDialog chunk={chunkDialog} onClose={() => setChunkDialog(null)} />
    </div>
  );
}
