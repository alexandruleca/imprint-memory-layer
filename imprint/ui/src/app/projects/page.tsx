"use client";

import { useUiMode } from "@/lib/ui-mode";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRightLeft, Copy, Link2, Network, RefreshCw } from "lucide-react";
import { ListSkeleton } from "@/components/loaders";
import { MigrateDialog, type MigratePreset } from "@/components/migrate-dialog";
import { ProjectAssociations } from "@/components/project-associations";
import { ProjectMoveDialog } from "@/components/project-move-dialog";
import { ProjectOverview, type ProjectDetail } from "@/components/projects/project-overview";
import { GroupRail, GroupSelect, type RailGroup } from "@/components/review/group-rail";
import { PAGE_SIZES } from "@/components/review/pager";
import { CountTab, ReviewHeader } from "@/components/review/review-header";
import { ActionButton, Announcer, RESET, ReviewEmpty, reviewTab, useAnnounce, type ReviewTabState } from "@/components/review/review-list";
import { ShortcutsButton, ShortcutsDialog, type Shortcut } from "@/components/review/shortcuts-dialog";
import { SourceReader, type ReaderTarget } from "@/components/source-reader";
import { PurgeDialog, type PurgeTarget } from "@/components/sources/purge-dialog";
import { SOURCE_TAB, SourceList } from "@/components/sources/source-list";
import { StaleNote } from "@/components/sources/stale-note";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  connectSSE, getOrganize, getProjectDetail, getProjects, listAllSources, type ProjectMove, type SourceRow,
} from "@/lib/api";
import { useCached } from "@/lib/use-cached";
import { useHashTab } from "@/lib/use-hash-tab";
import { useReviewShortcuts } from "@/lib/use-review-shortcuts";
import { useStoredNumber } from "@/lib/use-stored";

const TABS = ["overview", "sources"] as const;
type Tab = (typeof TABS)[number];
const LAST = "imprint.projects.last";
/** A data change this soon after a load is the load's own, or the action's that caused it. */
const SETTLE_MS = 4000;

const INFO = (
  <div className="space-y-2">
    <p className="font-medium">What Projects shows</p>
    <p>Memories grouped by the project they belong to. Open one to see what it holds and work through its sources: read them, move some to another project, or move or merge the whole project.</p>
    <p><span className="font-medium">Project associations</span> link a source to more projects than the one it belongs to.</p>
    <p className="text-muted-foreground">Sources lists every project&apos;s sources together.</p>
  </div>
);

const SIMPLE_INFO = (
  <div className="space-y-2">
    <p className="font-medium">What the Library shows</p>
    <p>Everything Imprint remembers, by project. Open a project to see what it is about and read the documents and conversations in it.</p>
    <p>If something is in the wrong project, move it. Imprint keeps it there from then on.</p>
  </div>
);

const SHORTCUTS: Shortcut[] = [
  [["J", "K"], "Next / previous project"], [["1", "2"], "Overview / Sources"],
  [["j", "↓"], "Next source"], [["k", "↑"], "Previous source"], [["x"], "Select the source"],
  [["Enter"], "Show or hide details and chunks"], [["o"], "Read the source"], [["m"], "Move to another project"],
  [["[", "]"], "Previous / next page"], [["/"], "Search"], [["Esc"], "Clear the selection, close details"], [["?"], "This list"],
];

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

function remembered() {
  try { return window.localStorage.getItem(LAST); } catch { return null; }
}
function remember(name: string) {
  try { window.localStorage.setItem(LAST, name); } catch {}
}
/** The project the address names, else the last one opened in this browser. */
function wanted() {
  return new URLSearchParams(window.location.search).get("project") ?? remembered();
}

export default function ProjectsPage() {
  return <Announcer><Projects /></Announcer>;
}

