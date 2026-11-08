import { useSyncExternalStore } from "react";
import { API_BASE } from "./api-base";
import { ApiError } from "./api";

// Reminders, timers and scheduled jobs (ADR 0158). The entries live on this
// device; the API's scheduler fires them and each firing is an alert the
// dashboard shows until the user marks it done or snoozes it.

export type ScheduleKind = "reminder" | "timer" | "job" | "prompt";
export type ScheduleStatus = "scheduled" | "fired" | "done" | "cancelled" | "missed";
export type Recurrence =
  | { every: "daily" | "weekdays"; time: string }
  | { every: "weekly"; days: number[]; time: string }
  | { every: "interval"; seconds: number };

export type Alert = {
  id: string;
  entry_id: string;
  kind: ScheduleKind;
  title: string;
  message: string;
  occurrence_at: number;
  fired_at: number;
  alerted_at: number;
  late: boolean;
  missed: boolean;
  ring: number;
  repeats: boolean;
  when: string;
  outcome: { job_id?: string; merged?: boolean; chat?: string; error?: string; skipped?: string };
};

export type ScheduleEntry = {
  id: string;
  kind: ScheduleKind;
  title: string;
  message: string;
  due_at: number;
  timezone: string;
  recurrence: Recurrence | null;
  /** "Every weekday at 09:00", or "" when it does not repeat. */
  repeat: string;
  /** The due time as the server reads it in the entry's zone, e.g. "tomorrow, Mon 9 Nov 10:00". */
  when: string;
  workspace: string;
  payload: { command?: string; dir?: string; question?: string; seconds?: number; [key: string]: unknown };
  status: ScheduleStatus;
  created_by: "chat" | "voice" | "dashboard";
  created_at: number;
  updated_at: number;
  last_fired_at: number | null;
  snoozed_until: number | null;
  ended_at: number | null;
  alert: (Omit<Alert, "kind" | "title" | "message" | "repeats" | "when"> & { state: "pending" | "snoozed"; snoozed_until: number | null }) | null;
  note?: string;
};

export type SchedulePage = { entries: ScheduleEntry[]; next: string | null; total: number };
export type NewEntry = {
  kind: ScheduleKind;
  title?: string;
  message?: string;
  at?: string;
  in?: string;
  recurrence?: string | Recurrence | null;
  payload?: Record<string, unknown>;
  timezone?: string;
};

export const JOB_COMMANDS: { value: string; label: string; folder?: boolean }[] = [
  { value: "refresh", label: "Re-index a folder (changed files)", folder: true },
  { value: "ingest", label: "Index a folder", folder: true },
  { value: "learn", label: "Learn conversations" },
  { value: "organize", label: "Organize memories" },
  { value: "refresh-urls", label: "Refresh URLs" },
  { value: "retag", label: "Retag memories" },
];
export const SNOOZE_MINUTES = [5, 10, 30] as const;

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}/api/schedule${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new ApiError(detail.error || `Request failed (${res.status})`, detail);
  }
  return res.json();
}

export function getSchedule(view: "upcoming" | "past", before?: string | null, limit = 25) {
  const params = new URLSearchParams({ view, limit: String(limit) });
  if (before) params.set("before", before);
  return call<SchedulePage>(`?${params}`);
}
export function createEntry(entry: NewEntry) {
  return call<ScheduleEntry>("", { method: "POST", body: JSON.stringify(entry) }).then(changed);
}
export function updateEntry(id: string, changes: Partial<NewEntry>) {
  return call<ScheduleEntry>(`/${id}`, { method: "PATCH", body: JSON.stringify(changes) }).then(changed);
}
export function snoozeEntry(id: string, minutes: number) {
  return call<ScheduleEntry>(`/${id}/snooze`, { method: "POST", body: JSON.stringify({ minutes }) }).then(changed);
}
export function doneEntry(id: string) {
  return call<ScheduleEntry>(`/${id}/done`, { method: "POST" }).then(changed);
}
export function cancelEntry(id: string) {
  return call<ScheduleEntry>(`/${id}/cancel`, { method: "POST" }).then(changed);
}

/** The viewer's time zone, which entries made on the dashboard keep. */
export function viewerZone(): string {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { return ""; }
}

/** "4:59", "1:02:03": what is left of a timer. */
export function countdown(seconds: number): string {
  const s = Math.max(0, Math.ceil(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(r)}` : `${m}:${pad(r)}`;
}

// ── Alerts: one poll for every surface (toasts, the Reminders page) ──

const POLL_MS = 3000;
type AlertsSnapshot = { alerts: Alert[]; version: number } | null;
let snapshot: AlertsSnapshot = null;
let version = 0;
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | undefined;

export async function refreshAlerts() {
  try {
    const data = await call<{ alerts: Alert[] }>("/alerts");
    snapshot = Array.isArray(data?.alerts) ? { alerts: data.alerts, version: ++version } : snapshot;
  } catch {
    // The heartbeat reports an unreachable service; keep what was shown.
  }
  listeners.forEach((listener) => listener());
}

/** Something changed the schedule: alerts and lists read it again. */
function changed<T>(value: T): T {
  void refreshAlerts();
  scheduleListeners.forEach((listener) => listener());
  return value;
}
const scheduleListeners = new Set<() => void>();
export function onScheduleChange(listener: () => void) {
  scheduleListeners.add(listener);
  return () => { scheduleListeners.delete(listener); };
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) {
    void refreshAlerts();
    timer = setInterval(refreshAlerts, POLL_MS);
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) clearInterval(timer);
  };
}

/** The alerts waiting for the user, latest first; null until the first answer. */
export function useAlerts(): Alert[] | null {
  return useSyncExternalStore(subscribe, () => snapshot, () => null)?.alerts ?? null;
}

/** The text an alert shows under its title. */
export function alertDescription(alert: Pick<Alert, "kind" | "message" | "late" | "missed" | "when" | "outcome">): string {
  const parts: string[] = [];
  if (alert.message && alert.kind === "reminder") parts.push(alert.message);
  if (alert.outcome.error) parts.push(`Failed: ${alert.outcome.error}`);
  else if (alert.missed) parts.push(`Missed (was due ${alert.when}) while Imprint was not running; not run late.`);
  else if (alert.kind === "job") parts.push(alert.outcome.merged ? "Joined the same job already waiting." : "Queued.");
  else if (alert.kind === "prompt") parts.push("The answer is in a new chat.");
  if (alert.late && !alert.missed) parts.push(`Late: was due ${alert.when}.`);
  return parts.join(" ");
}
