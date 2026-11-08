"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { jobTitle, retry } from "@/components/ingestion-progress";
import { useQueue } from "@/lib/queue";
import type { QueueJob } from "@/lib/types";

/** Reports each queued job this tab saw waiting or running when it ends,
 * whichever page is open by then. A failure offers Retry and the queue. */
export function JobToasts() {
  const router = useRouter();
  const queue = useQueue();
  const watched = useRef(new Set<string>());

  useEffect(() => {
    if (!queue) return;
    for (const job of queue.recent) {
      if (watched.current.has(job.id)) announce(job, () => router.push("/queue/"));
    }
    watched.current = new Set([queue.active, ...queue.queued].flatMap((job) => (job ? [job.id] : [])));
  }, [queue, router]);

  return null;
}

/** The first line of what the job asked for, such as its folder or URL. */
function target(job: QueueJob) {
  const param = job.params?.find((p) => p.value)?.value;
  const body = job.body.dir ?? job.body.url;
  return param ?? (typeof body === "string" ? body : undefined);
}

/** A failure's reason in a line: its error's last line, else its exit code. */
function reason(job: QueueJob) {
  const error = job.error?.replace(/cancelled[^;]*;\s*/g, "").replace(/^Traceback \(most recent call last\):\s*/, "")
    .trim().split("\n").filter(Boolean).pop();
  if (!error) return job.exit_code != null ? `Exit code ${job.exit_code}` : target(job);
  return error.length > 120 ? `${error.slice(0, 119)}…` : error;
}

function announce(job: QueueJob, openQueue: () => void) {
  const title = jobTitle(job);
  const description = target(job);
  if (job.status === "done") toast.success(`${title} finished`, { description });
  else if (job.status === "cancelled") toast(`${title} cancelled`, { description });
  else toast.error(`${title} failed`, {
    description: reason(job),
    duration: 10_000,
    action: { label: "Retry", onClick: () => void retry(job) },
    cancel: { label: "Open queue", onClick: openQueue },
  });
}
