"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { ArrowRight, RotateCw, X } from "lucide-react";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Hint } from "@/components/hint";
import { cancelJob, retryJob, type QueuedJob } from "@/lib/api";
import { formatDuration, phaseName, plainJob, runTime, useQueue } from "@/lib/queue";
import { useUiMode } from "@/lib/ui-mode";
import { Spinner } from "@/components/loaders";
import type { QueueJob } from "@/lib/types";

/** The dashboard's view of the queue: the running job, and how many wait. */
export function IngestionProgress() {
  const data = useQueue();
  const { simple } = useUiMode();
  if (!data) return null;
  const running = data.active ?? (data.background?.state === "running" ? data.background.job : null);
  if (!running && data.queued.length === 0) return null;
  return (
    <div className="space-y-2">
      {running && <RunningJob job={running} plain={simple} />}
      {data.queued.length > 0 && (
        <Link href="/queue/" className="flex items-center gap-2 rounded-lg px-1 text-xs text-muted-foreground hover:text-foreground">
          {simple
            ? `${data.queued.length} more ${data.queued.length === 1 ? "thing waits its turn" : "things wait their turn"}`
            : `${data.queued.length} more ${data.queued.length === 1 ? "job waits" : "jobs wait"} in the queue`}
          <ArrowRight className="size-3.5" aria-hidden />
        </Link>
      )}
    </div>
  );
}

/** A job's name and what it was asked to do: "Retag memories" over
 * "Include tagged chunks · workspace default". */
export function JobName({ job, className }: { job: QueueJob; className?: string }) {
  const details = [
    ...(job.params ?? []).map((p) => (p.value ? `${p.label}: ${p.value}` : p.label)),
    ...(job.workspace ? [`workspace ${job.workspace}`] : []),
  ];
  return (
    <span className={className}>
      <span className="block truncate font-medium">{job.label ?? job.command}</span>
      {details.length > 0 && <span className="block truncate text-xs text-muted-foreground" title={details.join(" · ")}>{details.join(" · ")}</span>}
    </span>
  );
}

/** The running job: what it is, how far it has got, how long it has run
 * and how long it has left, and a way to stop it. Every page shows a
 * running job this way. */
export function RunningJob({ job, children, plain }: {
  job: QueueJob; children?: React.ReactNode;
  /** The simple dashboard's words (ADR 0182): what it is doing and how far, without phases and counts. */
  plain?: boolean;
}) {
  const pct = Math.min(job.percent ?? 0, 100);
  const total = job.total ?? 0;
  const unit = job.unit ?? (job.phase === "llm_tagging" ? "memories" : "files");
  const elapsed = runTime(job);
  const counts = [
    job.stored ? `${job.stored.toLocaleString()} ${job.phase === "llm_tagging" ? "tagged" : "stored"}` : "",
    job.skipped ? `${job.skipped.toLocaleString()} skipped` : "",
  ].filter(Boolean);
  return (
    <Card size="sm" aria-label={`Running: ${plain ? plainJob(job) : job.label ?? job.command}`} role="region">
      <CardContent className="space-y-3">
        <div className="flex items-start gap-3">
          <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-muted"><Spinner className="size-4" /></span>
          {plain
            ? <span className="min-w-0 flex-1 truncate text-sm font-medium">{plainJob(job)}</span>
            : <JobName job={job} className="min-w-0 flex-1 text-sm" />}
          <CancelButton job={job} label="Cancel job" className="text-muted-foreground hover:text-destructive" iconClassName="size-4"><span className="max-sm:sr-only">Cancel</span></CancelButton>
        </div>
        <div className="space-y-1.5">
          {total > 0
            ? <Progress value={pct} aria-label={`${job.label ?? job.command} progress`} className="[&_[data-slot=progress-track]]:h-2" />
            : <div className="h-2 overflow-hidden rounded-full bg-muted"><div className="h-full w-1/3 animate-pulse rounded-full bg-foreground/30" /></div>}
          <div className="flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs text-muted-foreground tabular-nums">
            {plain ? (
              <span>{total > 0 ? <>{(job.processed ?? 0).toLocaleString()} of {total.toLocaleString()} {unit} · {Math.round(pct)}%</> : "Working…"}</span>
            ) : (
              <span>
                {job.phase && <span className="inline-block font-medium text-foreground first-letter:uppercase">{phaseName(job.phase)}</span>}
                {total > 0 && <>{job.phase && " · "}{(job.processed ?? 0).toLocaleString()} of {total.toLocaleString()} {unit} · {Math.round(pct)}%</>}
                {counts.length > 0 && <> · {counts.join(", ")}</>}
              </span>
            )}
            <span>
              {elapsed !== null && `${formatDuration(elapsed)} so far`}
              {job.eta_seconds != null && job.eta_seconds > 0 && ` · about ${formatDuration(job.eta_seconds)} left`}
            </span>
          </div>
        </div>
        {children}
      </CardContent>
    </Card>
  );
}

