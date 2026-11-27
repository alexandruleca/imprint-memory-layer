"use client";

import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { ClipboardList, Plus, RotateCw, Search, X } from "lucide-react";
import { toast } from "sonner";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";
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
import { NewTaskDialog } from "./new-task-dialog";
import { TaskCard } from "./task-card";
import { TaskDrawer } from "./task-drawer";

type Group = "none" | "project" | "feature";
type View = { filters: BoardFilters; group: Group };
const VIEW_KEY = "imprint.board.view";
const EMPTY_VIEW: View = { filters: {}, group: "none" };
const AUTONOMY_LABELS: Record<Autonomy, string> = { ready: "Ready and Stale tasks", stale: "Only Stale tasks", off: "Nothing" };

// The filters and grouping the viewer chose last, kept in this browser, and
// in memory too when storage is unavailable (private window, blocked data).
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
    if (saved && typeof saved === "object") return { filters: saved.filters ?? {}, group: saved.group ?? "none" };
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

/** The workspace's Board: a column per status, cards for its Tasks, and a
 * drawer for one Task (ADR 0208). Agents change it through their tools;
 * here the user does, and it refreshes while they work. */
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

  const key = `tasks-board:${JSON.stringify(view.filters)}`;
  const board = useCached<BoardData>(key, () => getBoard(view.filters), { refreshInterval: 5000 });
  const [creating, setCreating] = useState(false);
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<TaskStatus | null>(null);

  const setFilter = (name: keyof BoardFilters, value: string) =>
    setView((v) => ({ ...v, filters: { ...v.filters, [name]: value || undefined } }));
  const filtered = Object.values(view.filters).some(Boolean);
  const clear = () => { setText(""); setView((v) => ({ ...v, filters: {} })); };

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
          <div className="flex gap-3 overflow-hidden">{TASK_STATUSES.map((s) => <Skeleton key={s} className="h-64 w-72 shrink-0 rounded-xl" />)}</div>
        )}
      </div>
    );
  }

  const data = board.data;
  const options = data.options;
  const nothingYet = data.total === 0 && !filtered && !view.filters.archived;
  return (
    <div className="space-y-4 pb-10">
      <div className="flex flex-wrap items-center gap-2 px-4 sm:px-8" role="toolbar" aria-label="Board filters">
        <InputGroup className="max-w-xs min-w-40 flex-1 bg-card">
          <InputGroupAddon><Search aria-hidden /></InputGroupAddon>
          <InputGroupInput aria-label="Find tasks by id, title, description, criteria or tag" placeholder="Find tasks"
            value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape" && text) { e.preventDefault(); setText(""); } }} />
          {text && <InputGroupAddon align="inline-end"><InputGroupButton size="icon-xs" aria-label="Clear the search" onClick={() => setText("")}><X aria-hidden /></InputGroupButton></InputGroupAddon>}
        </InputGroup>
        <Filter label="Project" all="All projects" value={view.filters.project} onChange={(v) => setFilter("project", v)}
          items={options.projects.map((p) => ({ value: p, label: p }))} />
        <Filter label="Feature" all="All Features" value={view.filters.feature} onChange={(v) => setFilter("feature", v)}
          items={options.features.map((f) => ({ value: f.id, label: f.title }))} />
        <Filter label="Tag" all="All tags" value={view.filters.tag} onChange={(v) => setFilter("tag", v)}
          items={options.tags.map((t) => ({ value: t, label: `#${t}` }))} />
        <Filter label="Priority" all="Any priority" value={view.filters.priority} onChange={(v) => setFilter("priority", v)}
          items={["P0", "P1", "P2", "P3"].map((p) => ({ value: p, label: p }))} />
        <Filter label="Assignee" all="Anyone" value={view.filters.assignee} onChange={(v) => setFilter("assignee", v)}
          items={options.assignees.map((a) => ({ value: a, label: a === "user" ? "You" : a }))} />
        <Filter label="Agent" all="Any agent" value={view.filters.holder} onChange={(v) => setFilter("holder", v)}
          items={options.holders.map((h) => ({ value: h, label: h }))} />
        <Filter label="Group by" all="No grouping" value={view.group === "none" ? undefined : view.group}
          onChange={(v) => setView((current) => ({ ...current, group: (v || "none") as Group }))}
          items={[{ value: "project", label: "Group by project" }, { value: "feature", label: "Group by Feature" }]} />
        {filtered && <Button variant="ghost" size="sm" onClick={clear}>Clear filters</Button>}
        <Button size="sm" className="ml-auto" onClick={() => setCreating(true)}><Plus aria-hidden /> New task</Button>
      </div>

      {!simple && !nothingYet && (
        <div className="flex flex-wrap items-center gap-2 px-4 text-xs text-muted-foreground sm:px-8">
          <span id="autonomy-label">Agents in this workspace take unasked:</span>
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
        </div>
      )}

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
        <div className="flex snap-x gap-3 overflow-x-auto px-4 pb-2 sm:px-8" data-tour="board-columns">
          {TASK_STATUSES.map((status) => (
            <Column key={status} status={status} tasks={data.columns[status]} group={view.group} now={now} simple={simple}
              dragging={dragging} over={over === status}
              onOver={(on) => setOver(on ? status : null)} onDrop={(before) => drop(status, before)}
              onOpen={open} onMove={(task, to) => void move(task, to)}
              onDragStart={(task, event) => { event.dataTransfer.setData("text/plain", task.id); event.dataTransfer.effectAllowed = "move"; setDragging(task.id); }}
              onDragEnd={() => { setDragging(null); setOver(null); }} />
          ))}
        </div>
      )}
      {filtered && data.total === 0 && (
        <p className="px-4 text-sm text-muted-foreground sm:px-8">No tasks match these filters. <Button variant="link" size="sm" className="px-0" onClick={clear}>Clear filters</Button></p>
      )}
      {!nothingYet && (
        <p className="px-4 text-xs text-muted-foreground sm:px-8">
          Drag a card to move it, or use its menu. Done and Cancelled tasks leave the Board 14 days after they end.{" "}
          <Button variant="link" size="sm" className="h-auto px-0 text-xs" onClick={() => setView((v) => ({ ...v, filters: { ...v.filters, archived: !v.filters.archived || undefined } }))}>
            {view.filters.archived ? "Hide older ones" : "Show them too"}
          </Button>
        </p>
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

function Filter({ label, all, value, items, onChange }: {
  label: string; all: string; value: string | undefined; items: { value: string; label: string }[]; onChange: (value: string) => void;
}) {
  if (!items.length) return null;
  const choices = [{ value: "", label: all }, ...items];
  return (
    <Select items={choices} value={value ?? ""} onValueChange={(next) => onChange(String(next ?? ""))}>
      <SelectTrigger size="sm" aria-label={label} className={cn("max-w-44 shrink-0 text-xs", value && "border-primary/50")}>
        <SelectValue className="truncate" />
      </SelectTrigger>
      <SelectContent>{choices.map((c) => <SelectItem key={c.value || "all"} value={c.value}>{c.label}</SelectItem>)}</SelectContent>
    </Select>
  );
}

/** One status of the Board, its Tasks grouped as the viewer chose; a drop target. */
function Column({ status, tasks, group, now, simple, dragging, over, onOver, onDrop, onOpen, onMove, onDragStart, onDragEnd }: {
  status: TaskStatus; tasks: BoardTask[]; group: Group; now: Date; simple: boolean; dragging: string | null; over: boolean;
  onOver: (on: boolean) => void; onDrop: (before?: BoardTask) => void; onOpen: (id: string) => void;
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
  return (
    <section aria-label={`${STATUS_LABELS[status]}, ${tasks.length} tasks`}
      onDragOver={(e) => { if (dragging) { e.preventDefault(); onOver(true); } }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) onOver(false); }}
      onDrop={(e) => { e.preventDefault(); onDrop(); }}
      className={cn("flex w-[17.5rem] shrink-0 snap-start flex-col rounded-xl border border-border bg-muted/40 transition-colors",
        over && "border-primary/60 bg-primary/5")}>
      <header className="flex items-baseline gap-2 px-3 pt-3 pb-2" title={STATUS_HINTS[status]}>
        <h2 className="text-sm font-semibold">{STATUS_LABELS[status]}</h2>
        <span className="text-xs text-muted-foreground tabular-nums">{tasks.length}</span>
        <span className="ml-auto truncate text-[11px] text-muted-foreground">{STATUS_HINTS[status]}</span>
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
