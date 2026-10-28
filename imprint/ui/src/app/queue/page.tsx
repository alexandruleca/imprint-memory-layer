"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Ban, CheckCircle2, ChevronRight, Clock, PauseCircle, RotateCw, Sparkles, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Hint } from "@/components/hint";
import { Spinner } from "@/components/loaders";
import { PageHeader } from "@/components/split-page";
import { CancelButton, JobName, RunningJob } from "@/components/ingestion-progress";
import { resumeBackgroundTagging, tailJobStream } from "@/lib/api";
import { ago, formatDuration, runTime, useQueue } from "@/lib/queue";
import { useFollowOutput } from "@/lib/use-follow-output";
import { cn } from "@/lib/utils";
import type { BackgroundTagging, BackgroundTaggingState, QueueJob, QueueJobStatus } from "@/lib/types";

const DESCRIPTION = "Imprint runs one job at a time, in the order they were asked for. Background tagging fills the time in between and steps aside for anything else.";

const STATUS: Record<QueueJobStatus, { label: string; icon: React.ElementType; className: string }> = {
  running: { label: "Running", icon: Spinner, className: "text-foreground" },
  queued: { label: "Waiting", icon: Clock, className: "text-muted-foreground" },
  done: { label: "Done", icon: CheckCircle2, className: "text-emerald-600" },
  failed: { label: "Failed", icon: XCircle, className: "text-destructive" },
  cancelled: { label: "Cancelled", icon: Ban, className: "text-muted-foreground" },
  yielded: { label: "Stepped aside", icon: PauseCircle, className: "text-muted-foreground" },
};

function result(job: QueueJob) {
  const status = STATUS[job.status];
  return job.status === "failed" && job.exit_code != null ? `${status.label} · exit ${job.exit_code}` : status.label;
}

function StatusIcon({ status }: { status: QueueJobStatus }) {
  const { icon: Icon, className } = STATUS[status];
  return <Icon className={cn("size-4 shrink-0", className)} aria-hidden />;
}

const memories = (n: number) => `${n.toLocaleString()} ${n === 1 ? "memory" : "memories"}`;

const STATE_LABEL: Record<BackgroundTaggingState, string> = {
  off: "Off", running: "Running", yielded: "Stepped aside", waiting: "Waiting", paused: "Paused", failed: "Failed", idle: "Up to date",
};

function explain(bg: BackgroundTagging): string {
  const waiting = bg.pending != null ? memories(bg.pending) : "Memories";
  switch (bg.state) {
    case "off":
      return bg.pending
        ? `${waiting} wait for LLM tagging. Turn on LLM tagging (tagger.llm) in Settings to tag them here.`
        : "LLM tagging (tagger.llm) is off. Memories keep their keyword and zero-shot tags.";
    case "running":
      return "Tagging the newest memories first. It steps aside between memories whenever another job is waiting.";
    case "yielded":
      return "Stepped aside for another job; it resumes when the queue is idle.";
    case "waiting":
      return "Starts when no other job runs or waits.";
    case "paused":
      return `Cancelled with ${waiting.toLowerCase()} waiting. It resumes when more memories wait.`;
    case "failed":
      return `The last run failed; see its output in History.${bg.resume_at ? ` It retries at ${new Date(bg.resume_at * 1000).toLocaleTimeString()}.` : ""}`;
    case "idle":
      return "Every memory stored with LLM tagging on has its LLM tags.";
  }
}

export default function QueuePage() {
  const data = useQueue();
  const [open, setOpen] = useState<QueueJob | null>(null);

  if (!data) return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Queue" description={DESCRIPTION} />
      <div className="space-y-4 px-4 sm:px-8" aria-busy="true">
        <Skeleton className="h-28 w-full rounded-xl" />
        <Skeleton className="h-40 w-full rounded-xl" />
      </div>
    </div>
  );

  const { active, queued, recent, background } = data;
  const running = active ?? (background?.state === "running" ? background.job : null);
  // The sheet follows the job as the queue moves it along.
  const shown = open && ([running, ...queued, ...recent].find((j) => j?.id === open.id) ?? open);

  return (
    <div className="mx-auto max-w-5xl pb-8">
      <PageHeader title="Queue" description={DESCRIPTION} />
      <div className="space-y-8 px-4 sm:px-8">
        <Section title="Running">
          {running ? (
            <RunningJob job={running}><LiveOutput key={running.id} job={running} /></RunningJob>
          ) : <Nothing>Nothing is running.</Nothing>}
        </Section>

        <Section title="Up next" count={queued.length}>
          {queued.length ? (
            <Card size="sm" className="gap-0 py-0">
              <ol className="divide-y divide-border">
                {queued.map((job, i) => (
                  <li key={job.id} className="flex items-center gap-1 pr-2">
                    <button type="button" onClick={() => setOpen(job)} aria-label={`${job.label ?? job.command}, position ${i + 1}. Show details`}
                      className="flex min-w-0 flex-1 items-center gap-3 px-4 py-2.5 text-left text-sm outline-none hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset">
                      <span className="w-5 shrink-0 text-xs text-muted-foreground tabular-nums">{i + 1}</span>
                      <JobName job={job} className="min-w-0 flex-1" />
                      <Merged job={job} />
                      <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">asked {ago(job.created_at)}</span>
                    </button>
                    <CancelButton job={job} label="Remove from queue" className="text-muted-foreground hover:text-destructive" iconClassName="size-4" />
                  </li>
                ))}
              </ol>
            </Card>
          ) : <Nothing>Nothing is waiting.</Nothing>}
        </Section>

        {background && <Background bg={background} onOpen={setOpen} />}

        <Section title="History">
          {recent.length ? (
            <Card size="sm" className="gap-0 py-0">
              <ul className="divide-y divide-border">
                {recent.map((job) => {
                  const took = runTime(job);
                  return (
                    <li key={job.id}>
                      <button type="button" onClick={() => setOpen(job)} aria-label={`${job.label ?? job.command}, ${result(job)}. Show details`}
                        className="flex w-full min-w-0 items-center gap-3 px-4 py-2.5 text-left text-sm outline-none hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset">
                        <StatusIcon status={job.status} />
                        <JobName job={job} className="min-w-0 flex-1" />
                        <Merged job={job} />
                        <span className={cn("hidden shrink-0 text-xs sm:inline", job.status === "failed" ? "text-destructive" : "text-muted-foreground")}>{result(job)}</span>
                        <span className="hidden w-16 shrink-0 text-right text-xs text-muted-foreground tabular-nums md:inline">{took !== null ? formatDuration(took) : ""}</span>
                        <span className="w-20 shrink-0 text-right text-xs text-muted-foreground">{job.ended_at ? ago(job.ended_at) : ""}</span>
                        <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                      </button>
                    </li>
                  );
                })}
              </ul>
            </Card>
          ) : <Nothing>No job has finished yet.</Nothing>}
        </Section>
      </div>

      <JobSheet job={shown} onClose={() => setOpen(null)} />
    </div>
  );
}

