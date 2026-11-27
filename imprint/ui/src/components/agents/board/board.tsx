"use client";

import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { ChevronLeft, ChevronRight, ClipboardList, ListFilter, Plus, RotateCw, Search, X } from "lucide-react";
import { toast } from "sonner";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useNow } from "@/components/timestamp";
import {
  TASK_STATUSES, changeTask, getBoard, setAutonomy, type Autonomy, type BoardData, type BoardFilters, type BoardTask,
  type TaskStatus,
} from "@/lib/api";
import { useUiMode } from "@/lib/ui-mode";
import { useCached } from "@/lib/use-cached";
import { cn } from "@/lib/utils";
import { STATUS_HINTS, STATUS_LABELS } from "./format";
import { DispatcherStrip } from "./dispatcher-strip";
import { NewTaskDialog } from "./new-task-dialog";
import { ProgressPanel } from "./progress";
import { TaskCard } from "./task-card";
import { TaskDrawer } from "./task-drawer";

type Group = "none" | "project" | "feature";
/** The viewer's filters and grouping, and the ended columns they unfolded. */
type View = { filters: BoardFilters; group: Group; unfolded: TaskStatus[] };
const VIEW_KEY = "imprint.board.view";
const EMPTY_VIEW: View = { filters: {}, group: "none", unfolded: [] };
/** Ended columns start folded: they need the least attention and take the most room. */
const FOLDABLE: TaskStatus[] = ["done", "cancelled"];
const AUTONOMY_LABELS: Record<Autonomy, string> = { ready: "Standalone Ready and Stale tasks", stale: "Only standalone Stale tasks", off: "Nothing" };
const FILTER_NAMES: Record<string, string> = { project: "Project", feature: "Feature", tag: "Tag", priority: "Priority", assignee: "Assignee", holder: "Agent" };

/** What needs the user, or is stuck: each one a way to look at only those Tasks. */
const ATTENTION = {
  review: { label: "to review", tone: "", test: (t: BoardTask) => t.status === "review" },
  mine: { label: "assigned to you", tone: "", test: (t: BoardTask) => t.assignees.includes("user") && t.status !== "done" && t.status !== "cancelled" },
  stale: { label: "stale", tone: "text-destructive", test: (t: BoardTask) => t.stale },
  stalling: { label: "stalling", tone: "text-amber-700 dark:text-amber-300", test: (t: BoardTask) => t.stalling },
  blocked: { label: "blocked", tone: "text-destructive", test: (t: BoardTask) => !!t.blocked },
} as const;
type Attention = keyof typeof ATTENTION;

// The view is kept in this browser, and in memory too when storage is
// unavailable (a private window, blocked site data).
const viewListeners = new Set<() => void>();
let viewFallback: string | null = null;

function readView(): string {
  try { return window.localStorage.getItem(VIEW_KEY) ?? viewFallback ?? ""; } catch { return viewFallback ?? ""; }
}

function subscribeView(notify: () => void) {
  viewListeners.add(notify);
  window.addEventListener("storage", notify);
  return () => { viewListeners.delete(notify); window.removeEventListener("storage", notify); };
}

function writeView(view: View) {
  viewFallback = JSON.stringify(view);
  try { window.localStorage.setItem(VIEW_KEY, viewFallback); } catch { /* Kept in memory. */ }
  for (const notify of viewListeners) notify();
}

function parseView(raw: string): View {
  try {
    const saved = JSON.parse(raw);
    if (saved && typeof saved === "object") {
      return { filters: saved.filters ?? {}, group: saved.group ?? "none", unfolded: Array.isArray(saved.unfolded) ? saved.unfolded : [] };
    }
  } catch { /* Nothing kept yet. */ }
  return EMPTY_VIEW;
}

// The Task the address opens (/agents/?task=T-…#board): a notification or a
// link opens it, and opening or closing one updates the address.
const TASK_EVENT = "imprint:board-task";

function linkedTask(): string | null {
  return new URLSearchParams(window.location.search).get("task");
}

