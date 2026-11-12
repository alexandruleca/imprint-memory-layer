"use client";

import { useSyncExternalStore } from "react";
import { getUiState, setConfigValue } from "@/lib/api";

/** The simple and the advanced dashboard (ADR 0182): one set of pages, data
 * and settings, shown two ways. The mode is the `ui.mode` setting, so every
 * window of this device agrees; the last answer is also kept in this browser,
 * so the navigation is drawn right before the API has answered. */
export type UiMode = "simple" | "advanced";
export type UiState = {
  mode: UiMode;
  /** Tutorials start by themselves (`ui.tutorials`, ADR 0183). */
  tutorials: boolean;
  /** The mode can be drawn: the API answered, or this browser remembers it. */
  known: boolean;
  /** The API answered, or could not. */
  settled: boolean;
};

export const UI_MODE_KEY = "imprint.ui.mode";
const SERVER: UiState = { mode: "advanced", tutorials: false, known: false, settled: false };

const listeners = new Set<() => void>();
let state: UiState | null = null;
let loading: Promise<void> | null = null;

function isMode(value: unknown): value is UiMode {
  return value === "simple" || value === "advanced";
}

function hint(): UiMode | null {
  try {
    const stored = localStorage.getItem(UI_MODE_KEY);
    return isMode(stored) ? stored : null;
  } catch {
    return null;
  }
}

function remember(mode: UiMode) {
  try { localStorage.setItem(UI_MODE_KEY, mode); } catch {}
}

function publish(next: UiState) {
  state = next;
  document.documentElement.dataset.uiMode = next.mode;
  listeners.forEach((listener) => listener());
}

function current(): UiState {
  if (!state) {
    const stored = hint();
    state = { mode: stored ?? "advanced", tutorials: false, known: stored !== null, settled: false };
  }
  return state;
}

/** Ask the API. An answer that names no mode (an older service) and a failed
 * request both leave what this browser remembers, else the advanced dashboard:
 * every page stays reachable while the service cannot say. */
function load() {
  loading ??= getUiState()
    .then((answer) => {
      if (!isMode(answer?.mode)) throw new Error("no mode");
      remember(answer.mode);
      publish({ mode: answer.mode, tutorials: answer.tutorials === true, known: true, settled: true });
    })
    .catch(() => publish({ ...current(), tutorials: false, known: true, settled: true }));
  return loading;
}

/** Read the settings again, after they were changed somewhere else (Settings). */
export function refreshUiState() {
  loading = null;
  return load();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  void load();
  const onStorage = (event: StorageEvent) => {
    // Another window of this device switched.
    if (event.key === UI_MODE_KEY && isMode(event.newValue) && event.newValue !== current().mode) {
      publish({ ...current(), mode: event.newValue, known: true });
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function useUiMode(): UiState & { simple: boolean } {
  const ui = useSyncExternalStore(subscribe, current, () => SERVER);
  return { ...ui, simple: ui.mode === "simple" };
}

/** Switch the dashboard. Shown at once; put back when the setting cannot be saved. */
export async function setUiMode(mode: UiMode) {
  const before = current();
  if (before.mode === mode) return;
  remember(mode);
  publish({ ...before, mode, known: true });
  try {
    await setConfigValue("ui.mode", mode);
  } catch (error) {
    remember(before.mode);
    publish({ ...current(), mode: before.mode });
    throw error;
  }
}

/** Turn tutorials that start by themselves on or off. */
export async function setTutorials(on: boolean) {
  const before = current();
  publish({ ...before, tutorials: on });
  try {
    await setConfigValue("ui.tutorials", on);
  } catch (error) {
    publish({ ...current(), tutorials: before.tutorials });
    throw error;
  }
}