function Projects() {
  const root = useRef<HTMLDivElement>(null);
  const router = useRouter();
  // The simple dashboard calls this page Library and keeps reading and moving (ADR 0182).
  const { simple } = useUiMode();
  const announce = useAnnounce();
  // The project list shows the last answer at once and follows every data change (ADR 0152).
  const projects = useCached("projects", getProjects, { revalidateOnDataChange: true });
  const data = projects.data ?? null;
  const error = projects.error && !data ? message(projects.error) : "";
  const [chosen, setChosen] = useState<string | null>(() => (typeof window === "undefined" ? null : wanted()));
  /** When the API last reported a data change; the open sources are stale when they are older. */
  const [changedAt, setChangedAt] = useState(0);
  const [list, setList] = useState<ReviewTabState>(() => reviewTab(SOURCE_TAB));
  const [moving, setMoving] = useState<{ project: string; sources: string[] | null } | null>(null);
  const [migrating, setMigrating] = useState<MigratePreset | null>(null);
  const [associations, setAssociations] = useState<{ initial?: { source: string; project: string } } | null>(null);
  const [purging, setPurging] = useState<PurgeTarget | null>(null);
  const [reading, setReading] = useState<ReaderTarget | null>(null);
  const [help, setHelp] = useState(false);
  const [linkedTab, openTab] = useHashTab(TABS);
  const [size, setSize] = useStoredNumber("imprint.review.page-size", 20, PAGE_SIZES);
  const tab: Tab = linkedTab ?? "overview";

  const names = useMemo(() => data?.projects.map((p) => p.name) ?? [], [data]);
  // A project that went away (merged, moved, purged) gives way to the largest.
  const selected = chosen && names.includes(chosen) ? chosen : names[0] ?? null;

  // The open project's facets follow every data change; its sources wait for
  // Refresh so rows do not move under the cursor (a note offers it).
  const detail = useCached(selected ? `project:${selected}` : null,
    () => getProjectDetail(selected ?? "") as Promise<ProjectDetail>, { revalidateOnDataChange: true });
  const sources = useCached(selected ? `project-sources:${selected}` : null,
    () => listAllSources(selected ?? "").then((result) => result.sources.filter((row) => row.source)),
    { revalidateOnFocus: false, revalidateOnJobs: false });
  const { refresh: refreshProjects } = projects;
  const { refresh: refreshDetail } = detail;
  const { refresh: refreshSources } = sources;
  const refresh = useCallback(() => Promise.all([refreshProjects(), refreshDetail()]).then(() => undefined),
    [refreshProjects, refreshDetail]);
  const reloadSources = useCallback(() => { void refreshSources(); void refreshDetail(); }, [refreshSources, refreshDetail]);

  useEffect(() => {
    const events = connectSSE(() => setChangedAt(Date.now()));
    return () => events.close();
  }, []);

  // Organize's profiles for the Overview.
  const profileList = useCached(tab === "overview" ? "organize-profiles" : null, () => getOrganize().then((review) =>
    (Array.isArray(review?.profiles) ? review.profiles : []).map((p) => ({ project: p.project, text: p.text, updated: p.updated }))));
  const profiles = useMemo(() => profileList.data || profileList.error
    ? new Map((profileList.data ?? []).map((p) => [p.project, { text: p.text, updated: p.updated }]))
    : null, [profileList.data, profileList.error]);

  const select = useCallback((name: string) => {
    setChosen(name);
    setList((prev) => reviewTab({ sort: prev.sort }));
    remember(name);
    window.history.replaceState(null, "", `?project=${encodeURIComponent(name)}${window.location.hash}`);
  }, []);
  const step = (delta: number) => {
    if (!names.length || !selected) return;
    const next = names[(names.indexOf(selected) + delta + names.length) % names.length];
    select(next);
    announce(`${next}: ${data?.projects.find((p) => p.name === next)?.count.toLocaleString()} memories`);
  };

  function moved(result: ProjectMove) {
    setMoving(null);
    if (result.whole) select(result.target);
    else {
      setList((prev) => ({ ...prev, selected: new Set(), anchor: null, expanded: null }));
      reloadSources();
    }
    void refresh();
  }

  useReviewShortcuts(root, {
    J: () => step(1), K: () => step(-1), "1": () => openTab("overview"), "2": () => openTab("sources"), "?": () => setHelp(true),
  });

  const project = data?.projects.find((p) => p.name === selected);
  const sourcesName = simple ? "Documents and conversations" : "Sources";
  const shownDetail = detail.data ?? null;
  const rows = sources.data ? { rows: sources.data, error: undefined }
    : sources.error ? { rows: [] as SourceRow[], error: message(sources.error) } : null;
  const stale = !!sources.updatedAt && changedAt - sources.updatedAt > SETTLE_MS;
  const groups: RailGroup[] = (data?.projects ?? []).map((p) => ({ name: p.name, count: p.count, color: p.color }));

  const read = (row: SourceRow, chunk?: number) =>
    setReading({ source: row.source, project: row.project ?? selected, chunk, title: row.title || row.source });
  const actions = {
    simple,
    onRead: read,
    onMove: (row: SourceRow) => { if (selected) setMoving({ project: selected, sources: [row.source] }); },
    onLink: (row: SourceRow) => setAssociations({ initial: { source: row.source, project: row.project ?? selected ?? "" } }),
    onMigrate: (row: SourceRow) => setMigrating({ mode: "source", value: row.source }),
    onPurge: (row: SourceRow) => setPurging({ target: row.source, kind: "source", recursive: false }),
    onGraph: (row: SourceRow) => router.push(`/graph/?scope=${encodeURIComponent(`source:${row.source}`)}`),
  };

  const header = (
    <ReviewHeader title={simple ? "Library" : "Projects"} count={data?.projects.length} info={simple ? SIMPLE_INFO : INFO}
      infoLabel={simple ? "About the Library" : "About Projects"}
      updating={projects.refreshing || detail.refreshing || sources.refreshing}
      actions={simple ? undefined : <Button variant="outline" onClick={() => setAssociations({})}><Link2 /> Project associations…</Button>} />
  );

  const errorAlert = error && (
    <div className="px-4 pb-3 sm:px-8">
      <Alert variant="destructive">
        <AlertTitle>Projects could not load: {error}</AlertTitle>
        <AlertDescription>
          <Button variant="outline" size="sm" className="mt-1" onClick={() => { void refresh(); }}><RefreshCw /> Try again</Button>
        </AlertDescription>
      </Alert>
    </div>
  );

  let body: React.ReactNode;
  if (!data) {
    body = !error && (
      <div className="grid gap-4 px-4 sm:px-8 @min-[760px]/page:grid-cols-[15rem_minmax(0,1fr)]" aria-busy="true">
        <div className="hidden @min-[760px]/page:block"><ListSkeleton count={12} /></div>
        <div className="space-y-3">
          <Skeleton className="h-8 w-64" />
          <p className="flex gap-6 border-b border-border pb-2 text-sm text-muted-foreground"><span>Overview</span><span>Sources</span></p>
          <Skeleton className="h-48 w-full rounded-xl" />
        </div>
      </div>
    );
  } else if (!selected) {
    body = (
      <div className="px-4 sm:px-8">
        <ReviewEmpty title="No projects yet."
          description={simple ? "Projects appear as you add things: add a folder, a document or your AI conversations on Home."
            : "Projects appear as memories are saved: ingest a folder or learn your conversations from Commands."} />
      </div>
    );
  } else {
    body = (
      <div className="grid min-h-0 flex-1 gap-4 px-4 pb-4 sm:px-8 lg:grid-rows-[minmax(0,1fr)] @min-[760px]/page:grid-cols-[15rem_minmax(0,1fr)]">
        <GroupRail label="Projects" groups={groups} total={data.total} value={selected} onChange={select} allLabel={null} tour="library-projects" />
        <section aria-label={selected} data-tour="library-project" className="flex min-h-0 min-w-0 flex-col gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
            <GroupSelect groups={groups} total={data.total} value={selected} onChange={(next) => next && select(next)} allLabel={null} />
            <h2 className="hidden min-w-0 items-center gap-2 text-lg font-semibold @min-[760px]/page:flex">
              <span aria-hidden className="size-3 shrink-0 rounded-full" style={{ backgroundColor: project?.color }} />
              <span className="truncate">{selected}</span>
            </h2>
            <span className="text-sm text-muted-foreground tabular-nums @max-[620px]/page:hidden">
              {simple
                ? rows && !rows.error && `${rows.rows.length.toLocaleString()} ${rows.rows.length === 1 ? "document or conversation" : "documents and conversations"}`
                : <>{(shownDetail?.count ?? project?.count ?? 0).toLocaleString()} memories
                  {rows && !rows.error && ` · ${rows.rows.length.toLocaleString()} ${rows.rows.length === 1 ? "source" : "sources"}`}</>}
            </span>
            <span className="flex-1" />
            <div className="flex items-center gap-1">
              {!simple && <ActionButton icon={<Network />} label="Graph" hint="Show the project in the graph"
                onClick={() => router.push(`/graph/?scope=${encodeURIComponent(`project:${selected}`)}`)} />}
              <ActionButton icon={<ArrowRightLeft />} label="Move or merge…" hint="Move or merge the whole project into another one"
                onClick={() => setMoving({ project: selected, sources: null })} />
              {!simple && <ActionButton icon={<Copy />} label="Copy to workspace…" hint="Copy or move the project to another workspace"
                onClick={() => setMigrating({ mode: "project", value: selected })} />}
            </div>
          </div>
          <Tabs value={tab} onValueChange={(value) => openTab(value as Tab)} className="flex min-h-0 flex-1 flex-col gap-0">
            <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-border bg-background lg:static">
              <TabsList variant="line" data-tour="library-tabs" className="h-10 min-w-0 justify-start overflow-x-auto">
                <TabsTrigger value="overview" className="flex-none px-2.5">Overview</TabsTrigger>
                {rows && !rows.error ? <CountTab value="sources" name={sourcesName} short={simple ? "Contents" : undefined} count={rows.rows.length} />
                  : <TabsTrigger value="sources" className="flex-none px-2.5">{sourcesName}</TabsTrigger>}
              </TabsList>
              <span className="flex-1" />
              {!simple && <span className="@max-[520px]/page:hidden"><ShortcutsButton onClick={() => setHelp(true)} /></span>}
            </div>
            <TabsContent value="overview" className="min-h-0 flex-1 pt-3 lg:overflow-y-auto">
              <ProjectOverview simple={simple} detail={shownDetail} sources={rows ? rows.rows : null} profile={profiles?.get(selected)}
                onTopic={(topic) => router.push(`/topics/?topic=${encodeURIComponent(topic)}`)}
                onSources={(kind) => { setList((prev) => ({ ...prev, ...RESET, filter: kind })); openTab("sources"); }} />
            </TabsContent>
            <TabsContent value="sources" className="flex min-h-0 flex-1 flex-col gap-2 pt-3">
              {stale && rows && <StaleNote busy={sources.refreshing} onRefresh={reloadSources} what="This project" />}
              {!rows ? <div className="space-y-2" aria-busy="true"><Skeleton className="h-10 w-full rounded-xl" /><ListSkeleton count={8} /></div>
                : rows.error ? (
                  <Alert variant="destructive">
                    <AlertTitle>Its sources could not load: {rows.error}</AlertTitle>
                    <AlertDescription>
                      <Button variant="outline" size="sm" className="mt-1" onClick={reloadSources}><RefreshCw /> Try again</Button>
                    </AlertDescription>
                  </Alert>
                ) : (
                  <SourceList sources={rows.rows} scope={selected} state={list} onState={(patch) => setList((prev) => ({ ...prev, ...patch }))}
                    size={size} onSize={(next) => { setSize(next); setList((prev) => ({ ...prev, page: 0, reveal: false })); }}
                    root={root} label={`Sources of ${selected}`} actions={actions}
                    onMove={(moveRows) => setMoving({ project: selected, sources: moveRows.map((row) => row.source) })}
                    onMigrate={simple ? undefined : (values) => setMigrating({ mode: "source", values })}
                    empty={<ReviewEmpty title={simple ? "Nothing to read here yet." : "No sources."}
                      description={simple ? "This project holds only short notes your AI tools saved." : "This project holds only memories saved without one."} />} />
                )}
            </TabsContent>
          </Tabs>
        </section>
      </div>
    );
  }

  return (
    <>
      <div ref={root} className="@container/page flex min-h-full flex-col lg:h-full">
        {header}
        {errorAlert}
        {body}
      </div>
      <ProjectMoveDialog open={moving !== null} project={moving?.project ?? ""} sources={moving?.sources ?? null}
        projects={names} onClose={() => setMoving(null)} onMoved={moved} />
      <MigrateDialog open={migrating !== null} preset={migrating ?? undefined} onClose={() => setMigrating(null)} onDone={() => setMigrating(null)} />
      {associations && (
        <ProjectAssociations initial={associations.initial} onClose={() => setAssociations(null)}
          onChanged={() => { void refresh(); reloadSources(); }} />
      )}
      <PurgeDialog open={purging !== null} initial={purging} onClose={() => setPurging(null)}
        onPurged={() => {
          setPurging(null);
          setList((prev) => ({ ...prev, selected: new Set(), anchor: null, expanded: null }));
          reloadSources();
          void refresh();
        }} />
      <SourceReader target={reading} onClose={() => setReading(null)} />
      <ShortcutsDialog open={help} onOpenChange={setHelp} shortcuts={SHORTCUTS} />
    </>
  );
}
