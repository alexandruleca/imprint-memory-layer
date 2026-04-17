"use client";

import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { getJobs } from "@/lib/api";
import { Spinner } from "@/components/loaders";
import { HardDriveDownload, Sparkles } from "lucide-react";
import type { IngestionJob } from "@/lib/types";

function formatEta(seconds: number | null): string {
  if (seconds === null) return "calculating...";
  if (seconds < 60) return `~${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `~${m}m ${s}s`;
}

export function IngestionProgress() {
  const [job, setJob] = useState<IngestionJob | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const data = await getJobs();
        if (!cancelled) {
          setJob(data.jobs.length > 0 ? data.jobs[0] : null);
        }
      } catch {
        // API unreachable — ignore
      }
    }

    poll();
    const interval = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (!job) return null;

  const pct = Math.min(job.percent, 100);
  const isTagging = job.phase === "llm_tagging";
  const accent = isTagging
    ? { border: "border-violet-500/30", bg: "bg-violet-950/10", bar: "#8b5cf6", badgeBorder: "border-violet-500/50", badgeText: "text-violet-400", spinner: "text-violet-500", gradient: "linear-gradient(90deg, #8b5cf6, #a78bfa)" }
    : { border: "border-amber-500/30", bg: "bg-amber-950/10", bar: "#f59e0b", badgeBorder: "border-amber-500/50", badgeText: "text-amber-400", spinner: "text-amber-500", gradient: "linear-gradient(90deg, #f59e0b, #fbbf24)" };
  const label = isTagging ? "LLM tagging" : job.command;
  const Icon = isTagging ? Sparkles : HardDriveDownload;
  const unit = isTagging ? "chunks" : "files";

  return (
    <Card className={`relative overflow-hidden ${accent.border} ${accent.bg}`}>
      <div
        className="absolute left-0 top-0 bottom-0 w-1"
        style={{ backgroundColor: accent.bar }}
      />
      <CardContent className="py-4 pl-5 pr-4 space-y-3">
        <div className="flex items-center gap-3">
          <Spinner className={`w-4 h-4 ${accent.spinner}`} />
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <Badge
              variant="outline"
              className={`${accent.badgeBorder} ${accent.badgeText} text-[10px] uppercase shrink-0`}
            >
              <Icon className="w-3 h-3 mr-1" />
              {label}
            </Badge>
            {job.projects.map((p) => (
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
            {job.processed}/{job.total} {unit}
          </span>
        </div>

        <div className="space-y-1.5">
          <div className="h-2 bg-muted rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-700 ease-out"
              style={{
                width: `${pct}%`,
                background: accent.gradient,
              }}
            />
          </div>
          <div className="flex justify-between text-[11px] text-muted-foreground tabular-nums">
            <span>
              {Math.round(pct)}% &middot;{" "}
              {isTagging
                ? `${job.stored} tagged`
                : `${job.stored} stored, ${job.skipped} skipped`}
            </span>
            <span>{formatEta(job.eta_seconds)}</span>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
