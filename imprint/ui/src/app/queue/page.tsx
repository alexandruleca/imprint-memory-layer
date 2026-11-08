"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import {
  Ban, Check, CheckCircle2, ChevronLeft, ChevronRight, CircleDashed, Clock, Copy, PauseCircle, Play, RotateCw, Sparkles,
  XCircle,
} from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Hint } from "@/components/hint";
import { Spinner } from "@/components/loaders";
import { PageHeader } from "@/components/split-page";
import { CancelButton, JobName, RetryButton, RunningJob, jobTitle } from "@/components/ingestion-progress";
import { getQueueHistory, resumeBackgroundTagging, tailJobStream } from "@/lib/api";
import { groupByPeriod, periodStarts } from "@/lib/time-groups";
import { ago, formatDuration, runTime, useQueue } from "@/lib/queue";
import { revalidateCached, useCached } from "@/lib/use-cached";
import { useFollowOutput } from "@/lib/use-follow-output";
import { cn } from "@/lib/utils";
import type { BackgroundTagging, BackgroundTaggingState, QueueHistory, QueueJob, QueueJobStatus } from "@/lib/types";

const DESCRIPTION = "Imprint runs one job at a time, in the order they were asked for. Background work (LLM tagging, then Organize) fills the time in between and steps aside for anything else.";
const PAGE_SIZE = 25;

const STATUS: Record<QueueJobStatus, { label: string; icon: React.ElementType; className: string }> = {
  running: { label: "Running", icon: Spinner, className: "text-foreground" },
  queued: { label: "Waiting", icon: Clock, className: "text-muted-foreground" },
  done: { label: "Done", icon: CheckCircle2, className: "text-success" },
  failed: { label: "Failed", icon: XCircle, className: "text-destructive" },
  cancelled: { label: "Cancelled", icon: Ban, className: "text-muted-foreground" },
  yielded: { label: "Stepped aside", icon: PauseCircle, className: "text-muted-foreground" },
};

function result(job: QueueJob) {
  const status = STATUS[job.status];
  return job.status === "failed" && job.exit_code != null ? `${status.label} · exit ${job.exit_code}` : status.label;
}

function StatusIcon({ status, className }: { status: QueueJobStatus; className?: string }) {
  const { icon: Icon, className: tone } = STATUS[status];
  return <Icon className={cn("size-4 shrink-0", tone, className)} aria-hidden />;
}

/** Why a job failed, in a line: its error's last line without the cancel
 * marker or a bare traceback header; "" when it gave none. */
function reason(job: QueueJob) {
  return job.error?.replace(/cancelled[^;]*;\s*/g, "").replace(/^Traceback \(most recent call last\):\s*/, "")
    .trim().split("\n").filter(Boolean).pop() ?? "";
}

/** When a job ended, as precise as its group needs: a time today and
 * yesterday, a weekday and time this week, then a date. */
function endedAt(ts: number, period: string) {
  const date = new Date(ts * 1000);
  const time = date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (period === "Today" || period === "Yesterday") return time;
  if (period === "This week" || period === "Last week") return `${date.toLocaleDateString(undefined, { weekday: "short" })} ${time}`;
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) });
}

const memories = (n: number) => `${n.toLocaleString()} ${n === 1 ? "memory" : "memories"}`;

const STATE_LABEL: Record<BackgroundTaggingState, string> = {
  off: "Off", running: "Running", yielded: "Stepped aside", waiting: "Waiting", paused: "Paused", failed: "Failed", idle: "Up to date",
};

function explain(bg: BackgroundTagging): string {
  const waiting = bg.pending != null ? memories(bg.pending) : "Memories";
  const tagging = bg.tagging ?? bg.enabled;
  const organizing = bg.organize?.enabled ?? false;
  switch (bg.state) {
    case "off":
      if (bg.peer && bg.pending) return `${waiting} wait for LLM tagging on a paired device. Sync takes them there and brings their tags back.`;
      return bg.pending
        ? `${waiting} wait for LLM tagging. Turn on LLM tagging (tagger.llm) in Settings to tag them here.`
        : "LLM tagging (tagger.llm) and background Organize (organize.background) are off. Memories keep their keyword and zero-shot tags.";
    case "running":
      return bg.job?.phase === "organize"
        ? "Organizing what changed: project profiles, moves, core memories and guidelines drawn from them. It steps aside between sources whenever another job is waiting."
        : "Tagging the newest memories first. It steps aside between memories whenever another job is waiting.";
    case "yielded":
      return "Stepped aside for another job; it resumes when the queue is idle.";
    case "waiting":
      return "Starts when no other job runs or waits.";
    case "paused":
      return tagging && bg.pending
        ? `Cancelled with ${waiting.toLowerCase()} waiting. It resumes when more memories wait.`
        : "Cancelled. It resumes when new memories arrive.";
    case "failed":
      return `The last run failed; see its output under Last run.${bg.resume_at ? ` It retries at ${new Date(bg.resume_at * 1000).toLocaleTimeString()}.` : ""}`;
    case "idle":
      return [tagging && "Every memory stored with LLM tagging on has its LLM tags.",
              organizing && "Organize has looked at every change."].filter(Boolean).join(" ");
  }
}

