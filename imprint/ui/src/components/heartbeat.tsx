"use client";

import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api-base";
import { describeJob } from "@/lib/queue";
import { Button } from "@/components/ui/button";

type Activity = Parameters<typeof describeJob>[0];

export function Heartbeat() {
  const [offline, setOffline] = useState(false);
  // While the service is down the desktop app still reads the running job,
  // such as setup replacing the runtime (ADR 0089).
  const [activity, setActivity] = useState<Activity | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    async function ping() {
      let down = true;
      try {
        const response = await fetch(`${API_BASE}/api/ping`, { signal: AbortSignal.timeout(4000) });
        down = !response.ok;
      } catch { /* Unreachable. */ }
      const running = down ? await window.electronAPI?.activity?.().catch(() => null) ?? null : null;
      if (!cancelled) { setOffline(down); setActivity(running); }
    }
    void ping();
    const interval = setInterval(ping, 5000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [retry]);
  if (!offline) return null;
  return <div role="status" aria-live="polite" className="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 rounded-lg border border-amber-500/50 bg-background p-4 shadow-xl max-w-lg text-sm flex gap-3 items-center">
    {activity
      ? <p>Imprint is busy: {describeJob(activity)}. The dashboard reconnects when it is done.</p>
      : <p>Local service is unavailable. Start Imprint or wait for it to reconnect.</p>}
    <Button variant="link" className="h-auto shrink-0 p-0 underline" onClick={() => setRetry((value) => value + 1)}>Retry connection</Button>
  </div>;
}
