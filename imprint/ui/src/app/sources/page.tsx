"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { ListSkeleton } from "@/components/loaders";
import { MigrateDialog } from "@/components/migrate-dialog";
import { ProjectAssociations } from "@/components/project-associations";
import { ProjectMoveDialog } from "@/components/project-move-dialog";
import { PAGE_SIZES } from "@/components/review/pager";
import { CountTab, ReviewHeader } from "@/components/review/review-header";
import { Announcer, ReviewEmpty, reviewTab, type ReviewTabState } from "@/components/review/review-list";
import { ShortcutsButton, ShortcutsDialog, type Shortcut } from "@/components/review/shortcuts-dialog";
import { SourceReader, type ReaderTarget } from "@/components/source-reader";
import { ExcludedPanel, type Exclusion } from "@/components/sources/excluded-panel";
import { PurgeDialog, type PurgeTarget } from "@/components/sources/purge-dialog";
import { SOURCE_TAB, SourceList } from "@/components/sources/source-list";
import { StaleNote } from "@/components/sources/stale-note";
import { WorkspaceRoots } from "@/components/workspace-roots";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList } from "@/components/ui/tabs";
import {
  changeWorkspaceRoot, connectSSE, getOverview, getWorkspaces, getWorkspaceScope, listAllSources, SOURCE_LIMIT,
  type ProjectMove, type SourceRow, type WorkspaceScope,
} from "@/lib/api";
import { useHashTab } from "@/lib/use-hash-tab";
import { useReviewShortcuts } from "@/lib/use-review-shortcuts";
import { useStoredNumber } from "@/lib/use-stored";

const TABS = ["indexed", "excluded", "roots"] as const;
type Tab = (typeof TABS)[number];
type ListTab = Exclude<Tab, "roots">;

const INFO = (
  <div className="space-y-2">
    <p className="font-medium">What Sources lists</p>
    <p>Every conversation, file, document and web page indexed in this workspace, largest first. Read one whole, open its details and chunks, move it to another project or purge it.</p>
    <p><span className="font-medium">Excluded</span> lists what purges keep out of ingestion; <span className="font-medium">Roots</span> the folders this workspace may ingest from.</p>
    <p className="text-muted-foreground">Projects shows the same list for one project, with what the project holds.</p>
  </div>
);

const SHORTCUTS: Shortcut[] = [
  [["j", "↓"], "Next row"], [["k", "↑"], "Previous row"], [["x"], "Select the row"],
  [["Enter"], "Show or hide details and chunks"], [["o"], "Read the source"], [["m"], "Move to another project"],
  [["a"], "Allow re-ingestion (Excluded)"], [["[", "]"], "Previous / next page"], [["/"], "Search"], [["1", "3"], "Switch tab"],
  [["Esc"], "Clear the selection, close details"], [["?"], "This list"],
];

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
/** A data change this soon after a load is the load's own, or the action's that caused it. */
const SETTLE_MS = 4000;

