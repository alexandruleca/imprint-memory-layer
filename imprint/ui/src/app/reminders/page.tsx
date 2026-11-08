"use client";

import { Suspense, useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { AlarmClock, Bell, Clock, MessageSquare, Pencil, Plus, Terminal, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/split-page";
import { openChat } from "@/components/reminder-alerts";
import { groupByTime } from "@/lib/time-groups";
import { useCached } from "@/lib/use-cached";
import {
  alertDescription, cancelEntry, countdown, createEntry, doneEntry, getSchedule, JOB_COMMANDS, onScheduleChange,
  snoozeEntry, SNOOZE_MINUTES, updateEntry, useAlerts, viewerZone,
  type Alert, type NewEntry, type ScheduleEntry, type ScheduleKind, type SchedulePage,
} from "@/lib/schedule";

const DESCRIPTION = "Reminders, timers, scheduled jobs and questions for Imprint. Set them here, or ask in Chat or Echo: “remind me at 10 to call Dan”, “every weekday at 9 re-index my code folder”. They stay on this device.";
const KIND_LABEL: Record<ScheduleKind, string> = { reminder: "Reminder", timer: "Timer", job: "Job", prompt: "Question" };
const KIND_ICON: Record<ScheduleKind, typeof Bell> = { reminder: Bell, timer: Clock, job: Terminal, prompt: MessageSquare };
const STATUS_LABEL: Record<ScheduleEntry["status"], string> = { scheduled: "Scheduled", fired: "Rang", done: "Done", cancelled: "Cancelled", missed: "Missed" };
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
const SELECT = "h-8 w-full rounded-md border border-input bg-background px-2 text-sm";

/** The clock, ticking once a second while something counts down. */
function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

async function attempt<T>(action: () => Promise<T>, failure: string) {
  try { return await action(); }
  catch (e) { toast.error(failure, { description: e instanceof Error ? e.message : String(e) }); return null; }
}

function localTime(epoch: number) {
  return new Date(epoch * 1000).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** Upcoming entries by day, soonest first: Today, Tomorrow, This week, Later. */
function upcomingGroups(entries: ScheduleEntry[], now: Date) {
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const today = day(now), oneDay = 86_400_000;
  const groups: { label: string; items: ScheduleEntry[] }[] = [];
  for (const entry of entries) {
    const at = day(new Date(entry.due_at * 1000));
    const label = at <= today ? "Today" : at === today + oneDay ? "Tomorrow" : at < today + 7 * oneDay ? "This week" : "Later";
    if (groups.at(-1)?.label === label) groups.at(-1)!.items.push(entry);
    else groups.push({ label, items: [entry] });
  }
  return groups;
}

// ── Editing ──────────────────────────────────────────────────

type Draft = {
  kind: ScheduleKind; title: string; message: string; question: string; command: string; folder: string;
  mode: "at" | "in"; at: string; in: string; repeat: "none" | "daily" | "weekdays" | "weekly" | "interval"; days: string[]; every: string;
};

function isoLocal(epoch: number) {
  const d = new Date(epoch * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function draftOf(entry?: ScheduleEntry): Draft {
  const blank: Draft = { kind: "reminder", title: "", message: "", question: "", command: "refresh", folder: "", mode: "in", at: isoLocal(Date.now() / 1000 + 3600),
    in: "10m", repeat: "none", days: [], every: "1h" };
  if (!entry) return blank;
  const rule = entry.recurrence;
  return {
    ...blank, kind: entry.kind, title: entry.title, message: entry.message, question: entry.payload.question ?? "",
    command: entry.payload.command ?? "refresh", folder: entry.payload.dir ?? "", mode: entry.kind === "timer" ? "in" : "at", at: isoLocal(entry.due_at),
    repeat: rule ? rule.every : "none", days: rule?.every === "weekly" ? rule.days.map((d) => DAYS[d]) : [],
    every: rule?.every === "interval" ? `${Math.round(rule.seconds / 60)}m` : blank.every,
  };
}

function requestOf(draft: Draft, editing: boolean): NewEntry {
  const recurrence = draft.repeat === "none" ? "none" : draft.repeat === "weekly" ? `weekly:${(draft.days.length ? draft.days : []).join(",")}`
    : draft.repeat === "interval" ? `every ${draft.every}` : draft.repeat;
  const when = draft.kind === "timer" || draft.mode === "in" ? { in: draft.in } : { at: draft.at };
  const body: NewEntry = { kind: draft.kind, title: draft.title, ...when, timezone: viewerZone() };
  if (draft.kind !== "timer") body.recurrence = recurrence;
  if (draft.kind === "reminder") body.message = draft.message;
  if (draft.kind === "prompt") body.payload = { question: draft.question };
  if (draft.kind === "job") {
    const job = JOB_COMMANDS.find((c) => c.value === draft.command);
    body.payload = { command: draft.command, ...(job?.folder ? { dir: draft.folder } : {}) };
  }
  if (editing) delete (body as Partial<NewEntry>).kind;
  return body;
}

function EntryDialog({ entry, open, onClose }: { entry?: ScheduleEntry; open: boolean; onClose: () => void }) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(entry));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const set = (change: Partial<Draft>) => setDraft((d) => ({ ...d, ...change }));
  const job = JOB_COMMANDS.find((c) => c.value === draft.command);

  async function save() {
    setSaving(true);
    setError("");
    try {
      const saved = entry ? await updateEntry(entry.id, requestOf(draft, true)) : await createEntry(requestOf(draft, false));
      toast.success(`${KIND_LABEL[saved.kind]} ${entry ? "changed" : "set"}`, { description: `${saved.when}${saved.repeat ? ` · ${saved.repeat}` : ""}${saved.note ? ` · ${saved.note}` : ""}` });
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader><DialogTitle>{entry ? `Edit ${KIND_LABEL[entry.kind].toLowerCase()}` : "New reminder"}</DialogTitle></DialogHeader>
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); void save(); }}>
          {!entry && (
            <label className="block space-y-1 text-sm">What
              <select aria-label="Kind" className={SELECT} value={draft.kind} onChange={(e) => set({ kind: e.target.value as ScheduleKind, mode: e.target.value === "timer" ? "in" : draft.mode })}>
                <option value="reminder">Reminder</option>
                <option value="timer">Timer</option>
                <option value="job">Scheduled job</option>
                <option value="prompt">Question for Imprint</option>
              </select>
            </label>
          )}
          {draft.kind === "reminder" && (
            <label className="block space-y-1 text-sm">Remind me to
              <Input aria-label="Message" value={draft.message} maxLength={2000} placeholder="Call Dan" onChange={(e) => set({ message: e.target.value })} />
            </label>
          )}
          {draft.kind === "prompt" && (
            <label className="block space-y-1 text-sm">Question
              <Textarea aria-label="Question" value={draft.question} maxLength={2000} placeholder="Summarize what I worked on yesterday" onChange={(e) => set({ question: e.target.value })} />
            </label>
          )}
          {draft.kind === "job" && (
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block space-y-1 text-sm">Job
                <select aria-label="Job" className={SELECT} value={draft.command} onChange={(e) => set({ command: e.target.value })}>
                  {JOB_COMMANDS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              </label>
              {job?.folder && (
                <label className="block space-y-1 text-sm">Folder
                  <Input aria-label="Folder" value={draft.folder} placeholder="/home/me/code" onChange={(e) => set({ folder: e.target.value })} />
                </label>
              )}
            </div>
          )}
          <label className="block space-y-1 text-sm">{draft.kind === "reminder" ? "Title (optional)" : "Name (optional)"}
            <Input aria-label="Title" value={draft.title} maxLength={200} onChange={(e) => set({ title: e.target.value })} />
          </label>
          {draft.kind === "timer" ? (
            <label className="block space-y-1 text-sm">Duration
              <Input aria-label="Duration" value={draft.in} placeholder="10m, 1h30m" onChange={(e) => set({ in: e.target.value })} />
            </label>
          ) : (
            <div className="grid gap-3 sm:grid-cols-[auto_1fr]">
              <label className="block space-y-1 text-sm">When
                <select aria-label="When" className={SELECT} value={draft.mode} onChange={(e) => set({ mode: e.target.value as Draft["mode"] })}>
                  <option value="at">At a time</option>
                  <option value="in">In…</option>
                </select>
              </label>
              {draft.mode === "at" ? (
                <label className="block space-y-1 text-sm">Date and time
                  <Input aria-label="Date and time" type="datetime-local" value={draft.at} onChange={(e) => set({ at: e.target.value })} />
                </label>
              ) : (
                <label className="block space-y-1 text-sm">From now
                  <Input aria-label="From now" value={draft.in} placeholder="10m, 2h" onChange={(e) => set({ in: e.target.value })} />
                </label>
              )}
            </div>
          )}
          {draft.kind !== "timer" && (
            <div className="space-y-2">
              <label className="block space-y-1 text-sm">Repeat
                <select aria-label="Repeat" className={SELECT} value={draft.repeat} onChange={(e) => set({ repeat: e.target.value as Draft["repeat"] })}>
                  <option value="none">Does not repeat</option>
                  <option value="daily">Every day</option>
                  <option value="weekdays">Every weekday</option>
                  <option value="weekly">Every week on…</option>
                  <option value="interval">Every…</option>
                </select>
              </label>
              {draft.repeat === "weekly" && (
                <div className="flex flex-wrap gap-1" role="group" aria-label="Days">
                  {DAYS.map((d) => (
                    <Button key={d} type="button" size="xs" variant={draft.days.includes(d) ? "default" : "outline"} aria-pressed={draft.days.includes(d)}
                      onClick={() => set({ days: draft.days.includes(d) ? draft.days.filter((x) => x !== d) : [...draft.days, d] })}>
                      {d[0].toUpperCase() + d.slice(1)}
                    </Button>
                  ))}
                </div>
              )}
              {draft.repeat === "interval" && (
                <Input aria-label="Repeat every" value={draft.every} placeholder="30m, 2h (at least 5m)" onChange={(e) => set({ every: e.target.value })} />
              )}
            </div>
          )}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={saving}>{saving ? "Saving…" : entry ? "Save" : "Set"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ── Rows ─────────────────────────────────────────────────────

function AlertRow({ alert }: { alert: Alert }) {
  const router = useRouter();
  const Icon = KIND_ICON[alert.kind];
  const snoozable = alert.kind === "reminder" || alert.kind === "timer";
  return (
    <li className="flex flex-wrap items-center gap-2 px-4 py-3 sm:px-6" aria-label={alert.title}>
      <Icon className="size-4 text-primary" aria-hidden />
      <span className="font-medium">{alert.title}</span>
      <span className="text-xs text-muted-foreground">{alertDescription(alert) || `Due ${alert.when}`}</span>
      <div className="ml-auto flex flex-wrap items-center gap-1">
        {alert.outcome.chat && <Button size="sm" variant="ghost" onClick={() => { void doneEntry(alert.entry_id).catch(() => {}); openChat(alert.outcome.chat!, router.push); }}>Open chat</Button>}
        {snoozable && SNOOZE_MINUTES.map((minutes) => (
          <Button key={minutes} size="sm" variant="outline" aria-label={`Snooze ${alert.title} ${minutes} minutes`}
            onClick={() => void attempt(() => snoozeEntry(alert.entry_id, minutes), "Could not snooze")}>{minutes} min</Button>
        ))}
        <Button size="sm" onClick={() => void attempt(() => doneEntry(alert.entry_id), "Could not mark it done")}>Done</Button>
      </div>
    </li>
  );
}

function EntryRow({ entry, now, highlighted, onEdit }: { entry: ScheduleEntry; now: number; highlighted: boolean; onEdit?: (e: ScheduleEntry) => void }) {
  const Icon = KIND_ICON[entry.kind];
  const left = entry.due_at - now / 1000;
  const detail = entry.kind === "reminder" ? entry.message : entry.kind === "prompt" ? entry.payload.question : entry.kind === "job" ? entry.payload.dir : "";
  return (
    <li id={`entry-${entry.id}`} aria-label={entry.title}
      className={`flex flex-wrap items-center gap-2 px-4 py-3 sm:px-6 ${highlighted ? "bg-accent/60 ring-1 ring-primary/40" : ""}`}>
      <Icon className="size-4 text-muted-foreground" aria-hidden />
      <span className="font-medium">{entry.title}</span>
      <Badge variant="outline">{KIND_LABEL[entry.kind]}</Badge>
      {entry.status !== "scheduled" && <Badge variant={entry.status === "missed" ? "destructive" : "secondary"}>{STATUS_LABEL[entry.status]}</Badge>}
      <span className="text-xs text-muted-foreground">
        {entry.status === "scheduled"
          ? <>{localTime(entry.due_at)}{entry.repeat && ` · ${entry.repeat}`}</>
          : <>{entry.last_fired_at ? `Rang ${localTime(entry.last_fired_at)}` : `Was due ${localTime(entry.due_at)}`}</>}
        {` · from ${entry.created_by}`}
        {detail && entry.kind !== "reminder" ? ` · ${detail}` : ""}
      </span>
      {entry.status === "scheduled" && entry.kind === "timer" && (
        <span className="font-mono text-sm tabular-nums" role="timer" aria-label={`${entry.title} remaining`}>{countdown(left)}</span>
      )}
      {entry.kind === "reminder" && entry.message && entry.message !== entry.title && <p className="basis-full pl-6 text-sm text-muted-foreground">{entry.message}</p>}
      {entry.status === "scheduled" && (
        <div className="ml-auto flex gap-1">
          {onEdit && <Button size="sm" variant="ghost" aria-label={`Edit ${entry.title}`} onClick={() => onEdit(entry)}><Pencil /></Button>}
          <Button size="sm" variant="ghost" aria-label={`Cancel ${entry.title}`} onClick={() => void attempt(() => cancelEntry(entry.id), "Could not cancel it")}><X /></Button>
        </div>
      )}
    </li>
  );
}

/** A list read a page at a time: the first page cached, more on request. */
function usePaged(view: "upcoming" | "past") {
  const first = useCached(`schedule:${view}`, () => getSchedule(view), { global: true, refreshInterval: 15_000 });
  const [more, setMore] = useState<{ after: string | null; pages: SchedulePage[] }>({ after: null, pages: [] });
  const { refresh } = first;
  useEffect(() => onScheduleChange(() => { setMore({ after: null, pages: [] }); void refresh(); }), [refresh]);
  const pages = first.data ? [first.data, ...(more.after === first.data.next ? more.pages : [])] : [];
  const next = pages.at(-1)?.next ?? null;
  const entries = pages.flatMap((p) => p.entries);
  const loadMore = useCallback(async () => {
    if (!next || !first.data) return;
    const page = await attempt(() => getSchedule(view, next), "Could not load more");
    if (page) setMore((m) => ({ after: first.data!.next, pages: [...(m.after === first.data!.next ? m.pages : []), page] }));
  }, [next, first.data, view]);
  return { entries, total: first.data?.total, loading: first.loading, error: first.error, next, loadMore, refresh };
}

function RemindersContent() {
  const params = useSearchParams();
  const highlight = params.get("entry");
  const alerts = useAlerts();
  const upcoming = usePaged("upcoming");
  const past = usePaged("past");
  const [editing, setEditing] = useState<{ entry?: ScheduleEntry } | null>(null);
  const timing = upcoming.entries.some((e) => e.kind === "timer");
  const now = useNow(timing);
  const groups = useMemo(() => upcomingGroups(upcoming.entries, new Date(now)), [upcoming.entries, now]);
  const pastGroups = useMemo(() => groupByTime(past.entries, (e) => (e.ended_at ?? e.updated_at) * 1000), [past.entries]);
  // A timer that ends, or an alert that rings, moves its entry: read the lists again.
  const alertMark = alerts?.map((a) => `${a.id}:${a.ring}`).join(",") ?? "";
  const { refresh: refreshUpcoming } = upcoming;
  const { refresh: refreshPast } = past;
  useEffect(() => { if (alertMark) { void refreshUpcoming(); void refreshPast(); } }, [alertMark, refreshUpcoming, refreshPast]);
  useEffect(() => {
    if (highlight) document.getElementById(`entry-${highlight}`)?.scrollIntoView({ block: "center" });
  }, [highlight, upcoming.entries.length, past.entries.length]);

  return (
    <div className="mx-auto max-w-4xl pb-10">
      <PageHeader title="Reminders" count={upcoming.total} description={DESCRIPTION} updating={false}
        actions={<Button onClick={() => setEditing({})}><Plus /> New</Button>} />
      <div className="space-y-6 px-4 sm:px-8">
        {alerts && alerts.length > 0 && (
          <section aria-labelledby="ringing" className="space-y-2">
            <h2 id="ringing" className="flex items-center gap-2 text-sm font-semibold"><AlarmClock className="size-4 text-primary" /> Now</h2>
            <Card className="gap-0 py-0"><ul className="divide-y divide-border">{alerts.map((a) => <AlertRow key={`${a.id}:${a.ring}`} alert={a} />)}</ul></Card>
          </section>
        )}
        <section aria-labelledby="upcoming" className="space-y-2">
          <h2 id="upcoming" className="text-sm font-semibold">Upcoming</h2>
          {upcoming.error && <p role="alert" className="text-sm text-destructive">Could not load reminders: {upcoming.error.message}</p>}
          {upcoming.loading ? <Skeleton className="h-24 w-full rounded-xl" /> : upcoming.entries.length ? (
            <div className="space-y-3">
              {groups.map((group) => (
                <div key={group.label} className="space-y-1">
                  <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{group.label}</h3>
                  <Card className="gap-0 py-0"><ul className="divide-y divide-border">
                    {group.items.map((e) => <EntryRow key={e.id} entry={e} now={now} highlighted={e.id === highlight} onEdit={(entry) => setEditing({ entry })} />)}
                  </ul></Card>
                </div>
              ))}
              {upcoming.next && <Button variant="outline" size="sm" onClick={() => void upcoming.loadMore()}>Show more</Button>}
            </div>
          ) : (
            <Empty className="border border-dashed border-border">
              <EmptyHeader>
                <EmptyTitle>Nothing scheduled</EmptyTitle>
                <EmptyDescription>Set a reminder or timer with New, or ask Imprint in Chat or Echo.</EmptyDescription>
              </EmptyHeader>
            </Empty>
          )}
        </section>
        {past.entries.length > 0 && (
          <section aria-labelledby="past" className="space-y-2">
            <h2 id="past" className="text-sm font-semibold">Past</h2>
            {pastGroups.map((group) => (
              <div key={group.key} className="space-y-1">
                <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{group.label}</h3>
                <Card className="gap-0 py-0"><ul className="divide-y divide-border">
                  {group.items.map((e) => <EntryRow key={e.id} entry={e} now={now} highlighted={e.id === highlight} />)}
                </ul></Card>
              </div>
            ))}
            {past.next && <Button variant="outline" size="sm" onClick={() => void past.loadMore()}>Show more</Button>}
          </section>
        )}
      </div>
      {editing && <EntryDialog key={editing.entry?.id ?? "new"} entry={editing.entry} open onClose={() => setEditing(null)} />}
    </div>
  );
}

export default function RemindersPage() {
  const mounted = useSyncExternalStore(() => () => {}, () => true, () => false);
  return mounted ? <Suspense fallback={null}><RemindersContent /></Suspense> : null;
}