/** Which parts of the background work are on (ADR 0067, 0132). */
function parts(bg: BackgroundTagging): string {
  const tagging = bg.tagging ?? bg.enabled;
  const due = bg.organize?.due ?? 0;
  const organize = !bg.organize?.enabled ? "Organize off"
    : due ? `Organize: ${due} ${due === 1 ? "workspace" : "workspaces"} changed` : "Organize on";
  return `LLM tagging ${tagging ? "on" : bg.peer ? "by a paired device" : "off"} · ${organize}`;
}

type Filter = "all" | "failed";

/** One page of history at a time, newest first, behind a cursor per page
 * (GET /api/queue/history), with how many jobs each period holds. It reloads
 * whenever a job starts, ends or is queued, so a retried job's row says
 * where its new run is. Pages are cached (ADR 0152): a page seen before
 * shows at once, and the page shown stays while the next one loads. */
function useHistory(change: string) {
  const [filter, setFilter] = useState<Filter>("all");
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [page, setPage] = useState(0);
  // Computed once per load, so a page and its group counts agree.
  const starts = useMemo(() => periodStarts(), [change, page, filter]); // eslint-disable-line react-hooks/exhaustive-deps

  const before = cursors[page];
  // A new day starts new periods, so its counts are another answer.
  const cached = useCached(`queue-history:${JSON.stringify([filter, before, starts[0]])}`, () =>
    getQueueHistory({ limit: PAGE_SIZE, before, bounds: starts, ...(filter === "failed" ? { status: "failed" } : {}) })
      .then((h): QueueHistory => ({ jobs: h?.jobs ?? [], next: h?.next ?? null, total: h?.total ?? 0, counts: h?.counts ?? null })),
    { revalidateOnJobs: false });
  const [held, setHeld] = useState<QueueHistory | null>(null);
  if (cached.data && cached.data !== held) setHeld(cached.data);
  const next = cached.data?.next;
  if (next && cursors[page + 1] !== next) setCursors([...cursors.slice(0, page + 1), next]);

  const lastChange = useRef(change);
  useEffect(() => {
    if (lastChange.current === change) return;
    lastChange.current = change;
    void revalidateCached("queue-history:");
  }, [change]);

  const { refresh } = cached;
  return {
    history: cached.data ?? held,
    error: cached.error ? cached.error.message : "",
    starts, page, filter,
    loading: !cached.data,
    retry: () => void refresh(),
    setPage,
    setFilter: (f: Filter) => { setFilter(f); setPage(0); setCursors([null]); },
  };
}

