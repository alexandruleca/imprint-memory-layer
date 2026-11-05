"use client";

import Link from "next/link";
import { ArrowRight, Sparkles } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import type { OrganizeReview } from "@/lib/api";
import { ago } from "@/lib/queue";
import type { QueueJob } from "@/lib/types";
import { cn } from "@/lib/utils";

export type RunSummary = { profiles: number; pending: number; moved?: number; core: number; model: boolean };

export function runSummary(review: OrganizeReview | null): RunSummary | null {
  if (!review?.last_run) return null;
  try { return JSON.parse(review.last_run.summary) as RunSummary; } catch { return null; }
}

/** "Last run 2 min ago: 46 profiles · 142 moved · 91 to review · 3 core memory proposals". */
export function lastRunText(review: OrganizeReview) {
  const summary = runSummary(review);
  if (!review.last_run || !summary) return "Organize has not run in this workspace yet.";
  return `Last run ${ago(review.last_run.finished_at)}: ${summary.profiles} profiles · ${summary.moved ?? 0} moved · `
    + `${summary.pending} to review · ${summary.core} core memory proposals${summary.model ? "" : " · without the local helper"}`;
}

/** The running Organize job, queued or in the background (ADR 0132). */
export function organizeJob(queue: { active: QueueJob | null; background?: { state: string; job: QueueJob | null } } | null): QueueJob | null {
  if (!queue) return null;
  if (queue.active?.command === "organize") return queue.active;
  const background = queue.background;
  return background?.state === "running" && background.job?.phase === "organize" ? background.job : null;
}

/** Whether Organize runs by itself and moves strong matches, what it is doing
 * now, what holds moves back, the last run and the core memory proposals
 * waiting on the Core memories page. */
export function StatusStrip({ review, running, lock, core, arrived }: {
  review: OrganizeReview;
  running: QueueJob | null;
  /** The job holding the ingestion lock, which moves wait for. */
  lock: string | null;
  /** Core memory proposals waiting, live from /api/core. */
  core: number | null;
  /** Proposals that arrived while the page was open. */
  arrived: number;
}) {
  const background = review.background;
  const autoMove = background?.auto_move ?? true;
  const progress = running?.total ? Math.min(100, Math.floor((100 * (running.processed ?? 0)) / running.total)) : null;
  return (
    <section aria-label="How Organize runs"
      className="mx-4 mb-3 flex flex-col gap-1 rounded-xl border border-border bg-card px-3 py-2 text-sm sm:mx-8">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        {running ? (
          <>
            <span aria-hidden className="size-2 shrink-0 rounded-full bg-foreground motion-safe:animate-pulse" />
            <span className="font-medium">Organizing</span>
            {running.total ? (
              <span className="text-muted-foreground tabular-nums">
                · {(running.processed ?? 0).toLocaleString()}/{running.total.toLocaleString()} {running.unit ?? "steps"}
              </span>
            ) : null}
            {progress !== null && <Progress value={progress} aria-label="Organize progress" className="w-28 sm:w-48" />}
            <Link href="/queue/" className="underline underline-offset-2">Open queue</Link>
          </>
        ) : (
          <>
            <span>
              <span aria-hidden className={cn("mr-2 inline-block size-2 rounded-full align-middle",
                background?.enabled ? "bg-foreground" : "border-[1.5px] border-muted-foreground")} />
              {background?.enabled
                ? <>Runs by itself when the queue is idle{background.last_pass ? <> · last pass {ago(background.last_pass)}</> : null}</>
                : "Runs when you start it."}
            </span>
          </>
        )}
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        {autoMove
          ? <><span className="@max-[620px]/page:hidden">Strong matches move by themselves</span><span className="hidden @max-[620px]/page:inline">Auto-move on</span></>
          : <span>Every move waits for your review.</span>}
        <span aria-hidden className="text-muted-foreground">·</span>
        <Link href="/settings/#settings-organize" className="underline underline-offset-2">Settings ↗</Link>
        {lock && <span className="text-amber-700">· Moves wait while {lock} runs; Keep here still works</span>}
        {arrived > 0 && <span className="text-muted-foreground">· {arrived} new {arrived === 1 ? "proposal" : "proposals"} arrived</span>}
        <span className="flex-1" />
        {!!core && (
          <Link href="/core/#proposed" className="inline-flex items-center gap-1 underline underline-offset-2">
            <Sparkles className="size-3.5" aria-hidden />
            {core} core <span className="@max-[620px]/page:hidden">memory</span> {core === 1 ? "proposal" : "proposals"}
            <ArrowRight className="size-3.5" aria-hidden />
          </Link>
        )}
      </div>
      <p aria-label="Last run" className="text-xs text-muted-foreground @max-[620px]/page:hidden">{lastRunText(review)}</p>
    </section>
  );
}