function Section({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h2 className="flex items-baseline gap-2 text-sm font-semibold">
        {title}
        {count !== undefined && count > 0 && <span className="font-normal text-muted-foreground tabular-nums">{count}</span>}
      </h2>
      {children}
    </section>
  );
}

function Nothing({ children }: { children: React.ReactNode }) {
  return <p className="rounded-xl border border-dashed border-border px-4 py-3 text-sm text-muted-foreground">{children}</p>;
}

/** Identical submissions joined this job (ADR 0068). */
function Merged({ job }: { job: QueueJob }) {
  const merged = Number(job.body?._merged ?? 0);
  if (merged <= 0) return null;
  return (
    <Hint label="Identical requests joined this job; it runs once for all of them">
      <Badge variant="secondary" className="shrink-0 text-[10px]">{merged + 1} requests</Badge>
    </Hint>
  );
}

/** Background LLM tagging (ADR 0067): learn and ingest leave LLM tags to it,
 * and it gives way to every other job. A running pass shows under Running. */
function Background({ bg, onOpen }: { bg: BackgroundTagging; onOpen: (job: QueueJob) => void }) {
  const [resuming, setResuming] = useState(false);

  async function resume() {
    setResuming(true);
    try {
      await resumeBackgroundTagging();
    } catch (e) {
      toast.error("Could not resume background tagging", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setResuming(false);
    }
  }

  return (
    <section role="region" aria-labelledby="background-tagging-title" className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl bg-card px-4 py-3 ring-1 ring-foreground/10">
      <Sparkles className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span id="background-tagging-title" className="font-medium">Background tagging</span>
          <Badge variant={bg.state === "failed" ? "destructive" : "secondary"} className="text-[10px]">{STATE_LABEL[bg.state]}</Badge>
          {bg.pending != null && bg.pending > 0 && <span className="text-xs text-muted-foreground tabular-nums">{memories(bg.pending)} waiting</span>}
        </p>
        <p className="text-xs text-muted-foreground">{explain(bg)}</p>
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
function LiveOutput({ job }: { job: QueueJob }) {
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
function JobSheet({ job, onClose }: { job: QueueJob | null; onClose: () => void }) {
  return (
    <Sheet open={!!job} onOpenChange={(open) => { if (!open) onClose(); }}>
      <SheetContent className="w-full gap-0 sm:max-w-xl">
        {job && <JobDetails key={job.id} job={job} />}
      </SheetContent>
    </Sheet>
  );
}

function JobDetails({ job }: { job: QueueJob }) {
  const lines = useJobOutput(job.id);
  const took = runTime(job);
  const time = (ts: number | null) => (ts ? new Date(ts * 1000).toLocaleString() : "—");
  return (
    <>
      <SheetHeader className="gap-1 border-b border-border pr-12">
        <SheetTitle className="flex items-center gap-2"><StatusIcon status={job.status} />{job.label ?? job.command}</SheetTitle>
        <SheetDescription>{result(job)}{took !== null && ` · ${formatDuration(took)}`}</SheetDescription>
      </SheetHeader>
      <div className="flex min-h-0 flex-1 flex-col gap-4 p-4">
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
          <dt className="text-muted-foreground">Command</dt><dd className="font-mono text-xs leading-5">imprint {job.command}</dd>
        </dl>
        {job.error && <p role="alert" className="rounded-lg bg-destructive/5 px-3 py-2 font-mono text-xs text-destructive">{job.error}</p>}
        {(job.status === "queued" || job.status === "running") && (
          <CancelButton job={job} label={job.status === "running" ? "Cancel job" : "Remove from queue"} className="self-start" iconClassName="size-4">
            {job.status === "running" ? "Cancel job" : "Remove from queue"}
          </CancelButton>
        )}
        <div className="flex min-h-0 flex-1 flex-col gap-1.5">
          <h3 className="text-xs font-medium text-muted-foreground">Output</h3>
          <Output lines={lines} live={job.status === "running"} className="min-h-40 flex-1 rounded-lg border border-border bg-muted/40" />
        </div>
      </div>
    </>
  );
}
