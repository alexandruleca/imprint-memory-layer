"use client";

import type { DatasetProgress } from "@/lib/types";

function pct(done: number, total: number): number {
  return total > 0 ? Math.min(Math.round((done / total) * 100), 100) : 0;
}

export function SyncProgressBar({
  dataset,
  done,
  total,
}: DatasetProgress) {
  const p = pct(done, total);
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-muted-foreground">
        <span className="capitalize">{dataset}</span>
        <span className="tabular-nums">
          {done}/{total}
        </span>
      </div>
      <div className="h-2 bg-muted rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500 ease-out"
          style={{
            width: `${p}%`,
            background: "linear-gradient(90deg, #3b82f6, #60a5fa)",
          }}
        />
      </div>
    </div>
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
