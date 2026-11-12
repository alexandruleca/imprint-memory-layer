"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { jobTitle, retry } from "@/components/ingestion-progress";
import { plainJob, useQueue } from "@/lib/queue";
import { useUiMode } from "@/lib/ui-mode";
import type { QueueJob } from "@/lib/types";

/** Reports each queued job this tab saw waiting or running when it ends,
 * whichever page is open by then. A failure offers Retry and the queue. */
export function JobToasts() {
  const router = useRouter();
  const queue = useQueue();
  const watched = useRef(new Set<string>());
  const { simple } = useUiMode();

  useEffect(() => {
    if (!queue) return;
    for (const job of queue.recent) {
      if (watched.current.has(job.id)) announce(job, () => router.push("/queue/"), simple);
    }
    watched.current = new Set([queue.active, ...queue.queued].flatMap((job) => (job ? [job.id] : [])));
  }, [queue, router, simple]);

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

function announce(job: QueueJob, openQueue: () => void, simple: boolean) {
  // The simple dashboard (ADR 0182) says what was being done, in its words.
  if (simple) {
    const what = [plainJob(job), target(job)].filter(Boolean).join(" · ");
    if (job.status === "done") toast.success("Imprint finished", { description: what });
    else if (job.status === "cancelled") toast("Stopped", { description: what });
    else toast.error(`${plainJob(job)} did not finish`, {
      description: reason(job),
      duration: 10_000,
      action: { label: "Try again", onClick: () => void retry(job) },
      cancel: { label: "Details", onClick: openQueue },
    });
    return;
  }
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