export default function SourcesPage() {
  const root = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const [data, setData] = useState<{ sources: SourceRow[]; total: number } | null>(null);
  const [error, setError] = useState("");
  const [scope, setScope] = useState<WorkspaceScope | null>(null);
  const [scopeError, setScopeError] = useState("");
  const [projects, setProjects] = useState<string[]>([]);
  const [stale, setStale] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [tabs, setTabs] = useState<Record<ListTab, ReviewTabState>>(() => ({ indexed: reviewTab(SOURCE_TAB), excluded: reviewTab() }));
  const [reading, setReading] = useState<ReaderTarget | null>(null);
  const [moving, setMoving] = useState<{ project: string; sources: string[] } | null>(null);
  const [migrating, setMigrating] = useState<string | null>(null);
  const [linking, setLinking] = useState<{ source: string; project: string } | null>(null);
  const [purging, setPurging] = useState<{ initial: PurgeTarget | null } | null>(null);
  const [allowing, setAllowing] = useState(false);
  const [help, setHelp] = useState(false);
  const [linkedTab, openTab] = useHashTab(TABS);
  const [size, setSize] = useStoredNumber("imprint.review.page-size", 20, PAGE_SIZES);
  const loadedAt = useRef(0);
  const tab: Tab = linkedTab ?? "indexed";

  const absorb = useCallback((list: { sources: SourceRow[]; total: number }, names: string[] | null) => {
    setData(list);
    setError("");
    setStale(false);
    loadedAt.current = Date.now();
    if (names) setProjects(names);
  }, []);

  useEffect(() => {
    let live = true;
    Promise.all([listAllSources(), getOverview().catch(() => null)])
      .then(([list, overview]) => { if (live) absorb(list, overview ? overview.projects.map((p) => p.name) : null); },
            (e) => { if (live) setError(message(e)); });
    getWorkspaces().then((ws) => getWorkspaceScope(ws.active))
      .then((next) => { if (live) setScope(next); }, (e) => { if (live) setScopeError(message(e)); });
    const events = connectSSE(() => { if (Date.now() - loadedAt.current > SETTLE_MS) setStale(true); });
    return () => { live = false; events.close(); };
  }, [absorb]);

  const reload = useCallback(async () => {
    setReloading(true);
    try {
      const [list, overview] = await Promise.all([listAllSources(), getOverview().catch(() => null)]);
      absorb(list, overview ? overview.projects.map((p) => p.name) : null);
    } catch (e) {
      setError(message(e));
    } finally { setReloading(false); }
  }, [absorb]);

  const patchTab = useCallback((which: ListTab, patch: Partial<ReviewTabState>) => {
    setTabs((prev) => ({ ...prev, [which]: { ...prev[which], ...patch } }));
  }, []);
  const onSize = (next: number) => {
    setSize(next);
    setTabs((prev) => ({ indexed: { ...prev.indexed, page: 0, reveal: false }, excluded: { ...prev.excluded, page: 0, reveal: false } }));
  };

  function moved(result: ProjectMove) {
    setMoving(null);
    const gone = new Set(result.sample);
    setTabs((prev) => ({ ...prev, indexed: { ...prev.indexed, selected: new Set([...prev.indexed.selected].filter((key) => !gone.has(key))) } }));
    void reload();
  }

  async function allow(items: Exclusion[]) {
    if (!scope || !items.length) return;
    setAllowing(true);
    const failed: string[] = [];
    for (const item of items) {
      try { await changeWorkspaceRoot({ workspace: scope.workspace, path: item.path, action: "restore", kind: item.kind }); }
      catch (e) { failed.push(message(e)); }
    }
    try { setScope(await getWorkspaceScope(scope.workspace)); } catch {}
    setAllowing(false);
    const done = items.length - failed.length;
    if (done) toast(`Allowed ${done === 1 ? items[0].path : `${done} paths`} again`, { description: "The next ingest or refresh indexes what is under the workspace's roots." });
    if (failed.length) toast.error(`${failed.length} could not be allowed again`, { description: failed[0] });
  }

  useReviewShortcuts(root, {
    "1": () => openTab("indexed"), "2": () => openTab("excluded"), "3": () => openTab("roots"), "?": () => setHelp(true),
  });

  const read = (row: SourceRow, chunk?: number) => setReading({ source: row.source, project: row.project ?? undefined, chunk, title: row.title || row.source });
  const actions = {
    onRead: read,
    onMove: (row: SourceRow) => { if (row.project) setMoving({ project: row.project, sources: [row.source] }); },
    onLink: (row: SourceRow) => setLinking({ source: row.source, project: row.project ?? "" }),
    onMigrate: (row: SourceRow) => setMigrating(row.source),
    onPurge: (row: SourceRow) => setPurging({ initial: { target: row.source, kind: "source", recursive: false } }),
    onGraph: (row: SourceRow) => router.push(`/graph/?scope=${encodeURIComponent(`source:${row.source}`)}`),
  };

  const scopeMissing = (
    scopeError ? (
      <Alert variant="destructive"><AlertTitle>Could not load the workspace&apos;s roots: {scopeError}</AlertTitle></Alert>
    ) : <div className="space-y-2" aria-busy="true"><Skeleton className="h-10 w-full rounded-xl" /><ListSkeleton count={4} /></div>
  );

  return (
    <Announcer>
      <div ref={root} className="@container/page flex min-h-full flex-col lg:h-full">
        <ReviewHeader title="Sources" count={data?.total} info={INFO} infoLabel="About Sources" />
        {error && (
          <div className="px-4 pb-3 sm:px-8">
            <Alert variant="destructive">
              <AlertTitle>Sources could not load: {error}</AlertTitle>
              <AlertDescription>
                <Button variant="outline" size="sm" className="mt-1" disabled={reloading} onClick={() => void reload()}><RefreshCw /> Try again</Button>
              </AlertDescription>
            </Alert>
          </div>
        )}
        <Tabs value={tab} onValueChange={(value) => openTab(value as Tab)} className="flex min-h-0 flex-1 flex-col gap-0">
          <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-border bg-background px-4 sm:px-8 lg:static">
            <TabsList variant="line" className="h-10 min-w-0 justify-start overflow-x-auto">
              <CountTab value="indexed" name="Indexed" count={data?.total ?? 0} />
              <CountTab value="excluded" name="Excluded" count={scope?.exclusions.length ?? 0} />
              <CountTab value="roots" name="Roots" count={scope?.roots.length ?? 0} />
            </TabsList>
            <span className="flex-1" />
            {scope && <span className="hidden text-xs text-muted-foreground @min-[620px]/page:inline">Workspace {scope.workspace}</span>}
            <span className="@max-[520px]/page:hidden"><ShortcutsButton onClick={() => setHelp(true)} /></span>
          </div>
          <TabsContent value="indexed" className="flex min-h-0 flex-1 flex-col gap-2 px-4 pt-3 pb-4 sm:px-8">
            {stale && <StaleNote busy={reloading} onRefresh={() => void reload()} />}
            {!data ? (!error && <div className="space-y-2" aria-busy="true"><Skeleton className="h-10 w-full rounded-xl" /><ListSkeleton count={8} /></div>) : (
              <section aria-label="Indexed sources" className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
                {data.total >= SOURCE_LIMIT && (
                  <p className="text-xs text-muted-foreground">Listing the {SOURCE_LIMIT.toLocaleString()} sources with the most chunks.</p>
                )}
                <SourceList sources={data.sources} state={tabs.indexed} onState={(patch) => patchTab("indexed", patch)}
                  size={size} onSize={onSize} root={root} label="Indexed sources" actions={actions}
                  onMove={(rows) => setMoving({ project: rows[0].project ?? "", sources: rows.map((row) => row.source) })}
                  empty={(
                    <ReviewEmpty title="Nothing is indexed yet."
                      description={<>Ingest a project or learn your conversations from <Link href="/commands/" className="underline underline-offset-2">Commands</Link>.</>} />
                  )} />
              </section>
            )}
          </TabsContent>
          <TabsContent value="excluded" className="flex min-h-0 flex-1 flex-col px-4 pt-3 pb-4 sm:px-8">
            {scope ? (
              <ExcludedPanel scope={scope} state={tabs.excluded} onState={(patch) => patchTab("excluded", patch)} size={size} onSize={onSize}
                root={root} busy={allowing} onAllow={(items) => void allow(items)} onPurge={() => setPurging({ initial: null })} />
            ) : scopeMissing}
          </TabsContent>
          <TabsContent value="roots" className="flex min-h-0 flex-1 flex-col px-4 pt-3 pb-4 sm:px-8">
            {scope ? (
              <section aria-label="Roots" className="max-w-3xl rounded-xl border border-border bg-card p-4">
                <WorkspaceRoots scope={scope} onChange={setScope} />
              </section>
            ) : scopeMissing}
          </TabsContent>
        </Tabs>
      </div>
      <ProjectMoveDialog open={moving !== null} project={moving?.project ?? ""} sources={moving?.sources ?? null}
        projects={projects} onClose={() => setMoving(null)} onMoved={moved} />
      <MigrateDialog open={migrating !== null} preset={{ mode: "source", value: migrating ?? "" }}
        onClose={() => setMigrating(null)} onDone={() => setMigrating(null)} />
      {linking && <ProjectAssociations initial={linking} onClose={() => setLinking(null)} onChanged={() => setStale(true)} />}
      <PurgeDialog open={purging !== null} initial={purging?.initial} onClose={() => setPurging(null)}
        onPurged={(result) => {
          setPurging(null);
          patchTab("indexed", { selected: new Set(), anchor: null, expanded: null });
          getWorkspaceScope(result.workspace).then(setScope).catch(() => {});
          void reload();
        }} />
      <SourceReader target={reading} onClose={() => setReading(null)} />
      <ShortcutsDialog open={help} onOpenChange={setHelp} shortcuts={SHORTCUTS} />
    </Announcer>
  );
}
