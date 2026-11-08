"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, CircleAlert, CircleCheck, Clock, FileText, MessageSquare, RefreshCw } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { DashboardSummary } from "@/lib/api";
import { ago } from "@/lib/queue";
import type { QueueResponse } from "@/lib/types";

/** The time, again every minute: "5 min ago" labels stay true on an open page. */
export function useNow(every = 60_000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), every);
    return () => clearInterval(timer);
  }, [every]);
  return now;
}

type Notice = { id: string; tone: "problem" | "waiting"; title: string; detail?: string; href: string; action: string };

const TAGGING: Partial<Record<string, string>> = {
  running: "Background tagging is working through them.",
  yielded: "Background tagging resumes after the queued jobs.",
  waiting: "Background tagging starts when the queue is idle.",
  paused: "Background tagging is paused.",
  failed: "Background tagging stopped with an error.",
  off: "LLM tagging is off on this device.",
};

/** What needs the viewer: a job that failed in the last day, memories
 * waiting for tagging, a summary that could not be refreshed. Each notice
 * carries an icon and words, never colour alone. */
export function Attention({ summary, queue }: { summary: DashboardSummary | undefined; queue: QueueResponse | null }) {
  const now = useNow();
  const notices: Notice[] = [];
  const failed = queue?.recent?.find((job) => job.status === "failed" && (job.ended_at ?? 0) > now / 1000 - 86400);
  if (failed) {
    notices.push({
      id: `failed-${failed.id}`, tone: "problem",
      title: `${failed.label ?? failed.command} failed ${failed.ended_at ? ago(failed.ended_at) : ""}`.trim(),
      detail: failed.error ?? undefined, href: "/queue/", action: "Open queue",
    });
  }
  if (summary && summary.tag_pending > 0) {
    const state = queue?.background?.state;
    notices.push({
      id: "tagging", tone: "waiting",
      title: `${summary.tag_pending.toLocaleString()} ${summary.tag_pending === 1 ? "memory waits" : "memories wait"} for tagging`,
      detail: (state && TAGGING[state]) ?? undefined, href: "/queue/", action: "Open queue",
    });
  }
  if (summary?.refresh_error) {
    notices.push({ id: "summary", tone: "problem", title: "The summary could not be refreshed", detail: summary.refresh_error, href: "/queue/", action: "Open queue" });
  }
  return (
    <Card className="gap-3">
      <CardHeader><CardTitle className="text-sm"><h2>Needs attention</h2></CardTitle></CardHeader>
      <CardContent>
        {notices.length === 0 ? (
          <p className="flex items-center gap-2 py-1 text-sm text-muted-foreground">
            <CircleCheck className="size-4 shrink-0" aria-hidden />
            Nothing needs you right now.
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {notices.map((notice) => {
              const Icon = notice.tone === "problem" ? CircleAlert : Clock;
              return (
                <li key={notice.id} className="flex flex-wrap items-start gap-x-3 gap-y-1 py-2.5 first:pt-0 last:pb-0">
                  <Icon className={`mt-0.5 size-4 shrink-0 ${notice.tone === "problem" ? "text-destructive" : "text-muted-foreground"}`} aria-label={notice.tone === "problem" ? "Problem" : "Waiting"} />
                  <div className="min-w-0 flex-1 basis-48">
                    <p className="text-sm font-medium">{notice.title}</p>
                    {notice.detail && <p className="line-clamp-2 text-xs text-muted-foreground" title={notice.detail}>{notice.detail}</p>}
                  </div>
                  <Link href={notice.href} className="ml-7 inline-flex shrink-0 items-center gap-1 rounded-sm text-xs sm:ml-0 text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
                    {notice.action}<ArrowRight className="size-3" aria-hidden />
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

const basename = (source: string) => source.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || source;

/** Where the newest memories came from, newest first. */
export function RecentSources({ recent }: { recent: DashboardSummary["recent"] }) {
  return (
    <Card className="gap-3">
      <CardHeader><CardTitle className="text-sm"><h2>Recently added</h2></CardTitle></CardHeader>
      <CardContent>
        {recent.length === 0 ? (
          <p className="py-1 text-sm text-muted-foreground">Nothing yet.</p>
        ) : (
          <ul className="-mx-1.5 space-y-0.5">
            {recent.map((item) => {
              const conversation = !!item.title || item.type === "conversation" || /\.jsonl$/i.test(item.source);
              const Icon = conversation ? MessageSquare : FileText;
              const name = item.title || basename(item.source);
              return (
                <li key={item.source}>
                  <Link
                    href={item.project ? `/projects/?project=${encodeURIComponent(item.project)}` : "/sources/"}
                    className="group/row flex items-center gap-3 rounded-md px-1.5 py-1.5 outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                      <Icon className="size-3.5" aria-hidden />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm" title={item.source}>{name}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {item.project || "No project"}
                        {item.memories > 1 ? ` · ${item.memories.toLocaleString()} memories` : ""}
                      </span>
                    </span>
                    {item.timestamp ? <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{ago(item.timestamp)}</span> : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/** "Updated 2 min ago" or "Updating…", and a button that asks for an up-to-date summary. */
export function Freshness({ builtAt, updating, failed, onRefresh }: {
  builtAt: number | null; updating: boolean; failed: boolean; onRefresh: () => void;
}) {
  useNow(15_000);
  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <span aria-live="polite" className="flex items-center gap-1.5">
        {updating ? (
          <><span className="size-1.5 rounded-full bg-foreground/60 motion-safe:animate-pulse" aria-hidden />Updating…</>
        ) : failed ? (
          <><CircleAlert className="size-3.5" aria-hidden />Not refreshed{builtAt ? ` · from ${ago(builtAt)}` : ""}</>
        ) : builtAt ? (
          <>Updated {ago(builtAt)}</>
        ) : null}
      </span>
      <button
        type="button"
        onClick={onRefresh}
        disabled={updating}
        aria-label="Refresh dashboard"
        title="Refresh"
        className="inline-flex size-7 items-center justify-center rounded-md outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
      >
        <RefreshCw className={`size-3.5 ${updating ? "motion-safe:animate-spin" : ""}`} aria-hidden />
      </button>
    </div>
  );
}
