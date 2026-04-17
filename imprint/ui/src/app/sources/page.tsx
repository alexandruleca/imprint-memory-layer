"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { listSources, getSourceSummary, getSourceLineage } from "@/lib/api";
import { ListSkeleton } from "@/components/loaders";
import { Skeleton } from "@/components/ui/skeleton";
import { MigrateDialog } from "@/components/migrate-dialog";
import type { SourceEntry, SourceSummary, SourceLineage, MemoryNode } from "@/lib/types";

export default function SourcesPage() {
  const [sources, setSources] = useState<SourceEntry[]>([]);
  const [filtered, setFiltered] = useState<SourceEntry[]>([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [summary, setSummary] = useState<SourceSummary | null>(null);
  const [lineage, setLineage] = useState<SourceLineage | null>(null);
  const [expandedChunk, setExpandedChunk] = useState<string | null>(null);
  const [migrateOpen, setMigrateOpen] = useState(false);

  useEffect(() => {
    listSources({ limit: 500 })
      .then((data) => {
        setSources(data.sources);
        setFiltered(data.sources);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!filter.trim()) {
      setFiltered(sources);
      return;
    }
    const q = filter.toLowerCase();
    setFiltered(sources.filter((s) => s.source.toLowerCase().includes(q)));
  }, [filter, sources]);

  async function selectSource(sourceKey: string) {
    setSelected(sourceKey);
    setExpandedChunk(null);
    const [sum, lin] = await Promise.all([
      getSourceSummary(sourceKey),
      getSourceLineage(sourceKey),
    ]);
    setSummary(sum);
    setLineage(lin);
  }

  if (loading)
    return (
      <div className="p-8 space-y-4">
        <Skeleton className="h-8 w-48" />
        <div className="grid grid-cols-3 gap-4" style={{ minHeight: 500 }}>
          <ListSkeleton count={16} />
          <div className="col-span-2">
            <Skeleton className="h-64 w-full rounded-xl" />
          </div>
        </div>
      </div>
    );

  return (
    <div className="p-8 space-y-4">
      <h2 className="text-2xl font-bold">
        Sources ({sources.length.toLocaleString()} files)
      </h2>

      <div className="grid grid-cols-3 gap-4" style={{ minHeight: 500 }}>
        {/* Left panel: source list */}
        <div className="col-span-1 space-y-2">
          <Input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter sources..."
            className="text-sm"
          />
          <ScrollArea className="h-[580px]">
            <div className="space-y-0.5 pr-4">
              {filtered.map((s) => (
                <div
                  key={s.source}
                  className={`p-2 rounded cursor-pointer text-sm flex justify-between items-center gap-2 ${
                    selected === s.source
                      ? "bg-primary/20 border border-primary"
                      : "hover:bg-muted"
                  }`}
                  onClick={() => selectSource(s.source)}
                >
                  <span className="font-mono text-xs truncate flex-1">
                    {s.source}
                  </span>
                  <Badge variant="secondary" className="shrink-0">
                    {s.chunks}
                  </Badge>
                </div>
              ))}
              {filtered.length === 0 && (
                <p className="text-sm text-muted-foreground py-4">
                  {sources.length === 0
                    ? "No indexed sources. Run 'imprint ingest' to index a project."
                    : "No sources match filter."}
                </p>
              )}
            </div>
          </ScrollArea>
        </div>

        {/* Right panel: source detail + chunks */}
        <div className="col-span-2">
          {summary ? (
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
                    <ScrollArea className="h-[380px]">
                      <div className="space-y-1.5 pr-4">
                        {lineage.chunks.map((chunk: MemoryNode) => (
                          <div
                            key={chunk.id}
                            className={`rounded border cursor-pointer transition-colors ${
                              expandedChunk === chunk.id
                                ? "border-primary bg-primary/5"
                                : "border-transparent hover:bg-muted"
                            }`}
                            onClick={() =>
                              setExpandedChunk(
                                expandedChunk === chunk.id ? null : chunk.id
                              )
                            }
                          >
                            <div className="flex items-center gap-2 p-2">
                              <span className="text-xs text-muted-foreground font-mono w-8 shrink-0">
                                #{chunk.chunk_index}
                              </span>
                              <span className="text-xs truncate flex-1">
                                {chunk.label}
                              </span>
                              <div className="flex gap-1 shrink-0">
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
                              </div>
                            </div>
                            {expandedChunk === chunk.id && (
                              <pre className="text-xs bg-muted mx-2 mb-2 p-3 rounded whitespace-pre-wrap max-h-80 overflow-auto">
                                {chunk.content}
                              </pre>
                            )}
                          </div>
                        ))}
                      </div>
                    </ScrollArea>
                  </CardContent>
                </Card>
              )}
            </div>
          ) : (
            <div className="flex items-center justify-center h-full text-muted-foreground">
              Select a source file to view details and chunks
            </div>
          )}
        </div>
      </div>

      {summary && (
        <MigrateDialog
          open={migrateOpen}
          preset={{ mode: "source", value: summary.source }}
          onClose={() => setMigrateOpen(false)}
          onDone={() => setMigrateOpen(false)}
        />
      )}
    </div>
  );
}
