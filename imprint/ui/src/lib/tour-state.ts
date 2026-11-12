"use client";

import { useMemo } from "react";
import { useStoredString } from "@/lib/use-stored";

/** Which tutorials this browser has finished or skipped (ADR 0183). */
export const TOURS_SEEN_KEY = "imprint.tours.seen";

function parse(raw: string | null): string[] {
  try {
    const seen = JSON.parse(raw ?? "[]");
    return Array.isArray(seen) ? seen.filter((id) => typeof id === "string") : [];
  } catch {
    return [];
  }
}

export function useSeenTours(): { seen: string[]; markSeen: (id: string) => void; forgetAll: () => void } {
  const [raw, set] = useStoredString(TOURS_SEEN_KEY);
  return useMemo(() => {
    const seen = parse(raw);
    return {
      seen,
      markSeen: (id: string) => { if (!seen.includes(id)) set(JSON.stringify([...seen, id])); },
      forgetAll: () => set("[]"),
    };
  }, [raw, set]);
}

// The help menu asks for a tour; the host that draws tours listens.
type Request = { id: string } | { resume: true };
const listeners = new Set<(request: Request) => void>();

/** Show a tour now, seen or not: "welcome" or a page's href. */
export function requestTour(id: string) {
  listeners.forEach((listener) => listener({ id }));
}

/** Tutorials may start by themselves again on this page, after a skip. */
export function resumeTours() {
  listeners.forEach((listener) => listener({ resume: true }));
}

export function onTourRequest(listener: (request: Request) => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
