"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  ArrowDownAZ, ArrowDownWideNarrow, ArrowRightLeft, BookOpenText, ChevronLeft, ChevronRight, Copy, FileText,
  MessagesSquare, MoreHorizontal, Network, RefreshCw, Tags, X,
} from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { getTopics, getTopicDetail } from "@/lib/api";
import { useProjectNames } from "@/lib/use-projects";
import { revalidateCached, useCached } from "@/lib/use-cached";
import { ListSkeleton } from "@/components/loaders";
import { MigrateDialog, type MigratePreset } from "@/components/migrate-dialog";
import { ProjectMoveDialog } from "@/components/project-move-dialog";
import { Pager } from "@/components/review/pager";
import { PageHeader, PaneItem, SplitPage } from "@/components/split-page";
import { SourceReader, type ReaderTarget } from "@/components/source-reader";
import { cn } from "@/lib/utils";
import type { TopicOverviewData, TopicDetailData, TopicSort, TopicSource } from "@/lib/types";

const DESCRIPTION = "Themes tagged across your memories. Pick one to see the conversations and files it comes from, read them whole, and move them where they belong.";
/** Topics per page of the list; the server cuts it (ADR 0150). */
const TOPICS_PER_PAGE = 50;
/** Sources per page of a topic. */
const SOURCES_PER_PAGE = 20;

/** The chosen sort reads as chosen: full colour and a darker edge. */
const SORT_ITEM = "text-muted-foreground aria-pressed:border-foreground/30 aria-pressed:text-foreground";

const n = (value: number) => value.toLocaleString();
const plural = (count: number, one: string, many = `${one}s`) => `${n(count)} ${count === 1 ? one : many}`;
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const keyOf = (s: Pick<TopicSource, "project" | "source">) => `${s.project}\u0000${s.source}`;

function topicFromUrl() {
  return new URLSearchParams(window.location.search).get("topic");
}

/** Waits `ms` after the last change. */
function useDebounced<T>(value: T, ms: number) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

