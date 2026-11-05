import { useCallback, useSyncExternalStore } from "react";

function onHashChange(changed: () => void) {
  window.addEventListener("hashchange", changed);
  return () => window.removeEventListener("hashchange", changed);
}

const hash = () => window.location.hash.slice(1);

/** The tab the address links to, as on the Sync page: `#moved` opens the
 * "moved" tab. Null when the hash names none of `sections` (and while the
 * static page renders), so the page can choose its default from its data.
 * `open` records the choice with replaceState, so tabs do not fill history. */
export function useHashTab<T extends string>(sections: readonly T[]): [T | null, (next: T) => void] {
  const linked = useSyncExternalStore(onHashChange, hash, () => "");
  const open = useCallback((next: T) => {
    window.history.replaceState(null, "", `#${next}`);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  }, []);
  return [(sections as readonly string[]).includes(linked) ? (linked as T) : null, open];
}