export default function QueuePage() {
  const data = useQueue();
  const [openId, setOpenId] = useState<string | null>(null);
  const [fallback, setFallback] = useState<QueueJob | null>(null);
  const running = data ? data.active ?? (data.background?.state === "running" ? data.background.job : null) : null;
  // Whatever starts, ends or joins the queue reloads the history.
  const change = data ? [running?.id, data.recent[0]?.id, ...data.queued.map((j) => j.id)].join(",") : "";
  const history = useHistory(change);

  function open(job: QueueJob) {
    setOpenId(job.id);
    setFallback(job);
  }

  /** After Retry in the panel, it follows the new job. */
  function follow(original: QueueJob, jobId: string) {
    setOpenId(jobId);
    setFallback({ ...original, id: jobId, status: "queued", exit_code: null, error: null, pid: null, created_at: Date.now() / 1000,
      started_at: null, ended_at: null, twin: null });
  }

  if (!data) return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Queue" description={DESCRIPTION} />
      <div className="space-y-4 px-4 sm:px-8" aria-busy="true">
        <Skeleton className="h-28 w-full rounded-xl" />
        <Skeleton className="h-40 w-full rounded-xl" />
      </div>
    </div>
  );

  const { queued, background } = data;
  // The panel follows the job as the queue moves it along.
  const shown = openId
    ? [running, ...queued, ...(history.history?.jobs ?? []), ...data.recent].find((j) => j?.id === openId) ?? fallback
    : null;

  return (
    <div className="mx-auto max-w-5xl pb-8">
      <PageHeader title="Queue" description={DESCRIPTION} actions={
        <Button variant="outline" size="sm" nativeButton={false} render={<Link href="/commands/" />}>
          <Play />
          Run a command
        </Button>
      } />
      <div className="space-y-8 px-4 sm:px-8">
        {running || queued.length ? (
          <>
            <Section title="Running">
              {running ? (
                <RunningJob job={running}><LiveOutput key={running.id} job={running} onOpen={() => open(running)} /></RunningJob>
              ) : <Nothing>Nothing is running. The next job starts in a moment.</Nothing>}
            </Section>
            {queued.length > 0 && (
              <Section title="Up next" count={queued.length}>
                <Card className="gap-0 py-0">
                  <ol className="divide-y divide-border">
                    {queued.map((job, i) => (
                      <li key={job.id} className="flex items-center gap-1 pr-2">
                        <button type="button" onClick={() => open(job)} aria-label={`${jobTitle(job)}, position ${i + 1}. Show details`}
                          className="flex min-w-0 flex-1 items-center gap-3 px-4 py-2.5 text-left text-sm outline-none hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset">
                          <span className="grid size-6 shrink-0 place-items-center rounded-full bg-muted text-xs text-muted-foreground tabular-nums">{i + 1}</span>
                          <JobName job={job} className="min-w-0 flex-1" />
                          <Merged job={job} className="hidden sm:inline-flex" />
                          <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">asked {ago(job.created_at)}</span>
                        </button>
                        <CancelButton job={job} label="Remove from queue" className="text-muted-foreground hover:text-destructive" iconClassName="size-4" />
                      </li>
                    ))}
                  </ol>
                </Card>
              </Section>
            )}
          </>
        ) : (
          <Section title="Now">
            <Nothing icon={<CircleDashed className="size-4 shrink-0" aria-hidden />}>
              The queue is idle. Jobs started from Commands, Add to memory or the CLI wait here and run one at a time.
            </Nothing>
          </Section>
        )}

        {background && <Background bg={background} onOpen={open} />}

        <History {...history} onOpen={open} />
      </div>

      <JobSheet job={shown} onClose={() => setOpenId(null)} onRetried={follow} />
    </div>
  );
}