export default function TopicsPage() {
  // The list: one server page at a time, searched and sorted there.
  const [query, setQuery] = useState("");
  const q = useDebounced(query.trim(), 250);
  const [sort, setSort] = useState<TopicSort>("count");
  const [page, setPage] = useState(0);
  // Each page of the list is cached (ADR 0152): a page seen before shows at
  // once, and the page shown stays while the next one loads.
  const list = useCached(`topics:${JSON.stringify([q, sort, page])}`,
    () => getTopics({ q, sort, offset: page * TOPICS_PER_PAGE, limit: TOPICS_PER_PAGE }));
  const [held, setHeld] = useState<TopicOverviewData | undefined>(undefined);
  if (list.data && list.data !== held) setHeld(list.data);
  const listError = list.error ? message(list.error) : "";

  const [selected, setSelected] = useState<string | null>(() => (typeof window === "undefined" ? null : topicFromUrl()));
  const [migrating, setMigrating] = useState<MigratePreset | null>(null);
  const [moving, setMoving] = useState<{ project: string; sources: string[] } | null>(null);
  const [reading, setReading] = useState<ReaderTarget | null>(null);
  const [detailNonce, setDetailNonce] = useState(0);

  // Move targets, once something is to move.
  const { names: projects, loaded: projectsLoaded } = useProjectNames({ enabled: moving !== null });

  function select(name: string) {
    setSelected(name);
    window.history.replaceState(null, "", `?topic=${encodeURIComponent(name)}`);
  }

  const data = list.data ?? held;
  const listLoading = !list.data && !list.error;
  const topicCount = data ? data.topic_count ?? data.matched ?? data.topics.length : undefined;
  const matched = data?.matched ?? data?.topics.length ?? 0;
  const pages = Math.max(1, Math.ceil(matched / TOPICS_PER_PAGE));

  const header = <PageHeader title="Topics" count={topicCount} description={DESCRIPTION} updating={list.refreshing} />;

  if (listError && !data) return (
    <div>
      {header}
      <div className="px-4 sm:px-8">
        <Alert variant="destructive">
          <AlertTitle>Topics could not load: {listError}</AlertTitle>
          <AlertDescription>
            <Button variant="outline" size="sm" className="mt-1" onClick={() => void list.refresh()}><RefreshCw /> Try again</Button>
          </AlertDescription>
        </Alert>
      </div>
    </div>
  );

  if (!data) return (
    <SplitPage header={header} list={<ListSkeleton count={14} />}>
      <Skeleton className="h-40 w-full rounded-xl" />
    </SplitPage>
  );

  if (topicCount === 0 && !q) return (
    <div>
      {header}
      <div className="px-4 sm:px-8">
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon"><Tags aria-hidden /></EmptyMedia>
            <EmptyTitle>No topics yet</EmptyTitle>
            <EmptyDescription>Retag memories from <Link href="/commands/">Commands</Link> to give existing memories topics.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      </div>
    </div>
  );

  const toolbar = (
    <div className="flex flex-col gap-2">
      <InputGroup className="bg-card">
        <InputGroupAddon><Tags aria-hidden /></InputGroupAddon>
        <InputGroupInput aria-label="Search topics" placeholder={`Search ${topicCount ? n(topicCount) : ""} topics`} value={query}
          onChange={(e) => { setQuery(e.target.value); setPage(0); }}
          onKeyDown={(e) => { if (e.key === "Escape" && query) { e.preventDefault(); setQuery(""); setPage(0); } }} />
        {query && (
          <InputGroupAddon align="inline-end">
            <InputGroupButton size="icon-xs" aria-label="Clear the search" onClick={() => { setQuery(""); setPage(0); }}><X /></InputGroupButton>
          </InputGroupAddon>
        )}
      </InputGroup>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span className="min-w-0 flex-1 truncate" aria-live="polite">
          {q ? `${plural(matched, "match", "matches")}` : `${plural(matched, "topic")}`}
        </span>
        <ToggleGroup aria-label="Sort topics" size="sm" variant="outline" value={[sort]}
          onValueChange={(value) => { if (value[0]) { setSort(value[0] as TopicSort); setPage(0); } }}>
          <ToggleGroupItem value="count" aria-label="Largest first" title="Largest first" className={SORT_ITEM}><ArrowDownWideNarrow aria-hidden /> Size</ToggleGroupItem>
          <ToggleGroupItem value="name" aria-label="A to Z" title="A to Z" className={SORT_ITEM}><ArrowDownAZ aria-hidden /> Name</ToggleGroupItem>
        </ToggleGroup>
      </div>
    </div>
  );

  const pager = pages > 1 && (
    <nav aria-label="Topic pages" className="flex items-center gap-1 text-xs text-muted-foreground">
      <Button variant="ghost" size="icon-sm" aria-label="Previous topics" disabled={page <= 0 || listLoading} onClick={() => setPage(page - 1)}><ChevronLeft /></Button>
      <span className="flex-1 text-center tabular-nums">
        {n(page * TOPICS_PER_PAGE + 1)}–{n(Math.min(matched, (page + 1) * TOPICS_PER_PAGE))} of {n(matched)}
      </span>
      <Button variant="ghost" size="icon-sm" aria-label="Next topics" disabled={page >= pages - 1 || listLoading} onClick={() => setPage(page + 1)}><ChevronRight /></Button>
    </nav>
  );

  const max = Math.max(1, ...data.topics.map((t) => t.count));

  return (
    <SplitPage
      header={header}
      filter={toolbar}
      listFooter={pager}
      list={
        <nav aria-label="Topics" aria-busy={listLoading} className={cn("space-y-0.5 transition-opacity", listLoading && "opacity-60")}>
          {listError && <p role="alert" className="px-2.5 py-2 text-xs text-destructive">Could not load this page: {listError}</p>}
          {data.topics.map((t) => (
            <PaneItem key={t.id} selected={selected === t.name} onClick={() => select(t.name)} className="relative items-start overflow-hidden">
              <span aria-hidden className="absolute inset-y-1 left-0 w-0.5 rounded-full bg-foreground/15" style={{ opacity: 0.3 + 0.7 * (t.count / max) }} />
              <span className="min-w-0 flex-1">
                <span className="block truncate" title={t.name}>{t.name}</span>
                {t.topProjects.length > 0 && (
                  <span className="block truncate text-[11px] font-normal text-muted-foreground">{t.topProjects.slice(0, 3).join(" · ")}</span>
                )}
              </span>
              <span className="shrink-0 pt-px text-xs font-normal text-muted-foreground tabular-nums">{n(t.count)}</span>
            </PaneItem>
          ))}
          {!data.topics.length && !listLoading && (
            <div className="px-2.5 py-6 text-center text-sm text-muted-foreground">
              <p>No topic matches &ldquo;{q}&rdquo;.</p>
              <Button variant="link" size="sm" onClick={() => { setQuery(""); setPage(0); }}>Clear the search</Button>
            </div>
          )}
        </nav>
      }
    >
      {!selected ? (
        <Empty className="min-h-64 border border-dashed">
          <EmptyHeader>
            <EmptyMedia variant="icon"><Tags aria-hidden /></EmptyMedia>
            <EmptyTitle>Select a topic</EmptyTitle>
            <EmptyDescription>Its memories are grouped by the conversation or file they came from.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <TopicDetail key={`${selected}\u0000${detailNonce}`} topic={selected}
          onRead={setReading}
          onMigrate={(preset) => setMigrating(preset)}
          onMove={(project, sources) => setMoving({ project, sources })} />
      )}

      <MigrateDialog open={migrating !== null} preset={migrating ?? undefined}
        onClose={() => setMigrating(null)}
        onDone={() => { setMigrating(null); void list.refresh(); setDetailNonce((v) => v + 1); }} />
      <ProjectMoveDialog open={moving !== null && projectsLoaded} project={moving?.project ?? ""} sources={moving?.sources ?? null}
        projects={projects} onClose={() => setMoving(null)}
        onMoved={() => { setMoving(null); setDetailNonce((v) => v + 1); void revalidateCached("topics:"); }} />
      <SourceReader target={reading} onClose={() => setReading(null)} />
    </SplitPage>
  );
}

