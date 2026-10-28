import { useSyncExternalStore } from "react";
import { getQueue } from "@/lib/api";
import type { QueueResponse } from "@/lib/types";

// One poll of /api/queue for every component that shows the queue. The
// running job's progress record is what every surface shows (ADR 0089).
const POLL_MS = 2000;
let snapshot: QueueResponse | null = null;
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | undefined;

async function poll() {
  try {
    const queue = await getQueue(30);
    snapshot = Array.isArray(queue?.queued) && Array.isArray(queue?.recent) ? queue : null;
  } catch {
    snapshot = null; // The heartbeat reports an unreachable service.
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) {
    void poll();
    timer = setInterval(poll, POLL_MS);
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) clearInterval(timer);
  };
}

/** The queue: the running job, the waiting ones and the latest thirty that ended. */
export function useQueue(): QueueResponse | null {
  return useSyncExternalStore(subscribe, () => snapshot, () => null);
}

/** What a job is doing now, from its progress phase: "llm_tagging" → "LLM tagging". */
export function phaseName(phase: string) {
  return phase.replace(/_/g, " ").replace(/\bllm\b/gi, "LLM");
}

/** A job's progress in a line: "Learn conversations · embedding · 3/112 conversations". */
export function describeJob(job: { command: string; label?: string; phase?: string; processed?: number; total?: number; unit?: string }) {
  const parts = [job.label ?? job.command];
  if (job.phase) parts.push(phaseName(job.phase));
  if (job.total) parts.push(`${(job.processed ?? 0).toLocaleString()}/${job.total.toLocaleString()} ${job.unit ?? "files"}`);
  return parts.join(" · ");
}

export function formatDuration(seconds: number): string {
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m ${Math.round(seconds % 60)}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

/** How long a job ran, or has run so far; null before it starts. */
export function runTime(job: { status: string; started_at: number | null; ended_at: number | null; elapsed?: number }): number | null {
  if (job.status === "running") return job.elapsed ?? (job.started_at ? Date.now() / 1000 - job.started_at : null);
  return job.started_at && job.ended_at ? job.ended_at - job.started_at : null;
}

/** "just now", "5 min ago", "3 h ago", then the date. */
export function ago(ts: number): string {
  const seconds = Date.now() / 1000 - ts;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
