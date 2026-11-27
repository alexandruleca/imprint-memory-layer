"use client";

import { ChartNoAxesColumn } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { getProgress, type ProgressEntry, type TaskProgress } from "@/lib/api";
import { useCached } from "@/lib/use-cached";
import { cn } from "@/lib/utils";

/** How long is left, in words: "about 3 days left", or why there is no estimate. */
export function estimateText(daysLeft: number | null): string {
  if (daysLeft === null) return "Not enough finished work to estimate yet";
  if (daysLeft === 0) return "Finished";
  if (daysLeft < 1) return "Less than a day left";
  const days = Math.ceil(daysLeft);
  return `About ${days} ${days === 1 ? "day" : "days"} left`;
}

/** A bar of the Done share, with what is in progress shown lighter. */
export function ProgressBar({ done, total, working = 0, className, label }: {
  done: number; total: number; working?: number; className?: string; label: string;
}) {
  const doneShare = total ? (done / total) * 100 : 0;
  const workingShare = total ? (working / total) * 100 : 0;
  return (
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}
      aria-valuetext={`${done} of ${total} done`} className={cn("flex h-1.5 w-full overflow-hidden rounded-full bg-muted", className)}>
      <div className="h-full bg-primary" style={{ width: `${doneShare}%` }} />
      <div className="h-full bg-primary/35" style={{ width: `${workingShare}%` }} />
    </div>
  );
}

/** One project's or Feature's progress: the bar, the counts and the estimate. */
export function ProgressSummary({ entry, name }: { entry: ProgressEntry; name: string }) {
  const working = entry.counts.in_progress + entry.counts.review;
  const share = entry.total ? Math.round((100 * entry.done) / entry.total) : 0;
  const extra = [
    entry.counts.in_progress && `${entry.counts.in_progress} in progress`,
    entry.counts.review && `${entry.counts.review} in review`,
    entry.blocked && `${entry.blocked} blocked`,
    entry.stale && `${entry.stale} stale`,
  ].filter(Boolean);
  return (
    <div className="grid gap-1">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="min-w-0 truncate font-medium">{name}</span>
        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{entry.done} of {entry.total} done · {share}%</span>
      </div>
      <ProgressBar done={entry.done} total={entry.total} working={working} label={`${name}: ${entry.done} of ${entry.total} done`} />
      <p className="text-xs text-muted-foreground">
        {extra.length > 0 && <>{extra.join(" · ")} · </>}
        <span className={cn(entry.days_left !== null && entry.days_left > 0 && "font-medium text-foreground")}>{estimateText(entry.days_left)}</span>
      </p>
    </div>
  );
}

/** The toolbar's Progress button: every project and Feature of the Board. */
export function ProgressPanel() {
  return (
    <Popover>
      <PopoverTrigger render={<Button variant="outline" size="sm" />}>
        <ChartNoAxesColumn aria-hidden /> Progress
      </PopoverTrigger>
      <PopoverContent align="start" className="max-h-[70dvh] w-96 max-w-[calc(100vw-2rem)] overflow-y-auto" aria-label="Progress">
        <ProgressList />
      </PopoverContent>
    </Popover>
  );
}

function ProgressList() {
  const report = useCached<TaskProgress>("tasks-progress", () => getProgress(), { refreshInterval: 15_000, persist: false });
  if (!report.data) {
    return report.error ? <p className="text-sm text-destructive">{report.error.message}</p>
      : <div className="space-y-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-10 w-full" />)}</div>;
  }
  const { projects, features } = report.data;
  if (!projects.length && !features.length) return <p className="text-sm text-muted-foreground">No tasks to measure yet.</p>;
  return (
    <div className="grid gap-4">
      {projects.length > 0 && (
        <section className="grid gap-3">
          <h3 className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Projects</h3>
          {projects.map((p) => <ProgressSummary key={p.name} entry={p} name={p.name} />)}
        </section>
      )}
      {features.length > 0 && (
        <section className="grid gap-3">
          <h3 className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Features</h3>
          {features.map((f) => <ProgressSummary key={f.id} entry={f} name={`${f.title} · ${f.project}`} />)}
        </section>
      )}
      <p className="text-[11px] text-muted-foreground">
        Done out of every Task but the Cancelled ones. How long is left follows the pace of the last 14 days, once 3 Tasks
        were finished in them.
      </p>
    </div>
  );
}
