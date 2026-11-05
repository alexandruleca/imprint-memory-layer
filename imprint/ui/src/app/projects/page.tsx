"use client";

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
  connectSSE, getOrganize, getOverview, getProjectDetail, listAllSources, type ProjectMove, type SourceRow,
} from "@/lib/api";
import type { OverviewData } from "@/lib/types";
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
  const announce = useAnnounce();
  const [data, setData] = useState<OverviewData | null>(null);
  const [error, setError] = useState("");
  const [chosen, setChosen] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ project: string; detail: ProjectDetail } | null>(null);
  const [sources, setSources] = useState<{ project: string; rows: SourceRow[]; error?: string } | null>(null);
  const [nonce, setNonce] = useState(0);
  const [stale, setStale] = useState(false);
  const [list, setList] = useState<ReviewTabState>(() => reviewTab(SOURCE_TAB));
  const [profiles, setProfiles] = useState<Map<string, { text: string; updated: number }> | null>(null);
  const [moving, setMoving] = useState<{ project: string; sources: string[] | null } | null>(null);
  const [migrating, setMigrating] = useState<MigratePreset | null>(null);
  const [associations, setAssociations] = useState<{ initial?: { source: string; project: string } } | null>(null);
  const [purging, setPurging] = useState<PurgeTarget | null>(null);
  const [reading, setReading] = useState<ReaderTarget | null>(null);
  const [help, setHelp] = useState(false);
  const [linkedTab, openTab] = useHashTab(TABS);
  const [size, setSize] = useStoredNumber("imprint.review.page-size", 20, PAGE_SIZES);
  const tab: Tab = linkedTab ?? "overview";
  const loadedAt = useRef(0);

  const names = useMemo(() => data?.projects.map((p) => p.name) ?? [], [data]);
  // A project that went away (merged, moved, purged) gives way to the largest.
  const selected = chosen && names.includes(chosen) ? chosen : names[0] ?? null;
  const selectedRef = useRef(selected);
  useEffect(() => { selectedRef.current = selected; }, [selected]);

  // The overview and the open project's facets follow every data change; its
  // sources wait for Refresh so rows do not move under the cursor.
  const refreshing = useRef<Promise<void> | null>(null);
  const again = useRef(false);
  const refresh = useCallback(async () => {
    if (refreshing.current) { again.current = true; return refreshing.current; }
    const run = (async () => {
      do {
        again.current = false;
        const overview = await getOverview();
        setData(overview);
        setError("");
        setChosen((prev) => prev ?? wanted());
        const name = selectedRef.current;
        if (name) {
          const next = await getProjectDetail(name) as ProjectDetail;
          if (selectedRef.current === name) setDetail({ project: name, detail: next });
        }
      } while (again.current);
    })();
    refreshing.current = run;
    try { await run; } finally { refreshing.current = null; }
  }, []);

  useEffect(() => {
    refresh().catch((e) => setError(message(e)));
    const events = connectSSE(() => {
      refresh().catch(() => {});
      if (Date.now() - loadedAt.current > SETTLE_MS) setStale(true);
    });
    return () => events.close();
  }, [refresh]);

  // The open project's facets and sources, again after a move or Refresh.
  useEffect(() => {
    if (!selected) return;
    let live = true;
    getProjectDetail(selected).then((next) => { if (live) setDetail({ project: selected, detail: next as ProjectDetail }); }).catch(() => {});
    listAllSources(selected).then(
      (result) => {
        if (!live) return;
        setSources({ project: selected, rows: result.sources.filter((row) => row.source) });
        setStale(false);
        loadedAt.current = Date.now();
      },
      (e) => { if (live) setSources({ project: selected, rows: [], error: message(e) }); });
    return () => { live = false; };
  }, [selected, nonce]);

  // Organize's profiles, once, for the Overview.
  useEffect(() => {
    if (tab !== "overview" || profiles) return;
    let live = true;
    getOrganize().then((review) => {
      if (live) setProfiles(new Map((Array.isArray(review?.profiles) ? review.profiles : []).map((p) => [p.project, { text: p.text, updated: p.updated }])));
    }, () => { if (live) setProfiles(new Map()); });
    return () => { live = false; };
  }, [tab, profiles]);

  const select = useCallback((name: string) => {
    setChosen(name);
    setList((prev) => reviewTab({ sort: prev.sort }));
    setStale(false);
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
      setNonce((n) => n + 1);
    }
    refresh().catch((e) => setError(message(e)));
  }

  useReviewShortcuts(root, {
    J: () => step(1), K: () => step(-1), "1": () => openTab("overview"), "2": () => openTab("sources"), "?": () => setHelp(true),
  });

  const project = data?.projects.find((p) => p.name === selected);
  const shownDetail = detail && detail.project === selected ? detail.detail : null;
  const rows = sources && sources.project === selected ? sources : null;
  const groups: RailGroup[] = (data?.projects ?? []).map((p) => ({ name: p.name, count: p.count, color: p.color }));

  const read = (row: SourceRow, chunk?: number) =>
    setReading({ source: row.source, project: row.project ?? selected, chunk, title: row.title || row.source });
  const actions = {
    onRead: read,
    onMove: (row: SourceRow) => { if (selected) setMoving({ project: selected, sources: [row.source] }); },
    onLink: (row: SourceRow) => setAssociations({ initial: { source: row.source, project: row.project ?? selected ?? "" } }),
    onMigrate: (row: SourceRow) => setMigrating({ mode: "source", value: row.source }),
    onPurge: (row: SourceRow) => setPurging({ target: row.source, kind: "source", recursive: false }),
    onGraph: (row: SourceRow) => router.push(`/graph/?scope=${encodeURIComponent(`source:${row.source}`)}`),
  };

  const header = (
    <ReviewHeader title="Projects" count={data?.projects.length} info={INFO} infoLabel="About Projects"
      actions={<Button variant="outline" onClick={() => setAssociations({})}><Link2 /> Project associations…</Button>} />
  );

  const errorAlert = error && (
    <div className="px-4 pb-3 sm:px-8">
      <Alert variant="destructive">
        <AlertTitle>Projects could not load: {error}</AlertTitle>
        <AlertDescription>
          <Button variant="outline" size="sm" className="mt-1" onClick={() => { refresh().catch((e) => setError(message(e))); }}><RefreshCw /> Try again</Button>
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
          description="Projects appear as memories are saved: ingest a folder or learn your conversations from Commands." />
      </div>
    );
  } else {
    body = (
      <div className="grid min-h-0 flex-1 gap-4 px-4 pb-4 sm:px-8 lg:grid-rows-[minmax(0,1fr)] @min-[760px]/page:grid-cols-[15rem_minmax(0,1fr)]">
        <GroupRail label="Projects" groups={groups} total={data.total} value={selected} onChange={select} allLabel={null} />
        <section aria-label={selected} className="flex min-h-0 min-w-0 flex-col gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
            <GroupSelect groups={groups} total={data.total} value={selected} onChange={(next) => next && select(next)} allLabel={null} />
            <h2 className="hidden min-w-0 items-center gap-2 text-lg font-semibold @min-[760px]/page:flex">
              <span aria-hidden className="size-3 shrink-0 rounded-full" style={{ backgroundColor: project?.color }} />
              <span className="truncate">{selected}</span>
            </h2>
            <span className="text-sm text-muted-foreground tabular-nums @max-[620px]/page:hidden">
              {(shownDetail?.count ?? project?.count ?? 0).toLocaleString()} memories
              {rows && !rows.error && ` · ${rows.rows.length.toLocaleString()} ${rows.rows.length === 1 ? "source" : "sources"}`}
            </span>
            <span className="flex-1" />
            <div className="flex items-center gap-1">
              <ActionButton icon={<Network />} label="Graph" hint="Show the project in the graph"
                onClick={() => router.push(`/graph/?scope=${encodeURIComponent(`project:${selected}`)}`)} />
              <ActionButton icon={<ArrowRightLeft />} label="Move or merge…" hint="Move or merge the whole project into another one"
                onClick={() => setMoving({ project: selected, sources: null })} />
              <ActionButton icon={<Copy />} label="Migrate…" hint="Copy or move the project to another workspace"
                onClick={() => setMigrating({ mode: "project", value: selected })} />
            </div>
          </div>
          <Tabs value={tab} onValueChange={(value) => openTab(value as Tab)} className="flex min-h-0 flex-1 flex-col gap-0">
            <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-border bg-background lg:static">
              <TabsList variant="line" className="h-10 min-w-0 justify-start overflow-x-auto">
                <TabsTrigger value="overview" className="flex-none px-2.5">Overview</TabsTrigger>
                {rows && !rows.error ? <CountTab value="sources" name="Sources" count={rows.rows.length} />
                  : <TabsTrigger value="sources" className="flex-none px-2.5">Sources</TabsTrigger>}
              </TabsList>
              <span className="flex-1" />
              <span className="@max-[520px]/page:hidden"><ShortcutsButton onClick={() => setHelp(true)} /></span>
            </div>
            <TabsContent value="overview" className="min-h-0 flex-1 pt-3 lg:overflow-y-auto">
              <ProjectOverview detail={shownDetail} sources={rows ? rows.rows : null} profile={profiles?.get(selected)}
                onTopic={(topic) => router.push(`/topics/?topic=${encodeURIComponent(topic)}`)}
                onSources={(kind) => { setList((prev) => ({ ...prev, ...RESET, filter: kind })); openTab("sources"); }} />
            </TabsContent>
            <TabsContent value="sources" className="flex min-h-0 flex-1 flex-col gap-2 pt-3">
              {stale && rows && <StaleNote busy={false} onRefresh={() => setNonce((n) => n + 1)} what="This project" />}
              {!rows ? <div className="space-y-2" aria-busy="true"><Skeleton className="h-10 w-full rounded-xl" /><ListSkeleton count={8} /></div>
                : rows.error ? (
                  <Alert variant="destructive">
                    <AlertTitle>Its sources could not load: {rows.error}</AlertTitle>
                    <AlertDescription>
                      <Button variant="outline" size="sm" className="mt-1" onClick={() => setNonce((n) => n + 1)}><RefreshCw /> Try again</Button>
                    </AlertDescription>
                  </Alert>
                ) : (
                  <SourceList sources={rows.rows} scope={selected} state={list} onState={(patch) => setList((prev) => ({ ...prev, ...patch }))}
                    size={size} onSize={(next) => { setSize(next); setList((prev) => ({ ...prev, page: 0, reveal: false })); }}
                    root={root} label={`Sources of ${selected}`} actions={actions}
                    onMove={(moveRows) => setMoving({ project: selected, sources: moveRows.map((row) => row.source) })}
                    empty={<ReviewEmpty title="No sources." description="This project holds only memories saved without one." />} />
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
          onChanged={() => { refresh().catch((e) => setError(message(e))); setNonce((n) => n + 1); }} />
      )}
      <PurgeDialog open={purging !== null} initial={purging} onClose={() => setPurging(null)}
        onPurged={() => {
          setPurging(null);
          setList((prev) => ({ ...prev, selected: new Set(), anchor: null, expanded: null }));
          setNonce((n) => n + 1);
          refresh().catch((e) => setError(message(e)));
        }} />
      <SourceReader target={reading} onClose={() => setReading(null)} />
      <ShortcutsDialog open={help} onOpenChange={setHelp} shortcuts={SHORTCUTS} />
    </>
  );
}
