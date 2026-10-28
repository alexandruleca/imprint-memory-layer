"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FolderOpen, Network } from "lucide-react";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { connectSSE, getOverview, getProjectDetail, listSources } from "@/lib/api";
import { ListSkeleton } from "@/components/loaders";
import { FacetBars, PageHeader, PaneCount, PaneFilter, PaneItem, SplitPage } from "@/components/split-page";
import { MigrateDialog } from "@/components/migrate-dialog";
import { ProjectMoveDialog } from "@/components/project-move-dialog";
import { ProjectSources } from "@/components/project-sources";
import { ProjectAssociations } from "@/components/project-associations";
import { SourceReader, type ReaderTarget } from "@/components/source-reader";
import type { OverviewData, Project, SourceEntry } from "@/lib/types";
import type { ProjectMove } from "@/lib/api";

type Facet = { name: string; count: number }[];

interface ProjectDetail {
  project: string;
  count: number;
  color: string;
  types: Facet;
  domains: Facet;
  langs: Facet;
  topics: Facet;
}

const DESCRIPTION = "Memories grouped by the project they belong to. Read a project's sources, or move them to another project.";

export default function ProjectsPage() {
  const [data, setData] = useState<OverviewData | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [sources, setSources] = useState<SourceEntry[] | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  // What the move dialog moves: some sources, or (null) the whole project.
  const [moving, setMoving] = useState<{ sources: string[] | null } | null>(null);
  const [migrateOpen, setMigrateOpen] = useState(false);
  const [associationsOpen, setAssociationsOpen] = useState(false);
  const [reading, setReading] = useState<ReaderTarget | null>(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");
  const router = useRouter();
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

  const header = <PageHeader title="Projects" count={data?.projects.length} description={DESCRIPTION} />;
  const errorAlert = error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>;

  if (!data) return (
    <SplitPage header={header} list={<ListSkeleton count={12} />}>
      {errorAlert}
      <Skeleton className="h-64 w-full rounded-xl" />
    </SplitPage>
  );

  const names = data.projects.map((p) => p.name);
  const q = filter.trim().toLowerCase();
  const shown = q ? data.projects.filter((p) => p.name.toLowerCase().includes(q)) : data.projects;

  return (
    <SplitPage
      header={header}
      filter={data.projects.length > 8 ? <PaneFilter label="Filter projects" value={filter} onChange={setFilter} /> : undefined}
      list={
        <nav aria-label="Projects" className="space-y-0.5">
          {shown.map((p: Project) => (
            <PaneItem key={p.id} selected={selected === p.name} onClick={() => select(p.name)}>
              <span className="size-2.5 shrink-0 rounded-full" style={{ backgroundColor: p.color }} aria-hidden />
              <span className="min-w-0 truncate">{p.name}</span>
              <PaneCount value={p.count} />
            </PaneItem>
          ))}
          {!shown.length && <p className="px-2.5 py-6 text-center text-sm text-muted-foreground">No project matches &ldquo;{filter}&rdquo;.</p>}
        </nav>
      }
    >
      {errorAlert}
      <div className="flex justify-end"><Button variant="outline" onClick={() => setAssociationsOpen(true)}>Review project associations</Button></div>
      {associationsOpen && <ProjectAssociations onClose={() => setAssociationsOpen(false)} onChanged={() => { refresh().catch((e) => setError(String(e))); }} />}
      {selected ? (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="flex min-w-0 items-center gap-2 text-lg">
                <span className="size-3 shrink-0 rounded-full" style={{ backgroundColor: data.projects.find((p) => p.name === selected)?.color }} aria-hidden />
                <span className="truncate">{selected}</span>
              </CardTitle>
              <CardDescription>
                {detail ? `${detail.count.toLocaleString()} memories` : "Loading…"}
                {sources && ` from ${sources.length.toLocaleString()} ${sources.length === 1 ? "source" : "sources"}`}
              </CardDescription>
              <CardAction className="flex flex-wrap justify-end gap-2">
                <Button size="sm" variant="outline" nativeButton={false} render={<Link href={`/graph/?scope=${encodeURIComponent(`project:${selected}`)}`} />}>
                  <Network aria-hidden /> Graph
                </Button>
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
                <div className="grid gap-x-8 gap-y-6 sm:grid-cols-2">
                  <FacetBars title="Topics" items={detail.topics} onSelect={(topic) => router.push(`/topics/?topic=${encodeURIComponent(topic)}`)} />
                  <FacetBars title="Kinds of memory" items={detail.types} />
                  <FacetBars title="Domains" items={detail.domains} />
                  <FacetBars title="Languages" items={detail.langs} />
                </div>
              ) : <Skeleton className="h-40 w-full" />}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Sources</CardTitle>
              <CardDescription>Conversations and files. Read one whole, or select some to move.</CardDescription>
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
              <ProjectSources sources={sources} selected={checked} onSelectedChange={setChecked}
                onRead={(s: SourceEntry) => setReading({ source: s.source, project: s.project ?? selected, title: s.title })} />
            </CardContent>
          </Card>
        </>
      ) : (
        <Empty className="min-h-64 border border-dashed">
          <EmptyHeader>
            <EmptyMedia variant="icon"><FolderOpen aria-hidden /></EmptyMedia>
            <EmptyTitle>Select a project</EmptyTitle>
            <EmptyDescription>See what it holds, read its sources, move some of them to another project, or merge the whole project.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}

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
      <SourceReader target={reading} onClose={() => setReading(null)} />
    </SplitPage>
  );
}
