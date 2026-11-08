"use client";

/**
 * Stale-while-revalidate reads for the dashboard (ADR 0152).
 *
 *   const { data, error, loading, refreshing, updatedAt, refresh } = useCached("stats", getStats);
 *
 * - The last answer for a key shows at once: from memory while the tab lives,
 *   and from sessionStorage after a reload. It is then fetched again in the
 *   background, and `refreshing` says so; `loading` is true only while there
 *   is nothing to show yet.
 * - Keys are scoped to the active workspace. Nothing is shown until this tab
 *   has asked the API which workspace is active, so one workspace's answer
 *   never appears in another. `workspaceOf` lets an answer that names its
 *   workspace be checked as well.
 * - Requests for one key are shared: components that mount together, focus
 *   events and timers start at most one request at a time.
 * - It fetches again on mount (unless the answer is under `dedupeMs` old),
 *   when the window regains focus, when a queued job starts or ends, every
 *   `refreshInterval` while the page is visible, and, with
 *   `revalidateOnDataChange`, when the API reports a change (/api/stream).
 * - An answer `isStale` marks as stale is asked for again after `staleRetryMs`.
 * - A failed request keeps the last answer and sets `error`; a request that
 *   never reached the API is retried after `retryMs`.
 */

import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { ApiError, connectSSE, getWorkspaces } from "@/lib/api";
import { useQueue } from "@/lib/queue";

export type FetchContext = {
  /** The viewer asked for an up-to-date answer (`refresh()`), not a cached one. */
  fresh: boolean;
};

export type CachedOptions<T> = {
  /** Fetch again every so many milliseconds while the page is visible; 0 (default) never. */
  refreshInterval?: number;
  /** Fetch again when the window regains focus or becomes visible. Default true. */
  revalidateOnFocus?: boolean;
  /** Fetch again when a queued job starts or ends. Default true. */
  revalidateOnJobs?: boolean;
  /** Fetch again (at most every `dataChangeMs`) when the API reports changed data. Default false. */
  revalidateOnDataChange?: boolean;
  dataChangeMs?: number;
  /** Mounting within this many milliseconds of the last answer does not fetch again. Default 2000. */
  dedupeMs?: number;
  /** Keep the last answer in sessionStorage so a reload shows it at once. Default true. */
  persist?: boolean;
  /** The same answer in every workspace (the version, say). Default false. */
  global?: boolean;
  /** The server says a newer answer is being prepared: ask again after `staleRetryMs` (default 2000). */
  isStale?: (data: T) => boolean;
  staleRetryMs?: number;
  /** The workspace an answer belongs to, when it says. */
  workspaceOf?: (data: T) => string | null | undefined;
  /** Retry a request that never reached the API after this many milliseconds. Default 3000; 0 never. */
  retryMs?: number;
};

export type Cached<T> = {
  data: T | undefined;
  error: Error | undefined;
  /** Nothing to show yet and a request is on its way. */
  loading: boolean;
  /** Something is shown and a newer answer is on its way. */
  refreshing: boolean;
  /** When the shown answer arrived (ms since epoch), from memory or storage. */
  updatedAt: number | null;
  /** Fetch now, asking the server for an up-to-date answer. */
  refresh: () => Promise<void>;
  /** Replace the shown answer, e.g. after a write whose result is known. */
  mutate: (data: T) => void;
};

type Snapshot<T> = Omit<Cached<T>, "refresh" | "mutate">;

type Entry = {
  key: string;
  data: unknown;
  error: Error | undefined;
  updatedAt: number | null;
  fetching: boolean;
  promise: Promise<void> | null;
  fetcher: ((ctx: FetchContext) => Promise<unknown>) | null;
  options: CachedOptions<unknown>;
  listeners: Set<() => void>;
  snapshot: Snapshot<unknown>;
  timer: ReturnType<typeof setTimeout> | undefined;
  lastDataChange: number;
  /** An answer arrived in this page's lifetime (a stored one is always checked again). */
  fetched: boolean;
};

const STORAGE_PREFIX = "imprint.cache.v1:";
const MAX_STORED = 512 * 1024;
const entries = new Map<string, Entry>();
const EMPTY: Snapshot<never> = { data: undefined, error: undefined, loading: false, refreshing: false, updatedAt: null };

// ── The active workspace ─────────────────────────────────────────

type WorkspaceState = { name: string | null; error: Error | undefined };
let workspace: WorkspaceState = { name: null, error: undefined };
let workspaceRequest: Promise<void> | null = null;
let workspaceRetry: ReturnType<typeof setTimeout> | undefined;
const workspaceListeners = new Set<() => void>();

function setWorkspace(next: WorkspaceState) {
  if (next.name === workspace.name && next.error === workspace.error) return;
  workspace = next;
  workspaceListeners.forEach((listener) => listener());
}

