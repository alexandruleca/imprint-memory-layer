"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cancelJob, getQueue } from "@/lib/api";
import { Spinner } from "@/components/loaders";
import { HardDriveDownload, Sparkles, X, ListOrdered } from "lucide-react";
import type { QueueJob, QueueResponse } from "@/lib/types";

function formatEta(seconds: number | null | undefined): string {
  if (seconds == null) return "calculating...";
  if (seconds < 60) return `~${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `~${m}m ${s}s`;
}

export function IngestionProgress() {
  const [data, setData] = useState<QueueResponse | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const q = await getQueue(0);
        if (!cancelled) setData(q);
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

function ActiveCard({ job }: { job: QueueJob }) {
  const isTagging = job.phase === "llm_tagging";
  const accent = isTagging
    ? {
        border: "border-violet-500/30",
        bg: "bg-violet-950/10",
        bar: "#8b5cf6",
        badgeBorder: "border-violet-500/50",
        badgeText: "text-violet-400",
        spinner: "text-violet-500",
        gradient: "linear-gradient(90deg, #8b5cf6, #a78bfa)",
      }
    : {
        border: "border-amber-500/30",
        bg: "bg-amber-950/10",
        bar: "#f59e0b",
        badgeBorder: "border-amber-500/50",
        badgeText: "text-amber-400",
        spinner: "text-amber-500",
        gradient: "linear-gradient(90deg, #f59e0b, #fbbf24)",
      };
  const label = isTagging ? "LLM tagging" : job.command;
  const Icon = isTagging ? Sparkles : HardDriveDownload;
  const unit = isTagging ? "chunks" : "files";
  const pct = Math.min(job.percent ?? 0, 100);
  const processed = job.processed ?? 0;
  const total = job.total ?? 0;
  const stored = job.stored ?? 0;
  const skipped = job.skipped ?? 0;

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
            {processed}/{total} {unit}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-muted-foreground hover:text-red-400"
            onClick={() => cancelJob(job.id).catch(() => {})}
            title="Cancel running job"
          >
            <X className="w-3.5 h-3.5" />
          </Button>
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
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-6 px-1.5 text-muted-foreground hover:text-red-400"
                onClick={() => cancelJob(j.id).catch(() => {})}
                title="Remove from queue"
              >
                <X className="w-3 h-3" />
              </Button>
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
