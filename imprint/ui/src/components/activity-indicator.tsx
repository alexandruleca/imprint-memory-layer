"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Spinner } from "@/components/loaders";
import { Popover, PopoverContent, PopoverDescription, PopoverHeader, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import { Progress } from "@/components/ui/progress";
import { describeJob, describeJobPlainly, plainJob, useQueue } from "@/lib/queue";
import { useUiMode } from "@/lib/ui-mode";

const PILL = "app-no-drag inline-flex min-w-0 max-w-72 items-center gap-2 rounded-full border border-border px-3 py-1.5 text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50";

/** The running job and how far it is, above every page (ADR 0089). The
 * advanced dashboard's opens the queue; the simple one's says, in plain
 * words, what Imprint is doing and what waits (ADR 0182). */
export function ActivityIndicator() {
  const queue = useQueue();
  const { simple } = useUiMode();
  const job = queue?.active ?? (queue?.background?.state === "running" ? queue.background.job : null);
  if (!job) return null;
  if (!simple) {
    const text = describeJob(job);
    return <Link href="/queue/" data-tour="activity" className={PILL} aria-label={`${text}. Open the queue`}>
      <Spinner className="size-3"/><span className="truncate">{text}</span>
    </Link>;
  }
  const waiting = queue?.queued ?? [];
  const percent = job.total ? Math.floor((100 * Math.min(job.processed ?? 0, job.total)) / job.total) : null;
  return (
    <Popover>
      <PopoverTrigger data-tour="activity" className={PILL} aria-label={`${plainJob(job)}. Show what Imprint is doing`}>
        <Spinner className="size-3"/><span className="truncate">{plainJob(job)}{percent !== null ? ` · ${percent}%` : ""}</span>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 gap-3 p-3">
        <PopoverHeader>
          <PopoverTitle>Imprint is working</PopoverTitle>
          <PopoverDescription>It does one thing at a time. You can keep using everything meanwhile.</PopoverDescription>
        </PopoverHeader>
        <div className="space-y-1.5">
          <p className="text-sm">{describeJobPlainly(job)}</p>
          {percent !== null && <Progress value={percent} aria-label={`${plainJob(job)}: ${percent}%`} />}
        </div>
        {waiting.length > 0 && (
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">Waiting its turn</p>
            <ul className="space-y-0.5 text-sm">
              {waiting.slice(0, 4).map((next) => <li key={next.id} className="truncate">{plainJob(next)}</li>)}
              {waiting.length > 4 && <li className="text-xs text-muted-foreground">and {waiting.length - 4} more</li>}
            </ul>
          </div>
        )}
        <Link href="/queue/" className="inline-flex items-center gap-1 self-start rounded-sm text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
          Details and cancel<ArrowRight className="size-3" aria-hidden />
        </Link>
      </PopoverContent>
    </Popover>
  );
}
