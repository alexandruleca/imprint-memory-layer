"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { connectSSE, getOverview, getProjectDetail, listSources } from "@/lib/api";
import { ListSkeleton } from "@/components/loaders";
import { MigrateDialog } from "@/components/migrate-dialog";
import { ProjectMoveDialog } from "@/components/project-move-dialog";
import { ProjectSources } from "@/components/project-sources";
import type { OverviewData, Project, SourceEntry } from "@/lib/types";
import type { ProjectMove } from "@/lib/api";

interface ProjectDetail {
  project: string;
  count: number;
  color: string;
  types: { name: string; count: number }[];
  domains: { name: string; count: number }[];
  langs: { name: string; count: number }[];
  sampleNodes: {
    id: string;
    label: string;
    type: string;
    source: string;
    tags: Record<string, unknown>;
    content: string;
  }[];
}

export default function ProjectsPage() {
  const [data, setData] = useState<OverviewData | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [sources, setSources] = useState<SourceEntry[] | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  // What the move dialog moves: some sources, or (null) the whole project.
  const [moving, setMoving] = useState<{ sources: string[] | null } | null>(null);
  const [migrateOpen, setMigrateOpen] = useState(false);
  const [error, setError] = useState("");
  const selectedRef = useRef<string | null>(null);
  const refreshing = useRef<Promise<void> | null>(null);
  const again = useRef(false);

  const loadProject = useCallback(async (name: string) => {
    const [d, s] = await Promise.all([
      getProjectDetail(name) as Promise<ProjectDetail>,
      listSources({ project: name, limit: 1000 }),
    ]);
    if (selectedRef.current !== name) return;
    const keys = s.sources.filter((entry) => entry.source);
    setDetail(d);
    setSources(keys);
    setChecked((prev) => new Set([...prev].filter((key) => keys.some((entry) => entry.source === key))));
  }, []);

  const select = useCallback((name: string | null) => {
    selectedRef.current = name;
    setSelected(name);
    setDetail(null);
    setSources(null);
    setChecked(new Set());
    if (name) loadProject(name).catch((e) => setError(String(e)));
  }, [loadProject]);

  // One refresh at a time; changes during one trigger a single follow-up.
  const refresh = useCallback(async () => {
    if (refreshing.current) { again.current = true; return refreshing.current; }
    const run = (async () => {
      do {
        again.current = false;
        const overview = await getOverview();
        setData(overview);
        setError("");
        const name = selectedRef.current;
        if (name && !overview.projects.some((p) => p.name === name)) select(null);
        else if (name) await loadProject(name);
      } while (again.current);
    })();
    refreshing.current = run;
    try { await run; } finally { refreshing.current = null; }
  }, [loadProject, select]);

  // The API reports data changes (ingest jobs, agents, sync); reads are fresh.
  useEffect(() => {
    refresh().catch((e) => setError(String(e)));
    const events = connectSSE(() => { refresh().catch(() => {}); });
    return () => events.close();
  }, [refresh]);

  function moved(result: ProjectMove) {
    setMoving(null);
    if (result.whole) select(result.target);
    else setChecked(new Set());
    refresh().catch((e) => setError(String(e)));
  }

  if (!data) return (
    <div className="p-8 space-y-4">
      <Skeleton className="h-8 w-48" />
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <div className="grid grid-cols-3 gap-4" style={{ minHeight: 500 }}>
        <ListSkeleton count={12} />
        <div className="col-span-2 flex items-center justify-center text-muted-foreground">
          <Skeleton className="h-64 w-full rounded-xl" />
        </div>
      </div>
    </div>
  );

  const names = data.projects.map((p) => p.name);

  return (
    <div className="p-8 space-y-4">
      <h2 className="text-2xl font-bold">Projects ({data.projects.length})</h2>
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}

      <div className="grid grid-cols-3 gap-4" style={{ minHeight: 500 }}>
        <ScrollArea className="h-[600px] col-span-1">
          <nav aria-label="Projects" className="space-y-1 pr-4">
            {data.projects.map((p: Project) => (
              <Button
                key={p.id}
                variant="ghost"
                aria-current={selected === p.name ? "true" : undefined}
                className={`h-auto w-full justify-between p-2 text-sm ${
                  selected === p.name ? "bg-primary/20 border border-primary" : ""
                }`}
                onClick={() => select(p.name)}
              >
                <span className="flex min-w-0 items-center gap-2">
                  <span className="w-3 h-3 shrink-0 rounded-full" style={{ backgroundColor: p.color }} />
                  <span className="truncate font-medium">{p.name}</span>
                </span>
                <Badge variant="secondary">{p.count.toLocaleString()}</Badge>
              </Button>
            ))}
          </nav>
        </ScrollArea>

        <div className="col-span-2 min-w-0">
          {selected ? (
            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>
                    {selected}
                    {detail && <span className="font-normal text-muted-foreground"> ({detail.count.toLocaleString()})</span>}
                  </CardTitle>
                  <CardAction className="flex gap-2">
                    <Button size="sm" variant="outline" onClick={() => setMoving({ sources: null })}>
                      Move or merge…
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => setMigrateOpen(true)}>
                      Migrate…
                    </Button>
                  </CardAction>
                </CardHeader>
                <CardContent>
                  {detail ? (
                    <div className="grid grid-cols-3 gap-4">
                      <div>
                        <p className="text-xs text-muted-foreground mb-1">Types</p>
                        <div className="flex gap-1 flex-wrap">
                          {detail.types.map((t) => (
                            <Badge key={t.name} variant="outline">{t.name} ({t.count})</Badge>
                          ))}
                        </div>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground mb-1">Domains</p>
                        <div className="flex gap-1 flex-wrap">
                          {detail.domains.map((d) => (
                            <Badge key={d.name}>{d.name} ({d.count})</Badge>
                          ))}
                        </div>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground mb-1">Languages</p>
                        <div className="flex gap-1 flex-wrap">
                          {detail.langs.map((l) => (
                            <Badge key={l.name} variant="secondary">{l.name} ({l.count})</Badge>
                          ))}
                        </div>
                      </div>
                    </div>
                  ) : <Skeleton className="h-16 w-full" />}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Sources{sources ? ` (${sources.length.toLocaleString()})` : ""}</CardTitle>
                  <CardAction className="flex items-center gap-2">
                    {checked.size > 0 && (
                      <span className="text-xs text-muted-foreground">{checked.size} selected</span>
                    )}
                    <Button size="sm" disabled={checked.size === 0} onClick={() => setMoving({ sources: [...checked] })}>
                      Move selected…
                    </Button>
                  </CardAction>
                </CardHeader>
                <CardContent>
                  <ScrollArea className="max-h-[360px]">
                    <ProjectSources sources={sources} selected={checked} onSelectedChange={setChecked} />
                  </ScrollArea>
                </CardContent>
              </Card>

              {detail && detail.sampleNodes.length > 0 && (
                <Card>
                  <CardHeader><CardTitle>Sample memories</CardTitle></CardHeader>
                  <CardContent>
                    <ScrollArea className="h-[300px]">
                      <div className="space-y-2 pr-4">
                        {detail.sampleNodes.map((node) => (
                          <div key={node.id} className="rounded-lg border p-3">
                            <p className="text-sm font-medium truncate">{node.label}</p>
                            <div className="flex gap-1 mt-1 flex-wrap">
                              <Badge variant="secondary" className="text-xs">{node.type}</Badge>
                              <Badge variant="outline" className="text-xs">{node.source}</Badge>
                            </div>
                            <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{node.content.slice(0, 200)}</p>
                          </div>
                        ))}
                      </div>
                    </ScrollArea>
                  </CardContent>
                </Card>
              )}
            </div>
          ) : (
            <Empty className="h-full border">
              <EmptyHeader>
                <EmptyTitle>Select a project</EmptyTitle>
                <EmptyDescription>See its sources, move some of them to another project, or merge the whole project.</EmptyDescription>
              </EmptyHeader>
            </Empty>
          )}
        </div>
      </div>

      {selected && (
        <ProjectMoveDialog
          open={moving !== null}
          project={selected}
          sources={moving?.sources ?? null}
          projects={names}
          onClose={() => setMoving(null)}
          onMoved={moved}
        />
      )}

      {selected && (
        <MigrateDialog
          open={migrateOpen}
          preset={{ mode: "project", value: selected }}
          onClose={() => setMigrateOpen(false)}
          onDone={() => setMigrateOpen(false)}
        />
      )}
    </div>
  );
}