/** What a job is called where it is named in a sentence. */
export function jobTitle(job: Pick<QueueJob, "label" | "command">) {
  return job.label ?? `imprint ${job.command}`;
}

/** Cancels a running job or removes a queued one. Cancelling a running job
 * asks first, since it stops work in progress; removing a waiting one does
 * not. Success shows as the job leaving its list (and a job toast once it
 * ends); a refusal is reported. With children it is a labelled button,
 * otherwise an icon with a hint. */
export function CancelButton({ job, label, className, iconClassName, children }: {
  job: QueueJob; label: string; className: string; iconClassName: string; children?: React.ReactNode;
}) {
  const [cancelling, setCancelling] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const running = job.status === "running";

  async function cancel() {
    setConfirming(false);
    setCancelling(true);
    try {
      const result = await cancelJob(job.id);
      if (!result.ok) throw new Error(result.error || "The job could not be cancelled");
    } catch (e) {
      setCancelling(false);
      toast.error(`Could not cancel ${jobTitle(job)}`, { description: e instanceof Error ? e.message : String(e) });
    }
  }

  const onClick = running ? () => setConfirming(true) : cancel;
  const icon = cancelling ? <Spinner className={iconClassName} /> : <X className={iconClassName} />;
  const button = children
    ? (
      <Button variant="outline" size="sm" className={className} onClick={onClick} disabled={cancelling} aria-label={label}>
        {icon}{children}
      </Button>
    )
    : (
      <Hint label={label}>
        <Button variant="ghost" size="icon-sm" className={className} onClick={onClick} disabled={cancelling} aria-label={label}>
          {icon}
        </Button>
      </Hint>
    );
  if (!running) return button;
  const background = job.command === "tag-backlog";
  const elapsed = runTime(job);
  return (
    <>
      {button}
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Cancel {jobTitle(job)}?</AlertDialogTitle>
            <AlertDialogDescription>
              {background
                ? "Background work pauses until more memories wait, or until you choose Resume now on the Queue page."
                : `It stops now${elapsed !== null ? ` after ${formatDuration(elapsed)}` : ""}. You can run it again from the queue's History.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep running</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={cancel}>Cancel job</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

/** What retrying a job did, said in a toast: joined an identical waiting
 * job, started, or waits at a position. */
export function announceRetry(job: QueueJob, queued: QueuedJob) {
  const title = jobTitle(job);
  if (queued.merged) toast(`${title} is already waiting`, { description: "The identical job in the queue runs once for both requests." });
  else if (queued.position > 0) toast.success(`${title} queued again`, { description: `It waits at position ${queued.position}.` });
  else toast.success(`${title} queued again`, { description: "It starts now." });
}

/** Queue a finished job again, reporting the outcome or the refusal. */
export async function retry(job: QueueJob): Promise<QueuedJob | null> {
  try {
    const queued = await retryJob(job.id);
    announceRetry(job, queued);
    return queued;
  } catch (e) {
    toast.error(`Could not queue ${jobTitle(job)} again`, { description: e instanceof Error ? e.message : String(e) });
    return null;
  }
}

/** "Retry" for a failed job, "Run again" for one that was cancelled or
 * finished: the same command, arguments and workspace through the queue
 * (ADR 0068). `onQueued` gets the new (or joined) job's ID. */
export function RetryButton({ job, onQueued, size = "sm", variant = "outline", compact, className }: {
  job: QueueJob; onQueued?: (jobId: string) => void; size?: "xs" | "sm"; variant?: "outline" | "ghost" | "default";
  /** Only the icon on phones. */
  compact?: boolean; className?: string;
}) {
  const [busy, setBusy] = useState(false);
  const label = job.status === "failed" ? "Retry" : "Run again";
  async function click() {
    setBusy(true);
    const queued = await retry(job);
    setBusy(false);
    if (queued) onQueued?.(queued.job_id);
  }
  return (
    <Button variant={variant} size={size} className={className} onClick={click} disabled={busy || !!job.twin}
      aria-label={`${label} ${jobTitle(job)}`}>
      {busy ? <Spinner /> : <RotateCw />}<span className={compact ? "max-sm:sr-only" : undefined}>{label}</span>
    </Button>
  );
}
