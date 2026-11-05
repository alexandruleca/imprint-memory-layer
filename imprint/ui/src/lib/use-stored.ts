import { useCallback, useSyncExternalStore } from "react";

// Per-browser conveniences (rows per page, shortcuts on or off, what was
// seen): localStorage when it works, the fallback when it does not, as in a
// private window or with site data blocked.
const EVENT = "imprint-stored";

function read(key: string): string | null {
  try { return window.localStorage.getItem(key); } catch { return null; }
}

function subscribe(changed: () => void) {
  window.addEventListener("storage", changed);
  window.addEventListener(EVENT, changed);
  return () => {
    window.removeEventListener("storage", changed);
    window.removeEventListener(EVENT, changed);
  };
}

/** A string kept in this browser; null when nothing is stored. */
export function useStoredString(key: string): [string | null, (value: string) => void] {
  const value = useSyncExternalStore(subscribe, () => read(key), () => null);
  const set = useCallback((next: string) => {
    try { window.localStorage.setItem(key, next); } catch {}
    window.dispatchEvent(new Event(EVENT));
  }, [key]);
  return [value, set];
}

/** A number kept in this browser, else `fallback`; `allowed` limits it. */
export function useStoredNumber(key: string, fallback: number, allowed?: readonly number[]): [number, (value: number) => void] {
  const [raw, set] = useStoredString(key);
  const parsed = raw === null ? NaN : Number(raw);
  const value = Number.isFinite(parsed) && (!allowed || allowed.includes(parsed)) ? parsed : fallback;
  return [value, useCallback((next: number) => set(String(next)), [set])];
}

/** An on/off switch kept in this browser, else `fallback`. */
export function useStoredFlag(key: string, fallback: boolean): [boolean, (value: boolean) => void] {
  const [raw, set] = useStoredString(key);
  return [raw === null ? fallback : raw === "1", useCallback((next: boolean) => set(next ? "1" : "0"), [set])];
}
