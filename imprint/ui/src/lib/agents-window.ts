"use client";

import { useSyncExternalStore } from "react";

/** Whether this page runs in the desktop app's Agents window (ADR 0232): the
 * Agents page alone, without the sidebar, opened with ?window=agents. */
export function isAgentsWindow(): boolean {
  try {
    return new URLSearchParams(window.location.search).get("window") === "agents";
  } catch {
    return false;
  }
}

function subscribe(changed: () => void) {
  window.addEventListener("popstate", changed);
  return () => window.removeEventListener("popstate", changed);
}

/** isAgentsWindow, for rendering: false on the prerendered page. */
export function useAgentsWindow(): boolean {
  return useSyncExternalStore(subscribe, isAgentsWindow, () => false);
}
