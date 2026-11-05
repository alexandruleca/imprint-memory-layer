"use client";

import { useEffect, useRef, useState } from "react";
import type { WakeView } from "./wake";

/** The desktop app's floating orb and its ring of actions (ADR 0135). The main
 * process owns their windows and passes these values between them. */

/** A meeting recording as the desktop app passes it between its windows. */
export type LauncherRecorder = {
  phase: "idle" | "starting" | "recording" | "stopping";
  id: string; title: string; project: string; startedAt: number; others: boolean; error: string;
};

export const IDLE_RECORDER: LauncherRecorder = { phase: "idle", id: "", title: "", project: "", startedAt: 0, others: false, error: "" };

export type LauncherState = {
  ring: boolean;
  panel: "record" | "live" | null;
  echo: boolean;
  /** The side of the orb Echo is docked on: 1 right, -1 left, 0 above or below. */
  echoSide: number;
  recorder: LauncherRecorder;
  activity: { text: string; percent: number | null } | null;
  /** "Hey Echo" (ADR 0137); the ear dot shows while `listening`. */
  wake?: WakeView;
};

type Box = { x: number; y: number; width: number; height: number };
export type HaloSatellite = { angle: number; x: number; y: number; slot: Box };
export type HaloLayout = {
  kind: "ring" | "record" | "live";
  side: 1 | -1;
  tilt: "level" | "down" | "up";
  bounds: Box;
  /** The orb's centre in this window. */
  orb: { x: number; y: number };
  radius?: number;
  satellites?: HaloSatellite[];
  /** The panel's corner nearest the orb, as a CSS transform-origin. */
  origin?: string;
  seq: number;
  focus: "first" | "last" | null;
  echo: boolean;
  recorder: LauncherRecorder;
};

/** The recording the dashboard reports, with its microphone and computer
 * levels. `onChange` hears each new state with the one before it. */
export function useLauncherRecorder(onChange?: (next: LauncherRecorder, previous: LauncherRecorder) => void) {
  const [recorder, setRecorder] = useState<LauncherRecorder>(IDLE_RECORDER);
  const [levels, setLevels] = useState<[number, number]>([0, 0]);
  const listener = useRef(onChange);
  useEffect(() => { listener.current = onChange; });
  useEffect(() => {
    const api = window.electronAPI;
    let alive = true, last = IDLE_RECORDER;
    const update = (state: LauncherRecorder) => {
      if (!alive) return;
      const previous = last;
      last = state;
      setRecorder(state);
      if (state.phase !== "recording") setLevels([0, 0]);
      listener.current?.(state, previous);
    };
    const unsubscribe = api?.onRecorder?.((message) => {
      if (message.state) update(message.state);
      if (message.levels) setLevels(message.levels);
    });
    void api?.getRecorder?.().then((state) => { if (state) update(state); }).catch(() => {});
    return () => { alive = false; unsubscribe?.(); };
  }, []);
  return { recorder, levels };
}

/** The current time, ticking each second while `active`. */
export function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const first = setTimeout(() => setNow(Date.now()), 0);
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => { clearTimeout(first); clearInterval(timer); };
  }, [active]);
  return now;
}

/** "12 minutes 4 seconds", for screen readers. */
export function spokenDuration(seconds: number) {
  const s = Math.max(0, Math.floor(seconds)), h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
  const part = (n: number, unit: string) => `${n} ${unit}${n === 1 ? "" : "s"}`;
  return [h && part(h, "hour"), (h || m) && part(m, "minute"), part(s % 60, "second")].filter(Boolean).join(" ");
}
