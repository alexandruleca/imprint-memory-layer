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
    const queue = await getQueue(10);
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

/** The queue: the running job, the waiting ones and the latest ten that ended. */
export function useQueue(): QueueResponse | null {
  return useSyncExternalStore(subscribe, () => snapshot, () => null);
}

/** A job's progress in a line: "learn · embedding · 3/112 conversations". */
export function describeJob(job: { command: string; phase?: string; processed?: number; total?: number; unit?: string }) {
  const parts = [job.command];
  if (job.phase) parts.push(job.phase.replace(/_/g, " "));
  if (job.total) parts.push(`${job.processed ?? 0}/${job.total} ${job.unit ?? "files"}`);
  return parts.join(" · ");
}