/** One topic: its counts and projects, then its sources a page at a time,
 * selectable to move to a project or another workspace. */
function TopicDetail({ topic, onRead, onMigrate, onMove }: {
  topic: string;
  onRead: (target: ReaderTarget) => void;
  onMigrate: (preset: MigratePreset) => void;
  onMove: (project: string, sources: string[]) => void;
}) {
  const [project, setProject] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(SOURCES_PER_PAGE);
  const [query, setQuery] = useState("");
  const q = useDebounced(query.trim(), 250);
  const [result, setResult] = useState<{ key: string; detail?: TopicDetailData; error?: string } | null>(null);
  // The whole topic's projects, kept while one is picked.
  const [overview, setOverview] = useState<TopicDetailData | null>(null);
  const [picked, setPicked] = useState<Map<string, TopicSource>>(new Map());
  const [nonce, setNonce] = useState(0);
  const top = useRef<HTMLDivElement>(null);
  const key = JSON.stringify([topic, project, page, size, q, nonce]);

  useEffect(() => {
    const ctrl = new AbortController();
    getTopicDetail(topic, project, { offset: page * size, limit: size, q }, ctrl.signal)
      .then((detail) => {
        setResult({ key, detail });
        if (project === null && !q) setOverview(detail);
      })
      .catch((e) => { if (!ctrl.signal.aborted) setResult((prev) => ({ key, detail: prev?.detail, error: message(e) })); });
    return () => ctrl.abort();
  }, [key, topic, project, page, size, q]);

  const toPage = useCallback((next: number) => {
    setPage(next);
    top.current?.scrollIntoView({ block: "nearest" });
  }, []);

  const detail = result?.detail;
  const loading = !result || result.key !== key;
  if (!detail) {
    if (result?.error) return (
      <Alert variant="destructive">
        <AlertTitle>{topic} could not load: {result.error}</AlertTitle>
        <AlertDescription><Button variant="outline" size="sm" className="mt-1" onClick={() => setNonce((v) => v + 1)}><RefreshCw /> Try again</Button></AlertDescription>
      </Alert>
    );
    return (
      <div className="space-y-4" aria-busy="true">
        <Skeleton className="h-28 w-full rounded-xl" />
        {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24 w-full rounded-xl" />)}
      </div>
    );
  }

  const whole = overview ?? detail;
  const sourceCount = detail.source_count ?? detail.sources.length;
  const pages = Math.max(1, Math.ceil(sourceCount / size));
  const pickedList = [...picked.values()];
  const pickedProjects = new Set(pickedList.map((s) => s.project));
  const toggle = (group: TopicSource, on: boolean) => setPicked((prev) => {
    const next = new Map(prev);
    if (on) next.set(keyOf(group), group); else next.delete(keyOf(group));
    return next;
  });
  const pageSources = detail.sources.filter((s) => s.source);
  const allOnPage = pageSources.length > 0 && pageSources.every((s) => picked.has(keyOf(s)));
  const projectsShown = whole.projects.length;
  const actions = (
    <>
      <Button size="sm" variant="outline" nativeButton={false} render={<Link href={`/graph/?scope=${encodeURIComponent(`topic:${whole.topic}`)}`} />}>
        <Network aria-hidden /> Graph
      </Button>
      <Button size="sm" variant="outline" onClick={() => onMigrate({ mode: "topic", value: whole.topic })}>
        <Copy aria-hidden /> Copy to workspace…
      </Button>
    </>
  );

  return (
    <div ref={top} className="@container/page space-y-4">
      <Card>
        <CardHeader>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
            <div className="min-w-0 flex-1 space-y-1">
              <CardTitle className="text-lg break-words">{whole.topic}</CardTitle>
              <CardDescription>
                {plural(whole.total, "memory", "memories")} from {plural(whole.source_count ?? whole.sources.length, "source")}
                {projectsShown > 1 && ` in ${plural(projectsShown, "project")}`}
              </CardDescription>
            </div>
            <div className="flex flex-wrap gap-2">{actions}</div>
          </div>
        </CardHeader>
        {projectsShown > 1 && (
          <CardContent>
            <div role="group" aria-label="Show sources from" className="flex flex-wrap gap-1.5">
              <Chip on={project === null} onClick={() => { setProject(null); setPage(0); }}>All projects</Chip>
              {whole.projects.map(([name, count]) => (
                <Chip key={name} on={project === name} onClick={() => { setProject(name); setPage(0); }}>
                  {name || "No project"} <span className="text-muted-foreground tabular-nums">{n(count)}</span>
                </Chip>
              ))}
            </div>
          </CardContent>
        )}
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        {pageSources.length > 0 && (
          <Checkbox aria-label="Select every source on this page" checked={allOnPage}
            indeterminate={!allOnPage && pageSources.some((s) => picked.has(keyOf(s)))}
            onCheckedChange={(on) => setPicked((prev) => {
              const next = new Map(prev);
              for (const s of pageSources) { if (on) next.set(keyOf(s), s); else next.delete(keyOf(s)); }
              return next;
            })} className="ml-3.5" />
        )}
        <span className="text-sm text-muted-foreground tabular-nums" aria-live="polite">
          {q ? `${plural(sourceCount, "source")} match` : `${plural(sourceCount, "source")}${project !== null ? ` in ${project || "no project"}` : ""}`}
        </span>
        <span className="flex-1" />
        {(sourceCount > size || q) && (
          <InputGroup className="w-full bg-card sm:w-64">
            <InputGroupInput aria-label="Search this topic's sources" placeholder="Search sources" value={query}
              onChange={(e) => { setQuery(e.target.value); setPage(0); }} />
            {query && (
              <InputGroupAddon align="inline-end">
                <InputGroupButton size="icon-xs" aria-label="Clear the source search" onClick={() => { setQuery(""); setPage(0); }}><X /></InputGroupButton>
              </InputGroupAddon>
            )}
          </InputGroup>
        )}
      </div>

      {picked.size > 0 && (
        <div role="toolbar" aria-label="Selected sources"
          className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-xl border border-border bg-background/95 px-3 py-2 shadow-sm backdrop-blur">
          <span className="text-sm font-medium">{plural(picked.size, "source")} selected</span>
          <span className="flex-1" />
          <Button size="sm" variant="outline" disabled={pickedProjects.size !== 1}
            title={pickedProjects.size !== 1 ? "Moving to a project takes sources of one project at a time: show one project above." : undefined}
            onClick={() => onMove(pickedList[0].project, pickedList.map((s) => s.source))}>
            <ArrowRightLeft aria-hidden /> Move to project…
          </Button>
          <Button size="sm" variant="outline" onClick={() => onMigrate({ mode: "source", values: [...new Set(pickedList.map((s) => s.source))] })}>
            <Copy aria-hidden /> Copy to workspace…
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setPicked(new Map())}>Clear</Button>
          {pickedProjects.size > 1 && (
            <p className="w-full text-xs text-muted-foreground">They come from {plural(pickedProjects.size, "project")}; a move to another project takes one project&apos;s sources at a time.</p>
          )}
        </div>
      )}

      {result?.error && <p role="alert" className="text-sm text-destructive">This page could not load: {result.error}</p>}
      <ul aria-label="Sources" aria-busy={loading} className={cn("space-y-3 transition-opacity", loading && "opacity-60")}>
        {detail.sources.map((s) => (
          <li key={keyOf(s)}>
            <SourceCard group={s} topic={detail.topic} onRead={onRead} picked={picked.has(keyOf(s))}
              onPick={(on) => toggle(s, on)}
              onMove={() => onMove(s.project, [s.source])}
              onMigrate={() => onMigrate({ mode: "source", value: s.source })} />
          </li>
        ))}
      </ul>
      {!detail.sources.length && !loading && (
        <p className="py-6 text-center text-sm text-muted-foreground">{q ? `No source matches “${q}”.` : "No sources."}</p>
      )}
      <Pager page={page} pages={pages} size={size} shown={detail.sources.length} total={sourceCount}
        onPage={toPage} onSize={(next) => { setSize(next); setPage(0); }} />
    </div>
  );
}

