"use client";

import { useSyncExternalStore } from "react";

/** The dashboard's colour theme: the viewer's choice, kept per device. "system"
 * follows the operating system's light or dark setting as it changes. The
 * inline script in app/layout.tsx applies the same choice before first paint;
 * keep the two in step (THEME_KEY, the `dark` class, color-scheme). */
export type ThemeChoice = "light" | "dark" | "system";
export const THEME_KEY = "imprint.theme";

const listeners = new Set<() => void>();
let fallback: ThemeChoice | null = null;

function isChoice(value: unknown): value is ThemeChoice {
  return value === "light" || value === "dark" || value === "system";
}

export function readChoice(): ThemeChoice {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (isChoice(stored)) return stored;
  } catch {
    // Storage blocked: the choice lasts for this page only.
  }
  return fallback ?? "system";
}

function systemDark() {
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
}

/** Put the resolved theme on <html>: the `dark` class Tailwind's dark: variant keys on, and color-scheme for native controls. */
export function applyTheme(choice: ThemeChoice = readChoice()) {
  const dark = choice === "dark" || (choice === "system" && systemDark());
  const root = document.documentElement;
  root.classList.toggle("dark", dark);
  root.style.colorScheme = dark ? "dark" : "light";
  root.dataset.themeChoice = choice;
  // The desktop app follows it: native surfaces, window backgrounds and
  // prefers-color-scheme in its other windows (ADR 0154).
  try { window.electronAPI?.setTheme?.(choice); } catch {}
}

export function setTheme(choice: ThemeChoice) {
  fallback = choice;
  try {
    localStorage.setItem(THEME_KEY, choice);
  } catch {
    // Not remembered across reloads; applied all the same.
  }
  applyTheme(choice);
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const media = typeof matchMedia === "function" ? matchMedia("(prefers-color-scheme: dark)") : null;
  const onSystem = () => { if (readChoice() === "system") applyTheme("system"); listener(); };
  const onStorage = (event: StorageEvent) => { if (event.key === THEME_KEY) { applyTheme(); listener(); } };
  media?.addEventListener("change", onSystem);
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    media?.removeEventListener("change", onSystem);
    window.removeEventListener("storage", onStorage);
  };
}

/** The stored choice ("system" before hydration). */
export function useThemeChoice(): ThemeChoice {
  return useSyncExternalStore(subscribe, readChoice, () => "system");
}
