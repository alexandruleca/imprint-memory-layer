"use client";

import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api-base";

export function Heartbeat() {
  const [offline, setOffline] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    async function ping() {
      try {
        const response = await fetch(`${API_BASE}/api/ping`, { signal: AbortSignal.timeout(4000) });
        if (!cancelled) setOffline(!response.ok);
      } catch { if (!cancelled) setOffline(true); }
    }
    void ping();
    const interval = setInterval(ping, 5000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [retry]);
  if (!offline) return null;
  return <div role="status" aria-live="polite" className="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 rounded-lg border border-amber-500/50 bg-background p-4 shadow-xl max-w-lg text-sm flex gap-3 items-center">
    <p>Local service is unavailable. Start Imprint or wait for it to reconnect.</p>
    <button className="text-primary underline shrink-0" onClick={() => setRetry((value) => value + 1)}>Retry connection</button>
  </div>;
}