function subscribeTask(notify: () => void) {
  window.addEventListener("popstate", notify);
  window.addEventListener(TASK_EVENT, notify);
  return () => { window.removeEventListener("popstate", notify); window.removeEventListener(TASK_EVENT, notify); };
}

/** The workspace's Board: what needs the user, a column per status, cards
 * for its Tasks, and a drawer for one Task (ADR 0208). Agents change it
 * through their tools; here the user does, and it refreshes while they work. */
export function BoardView() {
  const { simple } = useUiMode();
  const now = useNow(30_000);
  const rawView = useSyncExternalStore(subscribeView, readView, () => "");
  const view = useMemo(() => parseView(rawView), [rawView]);
  const setView = (change: (current: View) => View) => writeView(change(parseView(readView())));
  // What the viewer is typing; the saved search until they type.
  const [typed, setText] = useState<string | null>(null);
  const text = typed ?? view.filters.text ?? "";
  useEffect(() => {
    if (typed === null) return;
    const timer = setTimeout(() => {
      const current = parseView(readView());
      const value = typed.trim() || undefined;
      if (current.filters.text !== value) writeView({ ...current, filters: { ...current.filters, text: value } });
    }, 250);
    return () => clearTimeout(timer);
  }, [typed]);
  const openTask = useSyncExternalStore(subscribeTask, linkedTask, () => null);

  const board = useCached<BoardData>(`tasks-board:${JSON.stringify(view.filters)}`, () => getBoard(view.filters), { refreshInterval: 5000 });
  const [creating, setCreating] = useState(false);
  const [attention, setAttention] = useState<Attention | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<TaskStatus | null>(null);

  const setFilter = (name: keyof BoardFilters, value: string) =>
    setView((v) => ({ ...v, filters: { ...v.filters, [name]: value || undefined } }));
  const clear = () => { setText(""); setAttention(null); setView((v) => ({ ...v, filters: {} })); };
  const fold = (status: TaskStatus, open: boolean) => setView((v) => ({
    ...v, unfolded: open ? [...new Set([...v.unfolded, status])] : v.unfolded.filter((s) => s !== status) }));

  function open(id: string | null) {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("task", id); else url.searchParams.delete("task");
    window.history.replaceState(null, "", url);
    window.dispatchEvent(new Event(TASK_EVENT));
  }

  async function move(task: BoardTask, status: TaskStatus, position?: number) {
    if (!board.data || (task.status === status && position === undefined)) return;
    const data = board.data;
    // Show it moved at once; the answer, or the next refresh, puts it right.
    const columns = Object.fromEntries(TASK_STATUSES.map((s) => [s, data.columns[s].filter((t) => t.id !== task.id)])) as BoardData["columns"];
    columns[status] = [...columns[status], { ...task, status, position: position ?? task.position }];
    board.mutate({ ...data, columns });
    try {
      await changeTask(task.id, "move", position === undefined ? { status } : { status, position });
      if (task.status !== status) toast.success(`${task.id} moved to ${STATUS_LABELS[status]}`);
    } catch (e) {
      board.mutate(data);
      const message = e instanceof Error ? e.message : String(e);
      toast.error(`${task.id} stays in ${STATUS_LABELS[task.status]}`, {
        description: message,
        action: message.includes("acceptance criterion") ? { label: "Add criteria", onClick: () => open(task.id) } : undefined,
      });
    } finally {
      void board.refresh();
    }
  }

  function drop(status: TaskStatus, before?: BoardTask) {
    const task = board.data && TASK_STATUSES.flatMap((s) => board.data!.columns[s]).find((t) => t.id === dragging);
    setDragging(null);
    setOver(null);
    if (!task) return;
    if (before && before.id !== task.id && before.status === status) {
      const column = board.data!.columns[status];
      const index = column.findIndex((t) => t.id === before.id);
      const place = (t: BoardTask, i: number) => t.position ?? i;
      const above = index > 0 ? place(column[index - 1], index - 1) : place(before, index) - 1;
      void move(task, status, (above + place(before, index)) / 2);
      return;
    }
    void move(task, status);
  }

  if (!board.data?.columns) {
    return (
      <div className="space-y-4 px-4 sm:px-8" aria-busy={!board.error}>
        {board.error ? (
          <Alert variant="destructive">
            <AlertTitle>Could not load the Board</AlertTitle>
            <AlertDescription>{board.error.message}. The dashboard keeps trying every few seconds.</AlertDescription>
            <AlertAction><Button variant="outline" size="sm" onClick={() => void board.refresh()}><RotateCw aria-hidden /> Retry</Button></AlertAction>
          </Alert>
        ) : (
          <div className="flex gap-3 overflow-hidden">{TASK_STATUSES.map((s) => <Skeleton key={s} className="h-64 w-64 shrink-0 rounded-xl" />)}</div>
        )}
      </div>
    );
  }

  const data = board.data;
  const options = data.options;
  const activeFilters = (Object.entries(view.filters) as [string, string][]).filter(([name, value]) => value && name in FILTER_NAMES);
  const filtered = activeFilters.length > 0 || !!view.filters.text;
  const nothingYet = data.total === 0 && !filtered && !view.filters.archived;
  const everything = TASK_STATUSES.flatMap((s) => data.columns[s]);
  const needs = (Object.keys(ATTENTION) as Attention[])
    .map((key) => ({ key, count: everything.filter(ATTENTION[key].test).length })).filter((n) => n.count > 0);
  const shownIn = (status: TaskStatus) => attention ? data.columns[status].filter(ATTENTION[attention].test) : data.columns[status];
  const featureName = (id: string) => options.features.find((f) => f.id === id)?.title ?? id;

  return (
    <div className="space-y-4 pb-10">
      {nothingYet ? (
        <div className="px-4 sm:px-8">
          <Empty className="border border-border bg-card">
            <EmptyHeader>
              <EmptyMedia variant="icon"><ClipboardList aria-hidden /></EmptyMedia>
              <EmptyTitle>No tasks on this workspace&apos;s Board yet.</EmptyTitle>
              <EmptyDescription>
                Agents put work here they cannot finish in one session, and claim, checkpoint and hand over Tasks as they go. Add
                one yourself, or ask an agent to plan work onto the Board.
              </EmptyDescription>
            </EmptyHeader>
            <EmptyContent><Button onClick={() => setCreating(true)}><Plus aria-hidden /> New task</Button></EmptyContent>
          </Empty>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 px-4 sm:px-8" role="toolbar" aria-label="Board filters">
            <InputGroup className="max-w-xs min-w-40 flex-1 bg-card">
              <InputGroupAddon><Search aria-hidden /></InputGroupAddon>
              <InputGroupInput aria-label="Find tasks by id, title, description, criteria or tag" placeholder="Find tasks"
                value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape" && text) { e.preventDefault(); setText(""); } }} />
              {text && <InputGroupAddon align="inline-end"><InputGroupButton size="icon-xs" aria-label="Clear the search" onClick={() => setText("")}><X aria-hidden /></InputGroupButton></InputGroupAddon>}
            </InputGroup>
            <Popover>
              <PopoverTrigger render={<Button variant="outline" size="sm" aria-label={`Filters${activeFilters.length ? `, ${activeFilters.length} on` : ""}`} />}>
                <ListFilter aria-hidden /> Filters
                {activeFilters.length > 0 && <Badge className="h-4 min-w-4 px-1 text-[10px] tabular-nums">{activeFilters.length}</Badge>}
              </PopoverTrigger>
              <PopoverContent align="start" className="grid w-72 max-w-[calc(100vw-2rem)] gap-3">
                <Filter label="Project" all="All projects" value={view.filters.project} onChange={(v) => setFilter("project", v)}
                  items={options.projects.map((p) => ({ value: p, label: p }))} />
                <Filter label="Feature" all="All Features" value={view.filters.feature} onChange={(v) => setFilter("feature", v)}
                  items={options.features.map((f) => ({ value: f.id, label: f.title }))} />
                <Filter label="Tag" all="All tags" value={view.filters.tag} onChange={(v) => setFilter("tag", v)}
                  items={options.tags.map((t) => ({ value: t, label: `#${t}` }))} />
                <Filter label="Priority" all="Any priority" value={view.filters.priority} onChange={(v) => setFilter("priority", v)}
                  items={["P0", "P1", "P2", "P3"].map((p) => ({ value: p, label: p }))} />
                <Filter label="Assignee" all="Any assignee" value={view.filters.assignee} onChange={(v) => setFilter("assignee", v)}
                  items={options.assignees.map((a) => ({ value: a, label: a === "user" ? "You" : a }))} />
                <Filter label="Agent" all="Any agent" value={view.filters.holder} onChange={(v) => setFilter("holder", v)}
                  items={options.holders.map((h) => ({ value: h, label: h }))} />
              </PopoverContent>
            </Popover>
            <ProgressPanel />
            <Filter label="Group by" all="No grouping" value={view.group === "none" ? undefined : view.group} inline
              onChange={(v) => setView((current) => ({ ...current, group: (v || "none") as Group }))}
              items={[{ value: "project", label: "Group by project" }, { value: "feature", label: "Group by Feature" }]} />
            <Button size="sm" className="ml-auto" onClick={() => setCreating(true)}><Plus aria-hidden /> New task</Button>
          </div>
          {activeFilters.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 px-4 sm:px-8" aria-label="Filters on">
              {activeFilters.map(([name, value]) => (
                <Badge key={name} variant="secondary" className="h-6 gap-1 pr-0.5 font-normal">
                  {FILTER_NAMES[name]}: {name === "feature" ? featureName(value) : value === "user" ? "you" : value}
                  <Button variant="ghost" size="icon-xs" className="size-5" aria-label={`Clear the ${FILTER_NAMES[name].toLowerCase()} filter`}
                    onClick={() => setFilter(name as keyof BoardFilters, "")}><X aria-hidden /></Button>
                </Badge>
              ))}
              <Button variant="link" size="sm" className="h-auto px-1 text-xs" onClick={clear}>Clear all</Button>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-1.5 px-4 sm:px-8" role="group" aria-label="What needs you" data-tour="board-attention">
            {needs.length === 0 ? (
              <span className="text-xs text-muted-foreground">Nothing waits for you, and nothing is stuck.</span>
            ) : needs.map(({ key, count }) => (
              <Button key={key} size="xs" variant={attention === key ? "secondary" : "outline"} aria-pressed={attention === key}
                className={cn(ATTENTION[key].tone, attention === key && "ring-2 ring-ring/40")}
                onClick={() => setAttention(attention === key ? null : key)}>
                <span className="font-semibold tabular-nums">{count}</span> {ATTENTION[key].label}
              </Button>
            ))}
            {attention && <Button variant="link" size="sm" className="h-auto px-1 text-xs" onClick={() => setAttention(null)}>Show every task</Button>}
          </div>
          <DispatcherStrip now={now} onOpen={open} />
          <div className="flex snap-x gap-3 overflow-x-auto px-4 pb-2 sm:px-8" data-tour="board-columns">
            {TASK_STATUSES.map((status) => (
              <Column key={status} status={status} tasks={shownIn(status)} group={view.group} now={now} simple={simple}
                folded={FOLDABLE.includes(status) && !view.unfolded.includes(status) && over !== status}
                onFold={(unfold) => fold(status, unfold)} dragging={dragging} over={over === status}
                onOver={(on) => setOver(on ? status : null)} onDrop={(before) => drop(status, before)}
                onOpen={open} onMove={(task, to) => void move(task, to)}
                onDragStart={(task, event) => { event.dataTransfer.setData("text/plain", task.id); event.dataTransfer.effectAllowed = "move"; setDragging(task.id); }}
                onDragEnd={() => { setDragging(null); setOver(null); }} />
            ))}
          </div>
          {filtered && data.total === 0 && (
            <p className="px-4 text-sm text-muted-foreground sm:px-8">No tasks match. <Button variant="link" size="sm" className="px-0" onClick={clear}>Clear filters</Button></p>
          )}
          <div className="space-y-1 px-4 text-xs text-muted-foreground sm:px-8">
            <p>
              Drag a card to move it, or use its menu. Done and Cancelled tasks leave the Board 14 days after they end.{" "}
              <Button variant="link" size="sm" className="h-auto px-0 text-xs"
                onClick={() => setView((v) => ({ ...v, filters: { ...v.filters, archived: !v.filters.archived || undefined } }))}>
                {view.filters.archived ? "Hide older ones" : "Show them too"}
              </Button>
            </p>
            {!simple && (
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span id="autonomy-label">Agents take unasked:</span>
                <Select items={(Object.keys(AUTONOMY_LABELS) as Autonomy[]).map((a) => ({ value: a, label: AUTONOMY_LABELS[a] }))} value={data.autonomy}
                  onValueChange={async (next) => {
                    if (!next || next === data.autonomy) return;
                    try { await setAutonomy(next as Autonomy); void board.refresh(); toast.success(`Agents take unasked: ${AUTONOMY_LABELS[next as Autonomy]}`); }
                    catch (e) { toast.error("Not changed", { description: e instanceof Error ? e.message : String(e) }); }
                  }}>
                  <SelectTrigger size="sm" aria-labelledby="autonomy-label" className="h-7 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent>{(Object.keys(AUTONOMY_LABELS) as Autonomy[]).map((a) => <SelectItem key={a} value={a}>{AUTONOMY_LABELS[a]}</SelectItem>)}</SelectContent>
                </Select>
                <span>in their own project, once they finish what you asked.</span>
              </p>
            )}
          </div>
        </>
      )}

      <NewTaskDialog open={creating} onOpenChange={setCreating} options={options}
        defaultProject={view.filters.project ?? (options.projects.length === 1 ? options.projects[0] : "")}
        onCreated={(task) => { void board.refresh(); open(task.id); }} />
      <Sheet open={!!openTask} onOpenChange={(o) => { if (!o) open(null); }}>
        <SheetContent className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-2xl">
          {openTask && <TaskDrawer key={openTask} taskId={openTask} options={options} now={now} simple={simple}
            onChanged={() => void board.refresh()} onClose={() => open(null)} />}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function Filter({ label, all, value, items, onChange, inline }: {
  label: string; all: string; value: string | undefined; items: { value: string; label: string }[]; onChange: (value: string) => void;
  /** In the toolbar, without its label above it. */
  inline?: boolean;
}) {
  if (!items.length) return null;
  const choices = [{ value: "", label: all }, ...items];
  const select = (
    <Select items={choices} value={value ?? ""} onValueChange={(next) => onChange(String(next ?? ""))}>
      <SelectTrigger size="sm" aria-label={label} className={cn("text-xs", inline ? "max-w-44 shrink-0" : "w-full", value && "border-primary/50")}>
        <SelectValue className="truncate" />
      </SelectTrigger>
      <SelectContent>{choices.map((c) => <SelectItem key={c.value || "all"} value={c.value}>{c.label}</SelectItem>)}</SelectContent>
    </Select>
  );
  if (inline) return select;
  return <div className="grid gap-1"><span className="text-[11px] font-medium text-muted-foreground">{label}</span>{select}</div>;
}

/** One status of the Board, its Tasks grouped as the viewer chose; a drop
 * target. Done and Cancelled fold to a narrow strip that still takes drops. */
function Column({ status, tasks, group, now, simple, folded, onFold, dragging, over, onOver, onDrop, onOpen, onMove, onDragStart, onDragEnd }: {
  status: TaskStatus; tasks: BoardTask[]; group: Group; now: Date; simple: boolean; folded: boolean; onFold: (open: boolean) => void;
  dragging: string | null; over: boolean; onOver: (on: boolean) => void; onDrop: (before?: BoardTask) => void; onOpen: (id: string) => void;
  onMove: (task: BoardTask, status: TaskStatus) => void; onDragStart: (task: BoardTask, event: React.DragEvent) => void; onDragEnd: () => void;
}) {
  const groups = useMemo(() => {
    if (group === "none") return [{ key: "", tasks }];
    const by = new Map<string, BoardTask[]>();
    for (const task of tasks) {
      const name = group === "project" ? task.project : (task.feature_title ?? (task.tasks.length ? task.title : "No Feature"));
      by.set(name, [...(by.get(name) ?? []), task]);
    }
    return [...by.entries()].map(([key, list]) => ({ key, tasks: list }));
  }, [tasks, group]);
  const target = {
    onDragOver: (e: React.DragEvent) => { if (dragging) { e.preventDefault(); onOver(true); } },
    onDragLeave: (e: React.DragEvent) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) onOver(false); },
    onDrop: (e: React.DragEvent) => { e.preventDefault(); onDrop(); },
  };
  if (folded) {
    return (
      <section aria-label={`${STATUS_LABELS[status]}, ${tasks.length} tasks, folded`} {...target}
        className="flex w-11 shrink-0 snap-start flex-col items-center gap-2 rounded-xl border border-border bg-muted/40 py-2">
        <Button variant="ghost" size="icon-sm" aria-label={`Show ${STATUS_LABELS[status]}, ${tasks.length} tasks`} onClick={() => onFold(true)}>
          <ChevronLeft aria-hidden />
        </Button>
        <span className="text-xs text-muted-foreground tabular-nums" aria-hidden>{tasks.length}</span>
        <span className="text-sm font-semibold [writing-mode:vertical-rl]" aria-hidden>{STATUS_LABELS[status]}</span>
      </section>
    );
  }
  return (
    <section aria-label={`${STATUS_LABELS[status]}, ${tasks.length} tasks`} {...target}
      className={cn("flex max-w-[22rem] min-w-[15rem] flex-1 shrink-0 snap-start flex-col rounded-xl border border-border bg-muted/40 transition-colors",
        over && "border-primary/60 bg-primary/5")}>
      <header className="px-3 pt-3 pb-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">{STATUS_LABELS[status]}</h2>
          <span className="text-xs text-muted-foreground tabular-nums">{tasks.length}</span>
          {FOLDABLE.includes(status) && (
            <Button variant="ghost" size="icon-xs" className="ml-auto" aria-label={`Fold ${STATUS_LABELS[status]}`} onClick={() => onFold(false)}>
              <ChevronRight aria-hidden />
            </Button>
          )}
        </div>
        <p className="text-[11px] leading-snug text-muted-foreground">{STATUS_HINTS[status]}</p>
      </header>
      <div className="flex min-h-24 flex-1 flex-col gap-2 px-2 pb-2">
        {tasks.length === 0 && <p className="px-1 py-6 text-center text-xs text-muted-foreground">{over ? "Drop here" : "Nothing here"}</p>}
        {groups.map(({ key, tasks: list }) => (
          <div key={key || "all"} className="space-y-2">
            {key && <h3 className="truncate px-1 pt-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{key}</h3>}
            <ul className="space-y-2">
              {list.map((task) => (
                <TaskCard key={task.id} task={task} now={now} simple={simple} dragging={dragging === task.id}
                  onOpen={() => onOpen(task.id)} onMove={(to) => onMove(task, to)}
                  onDragStart={(e) => onDragStart(task, e)} onDragEnd={onDragEnd}
                  onDragOver={(e) => { if (dragging && dragging !== task.id) e.preventDefault(); }}
                  onDrop={(e) => { e.preventDefault(); e.stopPropagation(); onDrop(task); }} />
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
