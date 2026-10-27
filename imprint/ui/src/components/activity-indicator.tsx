"use client";

import Link from "next/link";
import { Spinner } from "@/components/loaders";
import { describeJob, useQueue } from "@/lib/queue";

/** The running job and how far it is, above every page (ADR 0089). */
export function ActivityIndicator() {
  const queue = useQueue();
  const job = queue?.active ?? (queue?.background?.state === "running" ? queue.background.job : null);
  if (!job) return null;
  const text = describeJob(job);
  return <Link href="/queue/" className="app-no-drag inline-flex min-w-0 max-w-72 items-center gap-2 rounded-full border border-border px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground" aria-label={`${text}. Open the queue`}>
    <Spinner className="size-3"/><span className="truncate">{text}</span>
  </Link>;
}
