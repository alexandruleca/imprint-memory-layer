"use client";

import { Progress, ProgressLabel } from "@/components/ui/progress";
import type { DatasetProgress } from "@/lib/types";

function pct(done: number, total: number): number {
  return total > 0 ? Math.min(Math.round((done / total) * 100), 100) : 0;
}

export function SyncProgressBar({
  dataset,
  done,
  total,
}: DatasetProgress) {
  return (
    <Progress value={pct(done, total)} className="gap-1">
      <ProgressLabel className="text-xs font-normal capitalize text-muted-foreground">{dataset}</ProgressLabel>
      <span className="ml-auto text-xs tabular-nums text-muted-foreground">
        {done}/{total}
      </span>
    </Progress>
  );
}

export function SyncProgressGroup({
  datasets,
}: {
  datasets: Record<string, DatasetProgress>;
}) {
  const entries = Object.values(datasets);
  if (entries.length === 0) return null;
  return (
    <div className="space-y-3">
      {entries.map((d) => (
        <SyncProgressBar key={d.dataset} {...d} />
      ))}
    </div>
  );
}