/** Ask the API which workspace is active; shared by every caller. */
export function resolveWorkspace(): Promise<void> {
  if (workspaceRequest) return workspaceRequest;
  clearTimeout(workspaceRetry);
  workspaceRequest = getWorkspaces()
    .then((answer) => {
      // An answer without a name (an older or mocked API) still scopes keys.
      setWorkspace({ name: typeof answer?.active === "string" ? answer.active : "", error: undefined });
    })
    .catch((cause: unknown) => {
      const error = cause instanceof Error ? cause : new Error(String(cause));
      // Keep the known workspace through a hiccup; only an unknown one is an error.
      if (workspace.name === null) setWorkspace({ name: null, error });
      if (!(cause instanceof ApiError)) workspaceRetry = setTimeout(() => void resolveWorkspace(), 3000);
    })
    .finally(() => { workspaceRequest = null; });
  return workspaceRequest;
}

function subscribeWorkspace(listener: () => void) {
  workspaceListeners.add(listener);
  if (workspace.name === null && !workspaceRequest) void resolveWorkspace();
  return () => { workspaceListeners.delete(listener); };
}

/** The active workspace's name, or null until this tab has asked. */
export function useActiveWorkspace(): string | null {
  return useSyncExternalStore(subscribeWorkspace, () => workspace.name, () => null);
}

// ── Entries ──────────────────────────────────────────────────────

function storageKey(key: string) {
  return STORAGE_PREFIX + key;
}

function readStored(key: string): { data: unknown; updatedAt: number } | null {
  try {
    const raw = sessionStorage.getItem(storageKey(key));
    if (!raw) return null;
    const stored = JSON.parse(raw);
    return stored && typeof stored.updatedAt === "number" ? stored : null;
  } catch {
    return null;
  }
}

function writeStored(key: string, data: unknown, updatedAt: number) {
  try {
    const raw = JSON.stringify({ data, updatedAt });
    if (raw.length <= MAX_STORED) sessionStorage.setItem(storageKey(key), raw);
    else sessionStorage.removeItem(storageKey(key));
  } catch {
    // Storage full or blocked: the answer stays in memory.
  }
}

function entryFor(key: string, options: CachedOptions<unknown>): Entry {
  let entry = entries.get(key);
  if (!entry) {
    const stored = options.persist === false ? null : readStored(key);
    entry = {
      key, data: stored?.data, error: undefined, updatedAt: stored?.updatedAt ?? null,
      fetching: false, promise: null, fetcher: null, options, listeners: new Set(),
      snapshot: EMPTY, timer: undefined, lastDataChange: 0, fetched: false,
    };
    entries.set(key, entry);
    publish(entry, false);
  }
  return entry;
}

function publish(entry: Entry, notify = true) {
  const has = entry.data !== undefined;
  entry.snapshot = {
    data: entry.data,
    error: entry.error,
    loading: !has && (entry.fetching || entry.error === undefined),
    refreshing: has && entry.fetching,
    updatedAt: entry.updatedAt,
  };
  if (notify) entry.listeners.forEach((listener) => listener());
}

function schedule(entry: Entry, ms: number) {
  clearTimeout(entry.timer);
  entry.timer = setTimeout(() => { if (entry.listeners.size) void revalidate(entry); }, ms);
}

function revalidate(entry: Entry, ctx: FetchContext = { fresh: false }): Promise<void> {
  if (entry.promise && !ctx.fresh) return entry.promise;
  const fetcher = entry.fetcher;
  if (!fetcher) return Promise.resolve();
  clearTimeout(entry.timer);
  entry.fetching = true;
  publish(entry);
  const request = fetcher(ctx)
    .then((data) => {
      if (entry.promise !== request) return; // A newer request replaced this one.
      const options = entry.options;
      const owner = options.workspaceOf?.(data);
      const scope = options.global ? null : entry.key.slice(0, entry.key.indexOf("\u0000"));
      if (scope !== null && owner != null && owner !== scope) {
        // The workspace changed under this tab: the answer is another
        // workspace's. Learn the new one; its own key fetches afresh.
        void resolveWorkspace();
        return;
      }
      entry.data = data;
      entry.error = undefined;
      entry.updatedAt = Date.now();
      entry.fetched = true;
      if (options.persist !== false) writeStored(entry.key, data, entry.updatedAt);
      if (options.isStale?.(data)) schedule(entry, options.staleRetryMs ?? 2000);
    })
    .catch((cause: unknown) => {
      if (entry.promise !== request) return;
      entry.error = cause instanceof Error ? cause : new Error(String(cause));
      const retry = entry.options.retryMs ?? 3000;
      if (retry > 0 && !(cause instanceof ApiError)) schedule(entry, retry);
    })
    .finally(() => {
      if (entry.promise !== request) return;
      entry.promise = null;
      entry.fetching = false;
      publish(entry);
    });
  entry.promise = request;
  return request;
}

function mounted() {
  return [...entries.values()].filter((entry) => entry.listeners.size > 0);
}

/** Fetch again every mounted key that starts with `prefix` (every key without one),
 * e.g. after a write whose effects the page cannot compute itself. */
export function revalidateCached(prefix = ""): Promise<void> {
  return Promise.all(mounted()
    .filter((entry) => entry.key.slice(entry.key.indexOf("\u0000") + 1).startsWith(prefix))
    .map((entry) => revalidate(entry))).then(() => undefined);
}

