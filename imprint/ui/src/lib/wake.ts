"use client";

import { useEffect, useState } from "react";
import { API_BASE } from "./api-base";

/** "Hey Echo" (ADR 0137) as the desktop app reports it. `listening` is true
 * exactly while its hidden listener window exists; every indicator uses it. */
export type WakeView = {
  enabled: boolean;
  listening: boolean;
  /** Why it is not listening now ("" while it is): off, blocked, paused, recording, conversation, … */
  reason: string;
  pausedUntil: number;
  battery: "listen" | "pause";
  /** Why the microphone could not be used, until the user turns it on again. */
  problem: string;
  checking: boolean;
  /** When "Hey Echo" was last heard (ms), for the orb's pulse. */
  heard: number;
};

export type WakeStatus = {
  installed: boolean;
  model: string;
  bytes: number;
  idle_seconds: number;
  listening: boolean;
  setup: { stage: string; downloaded: number; total: number; problem: string | null; busy: boolean };
};

const PAUSED: Record<string, string> = {
  starting: "Starting…", recording: "Paused while a meeting is recorded", conversation: "Paused while Echo listens",
  locked: "Paused while the screen is locked", asleep: "Paused while the computer sleeps", battery: "Paused on battery",
  hot: "Paused while the computer is hot", retrying: "Reconnecting to the local service…",
};

function clockTime(ms: number) {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** One line for the state, or "" while it is off. */
export function wakeSummary(view: WakeView | null) {
  if (!view?.enabled) return "";
  if (view.listening) return "Listening for “Hey Echo”";
  if (view.reason === "blocked") return view.problem || "Can't use the microphone";
  if (view.reason === "paused") return `Paused until ${clockTime(view.pausedUntil)}`;
  return PAUSED[view.reason] || "Paused";
}

/** The desktop app's wake state, kept current; null in a browser or until known. */
export function useWakeState() {
  const [view, setView] = useState<WakeView | null>(null);
  useEffect(() => {
    const api = window.electronAPI;
    let alive = true;
    const update = (next: WakeView | null) => { if (alive && next) setView(next); };
    const unsubscribe = api?.onWakeState?.(update);
    void api?.getWakeState?.().then(update).catch(() => {});
    return () => { alive = false; unsubscribe?.(); };
  }, []);
  return view;
}

async function call(path: string, method = "GET"): Promise<WakeStatus> {
  const response = await fetch(`${API_BASE}/api/voice/wake/${path}`, { method });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || "The local service did not answer.");
  return response.json();
}
export const wakeStatus = () => call("status");
/** Turn on's one-time download of the pinned wake model (78 MB). */
export const downloadWakeModel = () => call("setup", "POST");
export const cancelWakeDownload = () => call("setup/cancel", "POST");
