"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Hint } from "@/components/hint";
import { cancelJob } from "@/lib/api";
import { useQueue } from "@/lib/queue";
import { Spinner } from "@/components/loaders";
import { HardDriveDownload, Sparkles, X, ListOrdered } from "lucide-react";
import type { QueueJob } from "@/lib/types";

function formatEta(seconds: number | null | undefined): string {
  if (seconds == null) return "calculating...";
  if (seconds < 60) return `~${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `~${m}m ${s}s`;
}

export function IngestionProgress() {
  const data = useQueue();

  if (!data) return null;
  const { active, queued } = data;
  if (!active && queued.length === 0) return null;

  return (
    <div className="space-y-2">
      {active && <ActiveCard job={active} />}
      {queued.length > 0 && <QueuedList jobs={queued} />}
    </div>
  );
}

/** Cancels a running job or removes a queued one. Success shows as the job
 * leaving its list (and a job toast once it ends); a refusal is reported. */
export function CancelButton({ job, label, className, iconClassName }: {
  job: QueueJob; label: string; className: string; iconClassName: string;
}) {
  const [cancelling, setCancelling] = useState(false);

  async function cancel() {
    setCancelling(true);
    try {
      const result = await cancelJob(job.id);
      if (!result.ok) throw new Error(result.error || "The job could not be cancelled");
    } catch (e) {
      setCancelling(false);
      toast.error(`Could not cancel imprint ${job.command}`, { description: e instanceof Error ? e.message : String(e) });
    }
  }

  return (
    <Hint label={label}>
      <Button variant="ghost" size="sm" className={className} onClick={cancel} disabled={cancelling} aria-label={label}>
        {cancelling ? <Spinner className={iconClassName} /> : <X className={iconClassName} />}
      </Button>
    </Hint>
  );
}

function ActiveCard({ job }: { job: QueueJob }) {
  const isTagging = job.phase === "llm_tagging";
  const label = isTagging ? "LLM tagging" : job.command;
  const Icon = isTagging ? Sparkles : HardDriveDownload;
  const unit = job.unit ?? (isTagging ? "memories" : "files");
  const pct = Math.min(job.percent ?? 0, 100);
  const processed = job.processed ?? 0;
  const total = job.total ?? 0;
  const stored = job.stored ?? 0;
  const skipped = job.skipped ?? 0;

  return (
    <Card>
      <CardContent className="py-4 px-5 space-y-3">
        <div className="flex items-center gap-3">
          <Spinner className="w-4 h-4 text-foreground" />
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <Badge
              variant="outline"
              className="text-[10px] uppercase shrink-0"
            >
              <Icon className="w-3 h-3 mr-1" />
              {label}
            </Badge>
            {(job.projects ?? []).map((p) => (
              <Badge
                key={p}
                variant="secondary"
                className="text-[10px] shrink-0"
              >
                {p}
              </Badge>
            ))}
          </div>
          <span className="text-xs text-muted-foreground tabular-nums shrink-0">
            {total > 0 ? `${processed}/${total} ${unit}` : job.phase}
          </span>
          <CancelButton job={job} label="Cancel running job" className="h-7 px-2 text-muted-foreground hover:text-red-600" iconClassName="w-3.5 h-3.5" />
        </div>

        <div className="space-y-1.5">
          <Progress value={pct} aria-label={`${label} progress`} className="[&_[data-slot=progress-track]]:h-2" />
          <div className="flex justify-between text-[11px] text-muted-foreground tabular-nums">
            <span>
              {Math.round(pct)}% &middot;{" "}
              {isTagging
                ? `${stored} tagged`
                : `${stored} stored, ${skipped} skipped`}
            </span>
            <span>{formatEta(job.eta_seconds)}</span>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function QueuedList({ jobs }: { jobs: QueueJob[] }) {
  return (
    <Card className="border-muted">
      <CardContent className="py-3 px-4 space-y-2">
        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <ListOrdered className="w-3.5 h-3.5" />
            {jobs.length} queued
          </span>
          <Link href="/queue" className="underline-offset-2 hover:underline">
            open queue
          </Link>
        </div>
        <ul className="space-y-1">
          {jobs.map((j, i) => (
            <li key={j.id} className="flex items-center gap-2 text-xs">
              <span className="tabular-nums text-muted-foreground w-5">
                {i + 1}.
              </span>
              <Badge variant="outline" className="text-[10px] shrink-0">
                {j.command}
              </Badge>
              <span className="truncate flex-1 text-muted-foreground">
                {summarizeBody(j.body)}
              </span>
              <CancelButton job={j} label="Remove from queue" className="h-6 px-1.5 text-muted-foreground hover:text-red-600" iconClassName="w-3 h-3" />
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function summarizeBody(body: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(body ?? {})) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    if (v === true) parts.push(`--${k}`);
    else parts.push(`${k}=${String(v)}`);
  }
  return parts.join(" ");
}
