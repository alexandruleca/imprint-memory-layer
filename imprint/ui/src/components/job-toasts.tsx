"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { useQueue } from "@/lib/queue";
import type { QueueJob } from "@/lib/types";

/** Reports each queued job this tab saw waiting or running when it ends,
 * whichever page is open by then. */
export function JobToasts() {
  const router = useRouter();
  const queue = useQueue();
  const watched = useRef(new Set<string>());

  useEffect(() => {
    if (!queue) return;
    for (const job of queue.recent) {
      if (watched.current.has(job.id)) announce(job, () => router.push("/queue"));
    }
    watched.current = new Set([queue.active, ...queue.queued].flatMap((job) => (job ? [job.id] : [])));
  }, [queue, router]);

  return null;
}

function announce(job: QueueJob, openQueue: () => void) {
  const title = `imprint ${job.command}`;
  const target = job.body.dir ?? job.body.url;
  const description = typeof target === "string" ? target : undefined;
  if (job.status === "done") toast.success(`${title} finished`, { description });
  else if (job.status === "cancelled") toast(`${title} cancelled`, { description });
  else toast.error(`${title} failed`, {
    description: job.error || (job.exit_code != null ? `Exit code ${job.exit_code}` : description),
    action: { label: "Open queue", onClick: openQueue },
  });
}