function Section({ title, count, actions, children }: { title: string; count?: number; actions?: React.ReactNode; children: React.ReactNode }) {
  const id = `queue-${title.toLowerCase().replace(/\s+/g, "-")}`;
  return (
    <section className="space-y-2" aria-labelledby={id}>
      <div className="flex min-h-7 flex-wrap items-center justify-between gap-2">
        <h2 id={id} className="flex items-baseline gap-2 text-sm font-semibold">
          {title}
          {count !== undefined && count > 0 && <span className="font-normal text-muted-foreground tabular-nums">{count.toLocaleString()}</span>}
        </h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

function Nothing({ icon, children }: { icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <p className="flex items-center gap-3 rounded-xl border border-dashed border-border px-4 py-3 text-sm text-muted-foreground">
      {icon}{children}
    </p>
  );
}

/** Identical submissions joined this job (ADR 0068). */
function Merged({ job, className }: { job: QueueJob; className?: string }) {
  const merged = Number(job.body?._merged ?? 0);
  if (merged <= 0) return null;
  return (
    <Hint label="Identical requests joined this job; it runs once for all of them">
      <Badge variant="secondary" className={cn("shrink-0 text-[10px]", className)}>{merged + 1} requests</Badge>
    </Hint>
  );
}

/** Finished jobs grouped by when they ended, a page at a time. */
function History({ history, error, loading, starts, page, filter, setPage, setFilter, retry, onOpen }: ReturnType<typeof useHistory> & {
  onOpen: (job: QueueJob) => void;
}) {
  const jobs = history?.jobs ?? [];
  const groups = groupByPeriod(jobs, (job) => job.ended_at ?? 0, starts);
  const first = page * PAGE_SIZE;
  return (
    <Section title="History" count={history?.total} actions={(filter !== "all" || !!history?.total) &&
      <ToggleGroup aria-label="Show" size="sm" spacing={0} variant="outline" value={[filter]} onValueChange={(v: string[]) => v[0] && setFilter(v[0] as Filter)}>
        <ToggleGroupItem value="all">All</ToggleGroupItem>
        <ToggleGroupItem value="failed">Failed</ToggleGroupItem>
      </ToggleGroup>
    }>
      {error && (
        <Alert variant="destructive">
          <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
            <span>History could not be loaded: {error}</span>
            <Button variant="outline" size="sm" onClick={retry}><RotateCw />Try again</Button>
          </AlertDescription>
        </Alert>
      )}
      {!history && !error ? (
        <Skeleton className="h-48 w-full rounded-xl" />
      ) : jobs.length === 0 && !error ? (
        <Nothing>{filter === "failed" ? "No job has failed." : "No job has finished yet."}</Nothing>
      ) : jobs.length > 0 && (
        <Card className={cn("gap-0 py-0 transition-opacity", loading && "opacity-60")} aria-busy={loading}>
          {groups.map((group) => (
            <div key={`${group.period}-${group.items[0].id}`} role="group" aria-label={group.period}>
              <h3 className="flex items-baseline gap-2 border-b border-border bg-muted/40 px-4 py-1.5 text-xs font-medium text-muted-foreground">
                {group.period}
                <span className="font-normal tabular-nums">{(history?.counts?.[group.index] ?? group.items.length).toLocaleString()}</span>
              </h3>
              <ul className="divide-y divide-border border-b border-border">
                {group.items.map((job) => <HistoryRow key={job.id} job={job} period={group.period} onOpen={onOpen} />)}
              </ul>
            </div>
          ))}
          <nav aria-label="History pages" className="-mt-px flex flex-wrap items-center gap-2 border-t border-border px-4 py-2 text-xs text-muted-foreground">
            <span className="tabular-nums">
              {(first + 1).toLocaleString()}–{(first + jobs.length).toLocaleString()}
              {history ? ` of ${history.total.toLocaleString()}` : ""}
            </span>
            {loading && <Spinner className="size-3" />}
            <span className="flex-1" />
            <Button variant="ghost" size="sm" disabled={page === 0 || loading} onClick={() => setPage(page - 1)}>
              <ChevronLeft aria-hidden />Newer
            </Button>
            <Button variant="ghost" size="sm" disabled={!history?.next || loading} onClick={() => setPage(page + 1)}>
              Older<ChevronRight aria-hidden />
            </Button>
          </nav>
        </Card>
      )}
    </Section>
  );
}

function HistoryRow({ job, period, onOpen }: { job: QueueJob; period: string; onOpen: (job: QueueJob) => void }) {
  const took = runTime(job);
  const why = job.status === "failed" ? reason(job) : "";
  const full = job.ended_at ? new Date(job.ended_at * 1000).toLocaleString() : "";
  return (
    <li className="flex items-center gap-2 pr-3">
      <button type="button" onClick={() => onOpen(job)} aria-label={`${jobTitle(job)}, ${result(job)}${full ? `, ended ${full}` : ""}. Show details`}
        className="flex min-w-0 flex-1 items-center gap-3 py-2.5 pl-4 text-left text-sm outline-none hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset">
        <StatusIcon status={job.status} className="self-start mt-0.5" />
        <span className="min-w-0 flex-1">
          <JobName job={job} className="block" />
          {why && <span className="block truncate font-mono text-xs text-destructive" title={why}>{why}</span>}
        </span>
        <Merged job={job} className="hidden sm:inline-flex" />
        <span className={cn("hidden w-24 shrink-0 text-xs sm:inline", job.status === "failed" ? "text-destructive" : "text-muted-foreground")}>{result(job)}</span>
        <span className="hidden w-14 shrink-0 text-right text-xs text-muted-foreground tabular-nums md:inline">{took !== null ? formatDuration(took) : ""}</span>
        <span className="shrink-0 text-right text-xs whitespace-nowrap text-muted-foreground tabular-nums sm:w-24" title={full}>{job.ended_at ? endedAt(job.ended_at, period) : ""}</span>
      </button>
      <span className="flex shrink-0 justify-end sm:w-24">
        {job.twin ? <Twin twin={job.twin} />
          : job.status === "failed" ? <RetryButton job={job} size="xs" compact />
          : <ChevronRight className="hidden size-4 text-muted-foreground sm:block" aria-hidden />}
      </span>
    </li>
  );
}

/** An identical job is waiting or running: a retry would join or follow it. */
function Twin({ twin }: { twin: NonNullable<QueueJob["twin"]> }) {
  const text = twin.status === "running" ? "Running again" : "Queued again";
  // On phones the icon alone; the hint and the accessible name say it.
  return (
    <Hint label={twin.status === "running" ? "An identical job is running now" : "An identical job waits in the queue"}>
      <Badge variant="secondary" className="gap-1 text-[10px]" tabIndex={0}>
        {twin.status === "running" ? <Spinner className="size-3" /> : <Clock className="size-3" aria-hidden />}<span className="max-sm:sr-only">{text}</span>
      </Badge>
    </Hint>
  );
}

/** Background work: LLM tagging (ADR 0067), which learn and ingest leave
 * LLM tags to, then Organize when it runs in the background (ADR 0132). It
 * gives way to every other job. A running pass shows under Running. */
function Background({ bg, onOpen }: { bg: BackgroundTagging; onOpen: (job: QueueJob) => void }) {
  const [resuming, setResuming] = useState(false);

  async function resume() {
    setResuming(true);
    try {
      await resumeBackgroundTagging();
    } catch (e) {
      toast.error("Could not resume background work", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setResuming(false);
    }
  }

  return (
    <section role="region" aria-labelledby="background-work-title" className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl bg-card px-4 py-3 ring-1 ring-foreground/10">
      <Sparkles className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span id="background-work-title" className="font-medium">Background work</span>
          <Badge variant={bg.state === "failed" ? "destructive" : "secondary"} className="text-[10px]">{STATE_LABEL[bg.state]}</Badge>
          {bg.pending != null && bg.pending > 0 && <span className="text-xs text-muted-foreground tabular-nums">{memories(bg.pending)} waiting</span>}
        </p>
        <p className="text-xs text-muted-foreground">{explain(bg)}</p>
        <p className="text-xs text-muted-foreground">{parts(bg)}</p>
      </div>
      {(bg.state === "paused" || bg.state === "failed") && (
        <Button variant="outline" size="sm" onClick={resume} disabled={resuming}>
          {resuming ? <Spinner /> : <RotateCw />} Resume now
        </Button>
      )}
      {bg.job && bg.state !== "running" && (
        <Button variant="ghost" size="sm" onClick={() => onOpen(bg.job!)}>Last run</Button>
      )}
    </section>
  );
}

/** The running job's newest line, and its whole output on request. */
function LiveOutput({ job, onOpen }: { job: QueueJob; onOpen: () => void }) {
  const lines = useJobOutput(job.id);
  const [show, setShow] = useState(false);
  let last = "";
  for (let i = lines.length - 1; i >= 0 && !last; i--) {
    last = lines[i].split("\n").map((line) => line.trim()).filter(Boolean).pop() ?? "";
  }
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <p className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground" title={last}>{last || "Waiting for output…"}</p>
        <Button variant="ghost" size="xs" aria-expanded={show} onClick={() => setShow((v) => !v)}>
          {show ? "Hide output" : "Show output"}
        </Button>
        <Button variant="ghost" size="xs" onClick={onOpen}>Details</Button>
      </div>
      {show && <Output lines={lines} live className="h-72 rounded-lg border border-border bg-muted/40" />}
    </div>
  );
}

/** A job's output so far, then as it arrives. Callers are keyed by job. */
function useJobOutput(jobId: string) {
  const [lines, setLines] = useState<string[]>([]);
  useEffect(() => {
    const ctrl = tailJobStream(jobId, (ev) => {
      if (ev.type === "output") setLines((prev) => [...prev, ev.text as string]);
    });
    return () => ctrl.abort();
  }, [jobId]);
  return lines;
}

function Output({ lines, live, className }: { lines: string[]; live?: boolean; className?: string }) {
  const scroller = useFollowOutput(lines);
  return (
    <ScrollArea className={className} viewportRef={scroller}>
      <pre className="p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words">
        {lines.join("") || <span className="text-muted-foreground">{live ? "Waiting for output…" : "No output."}</span>}
      </pre>
    </ScrollArea>
  );
}

/** Everything about one job: what it was asked to do, when it ran, how it
 * ended, and its output. */
function JobSheet({ job, onClose, onRetried }: { job: QueueJob | null; onClose: () => void; onRetried: (job: QueueJob, jobId: string) => void }) {
  return (
    <Sheet open={!!job} onOpenChange={(open) => { if (!open) onClose(); }}>
      <SheetContent className="w-full gap-0 sm:max-w-xl">
        {job && <JobDetails key={job.id} job={job} onRetried={(id) => onRetried(job, id)} />}
      </SheetContent>
    </Sheet>
  );
}

function JobDetails({ job, onRetried }: { job: QueueJob; onRetried: (jobId: string) => void }) {
  const lines = useJobOutput(job.id);
  const [copied, setCopied] = useState(false);
  const took = runTime(job);
  const finished = job.status === "done" || job.status === "failed" || job.status === "cancelled";
  const time = (ts: number | null) => (ts ? new Date(ts * 1000).toLocaleString() : "—");
  const error = job.error?.replace(/cancelled by user;\s*/g, "").trim();

  async function copy() {
    try {
      await navigator.clipboard.writeText(lines.join(""));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Could not copy the output");
    }
  }

  return (
    <>
      <SheetHeader className="gap-1 border-b border-border pr-12">
        <SheetTitle className="flex items-center gap-2"><StatusIcon status={job.status} />{jobTitle(job)}</SheetTitle>
        <SheetDescription>
          {result(job)}
          {took !== null && ` · ${formatDuration(took)}${job.status === "running" ? " so far" : ""}`}
          {job.ended_at && ` · ended ${ago(job.ended_at)}`}
        </SheetDescription>
      </SheetHeader>
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
        <div className="flex flex-wrap items-center gap-2">
          {finished && job.command !== "tag-backlog" && (
            <RetryButton job={job} variant={job.status === "failed" ? "default" : "outline"} onQueued={onRetried} />
          )}
          {(job.status === "queued" || job.status === "running") && (
            <CancelButton job={job} label={job.status === "running" ? "Cancel job" : "Remove from queue"} className="" iconClassName="size-4">
              {job.status === "running" ? "Cancel job" : "Remove from queue"}
            </CancelButton>
          )}
          {job.twin && (
            <p className="text-xs text-muted-foreground">
              An identical job is {job.twin.status === "running" ? "running now" : "already waiting"}, so it is not queued again.
            </p>
          )}
        </div>
        {error && job.status !== "cancelled" && (
          <div role="alert" className="max-h-40 overflow-y-auto rounded-lg bg-destructive/5 px-3 py-2 font-mono text-xs whitespace-pre-wrap break-words text-destructive">{error}</div>
        )}
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
          {(job.params ?? []).map((p) => (
            <div key={p.label} className="contents">
              <dt className="text-muted-foreground">{p.label}</dt>
              <dd className="font-mono text-xs leading-5 break-all">{p.value || "Yes"}</dd>
            </div>
          ))}
          {job.workspace && <><dt className="text-muted-foreground">Workspace</dt><dd>{job.workspace}</dd></>}
          <dt className="text-muted-foreground">Asked</dt><dd className="tabular-nums">{time(job.created_at)}</dd>
          <dt className="text-muted-foreground">Started</dt><dd className="tabular-nums">{time(job.started_at)}</dd>
          <dt className="text-muted-foreground">Ended</dt><dd className="tabular-nums">{time(job.ended_at)}</dd>
          {job.pid != null && <><dt className="text-muted-foreground">Process</dt><dd className="tabular-nums">{job.pid}</dd></>}
          <dt className="text-muted-foreground">Command</dt><dd className="font-mono text-xs leading-5 break-all">imprint {job.command}</dd>
        </dl>
        <div className="flex min-h-0 flex-1 flex-col gap-1.5">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-medium text-muted-foreground">Output</h3>
            <Hint label={copied ? "Copied" : "Copy output"}>
              <Button variant="ghost" size="icon-sm" aria-label="Copy output" disabled={!lines.length} onClick={copy}>
                {copied ? <Check /> : <Copy />}
              </Button>
            </Hint>
          </div>
          <Output lines={lines} live={job.status === "running"} className="min-h-48 flex-1 rounded-lg border border-border bg-muted/40" />
        </div>
      </div>
    </>
  );
}