function Chip({ on, className, ...props }: React.ComponentProps<"button"> & { on: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      className={cn(
        "inline-flex h-7 max-w-full items-center gap-1.5 truncate rounded-full border px-3 text-xs transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
        on ? "border-foreground bg-foreground text-background [&_span]:text-background/70" : "border-border bg-background hover:bg-muted",
        className,
      )}
      {...props}
    />
  );
}

const SHOWN = 2;

/** One conversation or file a topic comes from, its tagged passages, and a way to read it whole. */
function SourceCard({ group, topic, onRead, picked, onPick, onMove, onMigrate }: {
  group: TopicSource; topic: string; onRead: (target: ReaderTarget) => void;
  picked: boolean; onPick: (on: boolean) => void; onMove: () => void; onMigrate: () => void;
}) {
  const [all, setAll] = useState(false);
  const conversation = group.source_type === "conversation";
  const Icon = conversation ? MessagesSquare : FileText;
  const name = group.title || group.source || "Memories saved without a source";
  const shown = all ? group.chunks : group.chunks.slice(0, SHOWN);
  const read = (chunk?: number) => onRead({ source: group.source, project: group.project, topic, chunk, title: group.title });
  return (
    <Card size="sm" className={cn("gap-3", picked && "ring-2 ring-primary/40")}>
      <CardHeader>
        <div className="flex min-w-0 items-start gap-3">
          {group.source ? (
            <Checkbox aria-label={`Select ${name}`} checked={picked} onCheckedChange={(on) => onPick(on)} className="mt-2" />
          ) : <span className="w-4" />}
          <span className="hidden size-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground sm:grid"><Icon className="size-4" aria-hidden /></span>
          <div className="min-w-0 space-y-0.5">
            <CardTitle className="line-clamp-2 leading-snug break-words" title={name}>{name}</CardTitle>
            <CardDescription className="flex min-w-0 flex-wrap gap-x-1.5 text-xs">
              {group.title && <span className="max-w-full truncate font-mono">{group.source}</span>}
              <span>{group.title && "· "}{group.project || "No project"}</span>
              <span>· {plural(group.chunks.length, "passage")}</span>
            </CardDescription>
          </div>
        </div>
        {group.source && (
          <CardAction className="flex items-center gap-1">
            <Button size="sm" variant="secondary" onClick={() => read()} aria-label={`Read ${conversation ? "conversation" : "source"}: ${name}`}>
              <BookOpenText aria-hidden /> <span className="hidden sm:inline">Read</span>
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`More for ${name}`} />}>
                <MoreHorizontal />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-60">
                <DropdownMenuItem onClick={onMove}><ArrowRightLeft /> Move to another project…</DropdownMenuItem>
                <DropdownMenuItem onClick={onMigrate}><Copy /> Copy or move to a workspace…</DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem render={<Link href={`/graph/?scope=${encodeURIComponent(`source:${group.source}`)}`} />}><Network /> Show in the graph</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </CardAction>
        )}
      </CardHeader>
      <CardContent className="space-y-1.5">
        {shown.map((chunk) => (
          <button
            key={chunk.id}
            type="button"
            disabled={!group.source}
            onClick={() => read(chunk.chunk_index)}
            className="block w-full rounded-lg border border-border/70 bg-muted/30 px-3 py-2 text-left text-xs leading-relaxed text-foreground/80 transition-colors outline-none hover:border-foreground/20 hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none"
          >
            <span className="line-clamp-2 whitespace-pre-wrap break-words">{chunk.content}</span>
          </button>
        ))}
        {group.chunks.length > SHOWN && (
          <Button variant="ghost" size="xs" className="font-normal text-muted-foreground" onClick={() => setAll((v) => !v)}>
            {all ? "Show fewer" : `Show ${group.chunks.length - SHOWN} more`}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
