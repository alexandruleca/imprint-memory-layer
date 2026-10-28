"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { ArrowRight, X } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Hint } from "@/components/hint";
import { cancelJob } from "@/lib/api";
import { formatDuration, phaseName, runTime, useQueue } from "@/lib/queue";
import { Spinner } from "@/components/loaders";
import type { QueueJob } from "@/lib/types";

/** The dashboard's view of the queue: the running job, and how many wait. */
export function IngestionProgress() {
  const data = useQueue();
  if (!data) return null;
  const running = data.active ?? (data.background?.state === "running" ? data.background.job : null);
  if (!running && data.queued.length === 0) return null;
  return (
    <div className="space-y-2">
      {running && <RunningJob job={running} />}
      {data.queued.length > 0 && (
        <Link href="/queue/" className="flex items-center gap-2 rounded-lg px-1 text-xs text-muted-foreground hover:text-foreground">
          {data.queued.length} more {data.queued.length === 1 ? "job waits" : "jobs wait"} in the queue
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
export function RunningJob({ job, children }: { job: QueueJob; children?: React.ReactNode }) {
  const pct = Math.min(job.percent ?? 0, 100);
  const total = job.total ?? 0;
  const unit = job.unit ?? (job.phase === "llm_tagging" ? "memories" : "files");
  const elapsed = runTime(job);
  const counts = [
    job.stored ? `${job.stored.toLocaleString()} ${job.phase === "llm_tagging" ? "tagged" : "stored"}` : "",
    job.skipped ? `${job.skipped.toLocaleString()} skipped` : "",
  ].filter(Boolean);
  return (
    <Card size="sm" aria-label={`Running: ${job.label ?? job.command}`} role="region">
      <CardContent className="space-y-3">
        <div className="flex items-start gap-3">
          <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-muted"><Spinner className="size-4" /></span>
          <JobName job={job} className="min-w-0 flex-1 text-sm" />
          <CancelButton job={job} label="Cancel job" className="text-muted-foreground hover:text-destructive" iconClassName="size-4">Cancel</CancelButton>
        </div>
        <div className="space-y-1.5">
          {total > 0
            ? <Progress value={pct} aria-label={`${job.label ?? job.command} progress`} className="[&_[data-slot=progress-track]]:h-2" />
            : <div className="h-2 overflow-hidden rounded-full bg-muted"><div className="h-full w-1/3 animate-pulse rounded-full bg-foreground/30" /></div>}
          <div className="flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs text-muted-foreground tabular-nums">
            <span>
              {job.phase && <span className="inline-block font-medium text-foreground first-letter:uppercase">{phaseName(job.phase)}</span>}
              {total > 0 && <>{job.phase && " · "}{(job.processed ?? 0).toLocaleString()} of {total.toLocaleString()} {unit} · {Math.round(pct)}%</>}
              {counts.length > 0 && <> · {counts.join(", ")}</>}
            </span>
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

/** Cancels a running job or removes a queued one. Success shows as the job
 * leaving its list (and a job toast once it ends); a refusal is reported.
 * With children it is a labelled button, otherwise an icon with a hint. */
export function CancelButton({ job, label, className, iconClassName, children }: {
  job: QueueJob; label: string; className: string; iconClassName: string; children?: React.ReactNode;
}) {
  const [cancelling, setCancelling] = useState(false);

  async function cancel() {
    setCancelling(true);
    try {
      const result = await cancelJob(job.id);
      if (!result.ok) throw new Error(result.error || "The job could not be cancelled");
    } catch (e) {
      setCancelling(false);
      toast.error(`Could not cancel ${job.label ?? `imprint ${job.command}`}`, { description: e instanceof Error ? e.message : String(e) });
    }
  }

  const icon = cancelling ? <Spinner className={iconClassName} /> : <X className={iconClassName} />;
  if (children) {
    return (
      <Button variant="outline" size="sm" className={className} onClick={cancel} disabled={cancelling} aria-label={label}>
        {icon}{children}
      </Button>
    );
  }
  return (
    <Hint label={label}>
      <Button variant="ghost" size="icon-sm" className={className} onClick={cancel} disabled={cancelling} aria-label={label}>
        {icon}
      </Button>
    </Hint>
  );
}
