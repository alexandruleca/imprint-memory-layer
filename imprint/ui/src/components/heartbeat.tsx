"use client";

import { useEffect } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8420";

export function Heartbeat() {
  useEffect(() => {
    let cancelled = false;

    async function ping() {
      if (cancelled) return;
      try {
        await fetch(`${API_BASE}/api/ping`);
      } catch {
        // server unreachable — ignore
      }
    }

    ping();
    const interval = setInterval(ping, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return null;
}