// ── Shared triggers: focus and data changes ─────────────────────

let focusBound = false;
function bindFocus() {
  if (focusBound || typeof window === "undefined") return;
  focusBound = true;
  const onFocus = () => {
    if (document.visibilityState !== "visible") return;
    void resolveWorkspace();
    for (const entry of mounted()) {
      if (entry.options.revalidateOnFocus !== false) void revalidate(entry);
    }
  };
  window.addEventListener("focus", onFocus);
  document.addEventListener("visibilitychange", onFocus);
}

let stream: EventSource | null = null;
let streamUsers = 0;
function subscribeDataChanges() {
  streamUsers += 1;
  if (!stream && typeof EventSource !== "undefined") {
    stream = connectSSE(() => {
      const now = Date.now();
      for (const entry of mounted()) {
        if (!entry.options.revalidateOnDataChange) continue;
        const wait = entry.options.dataChangeMs ?? 5000;
        if (now - entry.lastDataChange < wait) continue;
        entry.lastDataChange = now;
        void revalidate(entry);
      }
    });
  }
  return () => {
    streamUsers -= 1;
    if (streamUsers === 0) { stream?.close(); stream = null; }
  };
}

// ── The hook ────────────────────────────────────────────────────

/**
 * The cached answer for `key`, fetched with `fetcher`; `null` as the key
 * waits (for a selection, say). The fetcher may change between renders;
 * the latest one is used.
 */
export function useCached<T>(
  key: string | null,
  fetcher: (ctx: FetchContext) => Promise<T>,
  options: CachedOptions<T> = {},
): Cached<T> {
  const ws = useActiveWorkspace();
  const workspaceError = useSyncExternalStore(subscribeWorkspace, () => workspace.error, () => undefined);
  const scoped = key === null ? null : options.global ? `\u0000${key}` : ws === null ? null : `${ws}\u0000${key}`;
  const latest = useRef({ fetcher, options });
  useEffect(() => { latest.current = { fetcher, options }; });

  const subscribe = useCallback((listener: () => void) => {
    if (!scoped) return () => {};
    const entry = entryFor(scoped, latest.current.options as CachedOptions<unknown>);
    entry.listeners.add(listener);
    return () => { entry.listeners.delete(listener); if (!entry.listeners.size) clearTimeout(entry.timer); };
  }, [scoped]);
  const snapshot = useSyncExternalStore(
    subscribe,
    () => (scoped ? entryFor(scoped, latest.current.options as CachedOptions<unknown>).snapshot : EMPTY),
    () => EMPTY,
  ) as Snapshot<T>;

  // Mount, key change and interval.
  useEffect(() => {
    if (!scoped) return;
    bindFocus();
    const entry = entryFor(scoped, latest.current.options as CachedOptions<unknown>);
    entry.fetcher = (ctx) => latest.current.fetcher(ctx);
    entry.options = latest.current.options as CachedOptions<unknown>;
    const dedupe = entry.options.dedupeMs ?? 2000;
    const recent = entry.fetched && entry.updatedAt !== null && Date.now() - entry.updatedAt < dedupe && !entry.error;
    if (!recent || entry.options.isStale?.(entry.data)) void revalidate(entry);
    const every = entry.options.refreshInterval ?? 0;
    if (!every) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void revalidate(entry);
    }, every);
    return () => clearInterval(timer);
  }, [scoped]);

  // Data changes reported by the API.
  const onData = options.revalidateOnDataChange === true && scoped !== null;
  useEffect(() => (onData ? subscribeDataChanges() : undefined), [onData]);

  // A job that starts or ends usually changes what pages show.
  const queue = useQueue();
  const jobMark = options.revalidateOnJobs === false || !queue
    ? null
    : `${queue.active?.id ?? ""}|${queue.recent?.[0]?.id ?? ""}|${queue.recent?.[0]?.status ?? ""}`;
  const lastMark = useRef<string | null>(null);
  useEffect(() => {
    if (jobMark === null) return;
    const previous = lastMark.current;
    lastMark.current = jobMark;
    if (previous === null || previous === jobMark || !scoped) return;
    const entry = entries.get(scoped);
    if (entry) void revalidate(entry);
  }, [jobMark, scoped]);

  const refresh = useCallback(async () => {
    if (!scoped) { await resolveWorkspace(); return; }
    await revalidate(entryFor(scoped, latest.current.options as CachedOptions<unknown>), { fresh: true });
  }, [scoped]);
  const mutate = useCallback((data: T) => {
    if (!scoped) return;
    const entry = entryFor(scoped, latest.current.options as CachedOptions<unknown>);
    entry.data = data;
    entry.error = undefined;
    entry.updatedAt = Date.now();
    if (entry.options.persist !== false) writeStored(scoped, data, entry.updatedAt);
    publish(entry);
  }, [scoped]);

  if (!scoped && key !== null && workspaceError) {
    return { ...EMPTY, error: workspaceError, refresh, mutate };
  }
  if (!scoped && key !== null) return { ...EMPTY, loading: true, refresh, mutate };
  return { ...snapshot, refresh, mutate };
}
