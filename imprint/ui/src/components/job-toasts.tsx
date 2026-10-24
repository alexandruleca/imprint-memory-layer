"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { getQueue } from "@/lib/api";
import type { QueueJob } from "@/lib/types";

const POLL_MS = 3000;

/** Reports each queued job this tab saw waiting or running when it ends,
 * whichever page is open by then. */
export function JobToasts() {
  const router = useRouter();

  useEffect(() => {
    let watched = new Set<string>();
    let cancelled = false;
    async function poll() {
      try {
        const { active, queued, recent } = await getQueue(10);
        if (cancelled) return;
        for (const job of recent) {
          if (watched.has(job.id)) announce(job, () => router.push("/queue"));
        }
        watched = new Set([active, ...queued].flatMap((job) => (job ? [job.id] : [])));
      } catch {
        // The heartbeat reports an unreachable service.
      }
    }
    void poll();
    const timer = setInterval(poll, POLL_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [router]);

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
