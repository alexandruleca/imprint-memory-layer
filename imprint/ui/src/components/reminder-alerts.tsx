"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { alertDescription, doneEntry, snoozeEntry, SNOOZE_MINUTES, useAlerts, type Alert } from "@/lib/schedule";

const LAST_CHAT = "imprint.chat.last";
const NOTIFY_WITHIN_S = 120;

/** Opens the chat a scheduled question was answered in: the Chat page
 * reopens the chat this browser was last in. */
export function openChat(id: string, push: (href: string) => void) {
  try { localStorage.setItem(LAST_CHAT, id); } catch {}
  push("/chat/");
}

function toastId(alert: Pick<Alert, "id" | "ring">) {
  return `reminder-${alert.id}-${alert.ring}`;
}

const TITLES: Record<Alert["kind"], string> = { reminder: "Reminder", timer: "Timer done", job: "Scheduled job", prompt: "Scheduled question" };

/** Shows each alert the scheduler raises (ADR 0158) as a toast, whichever
 * page is open, and in the desktop app as a native notification too. A
 * reminder or timer stays until Done or Snooze; a job or question is
 * reported once. Clicking the notification opens the Reminders page. */
export function ReminderAlerts() {
  const router = useRouter();
  const alerts = useAlerts();
  const shown = useRef(new Set<string>());

  useEffect(() => window.electronAPI?.onReminderOpen?.((id) => router.push(`/reminders/?entry=${encodeURIComponent(id)}`)), [router]);

  useEffect(() => {
    if (!alerts) return;
    const pending = new Set(alerts.map(toastId));
    for (const id of shown.current) {
      if (!pending.has(id)) { toast.dismiss(id); shown.current.delete(id); }
    }
    for (const alert of [...alerts].reverse()) {
      const id = toastId(alert);
      if (shown.current.has(id)) continue;
      shown.current.add(id);
      announce(alert, id, router.push);
      // A reload shows what still waits as toasts; the desktop notifies only what just rang.
      if (Date.now() / 1000 - alert.alerted_at < NOTIFY_WITHIN_S) {
        window.electronAPI?.notifyReminder?.({ id: alert.entry_id, title: `${TITLES[alert.kind]}: ${alert.title}`, body: alertDescription(alert) });
      }
    }
  }, [alerts, router]);

  return null;
}

function announce(alert: Alert, id: string, push: (href: string) => void) {
  const description = alertDescription(alert);
  const title = `${TITLES[alert.kind]}: ${alert.title}`;
  const finish = () => { void doneEntry(alert.entry_id).catch(() => {}); };
  if (alert.kind === "job" || alert.kind === "prompt") {
    // Reported once: closing it, or letting it close, puts it away.
    const chat = alert.outcome.chat;
    const show = alert.outcome.error || alert.missed ? toast.error : toast.success;
    show(title, {
      id, description, duration: 15_000, onDismiss: finish, onAutoClose: finish,
      action: chat ? { label: "Open chat", onClick: () => { finish(); openChat(chat, push); } }
        : alert.outcome.job_id ? { label: "Open queue", onClick: () => { finish(); push("/queue/"); } } : undefined,
    });
    return;
  }
  toast(title, {
    id,
    duration: Infinity,
    description: (
      <div className="mt-1 space-y-2">
        {description && <p>{description}</p>}
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label={`Snooze ${alert.title}`}>
          <span className="text-xs text-muted-foreground">Snooze</span>
          {SNOOZE_MINUTES.map((minutes) => (
            <Button key={minutes} size="xs" variant="outline" aria-label={`Snooze ${minutes} minutes`}
              onClick={() => { toast.dismiss(id); void snoozeEntry(alert.entry_id, minutes).catch((e) => toast.error("Could not snooze", { description: String(e?.message ?? e) })); }}>
              {minutes} min
            </Button>
          ))}
          <Button size="xs" className="ml-auto" onClick={() => { toast.dismiss(id); finish(); }}>Done</Button>
        </div>
      </div>
    ),
  });
}
