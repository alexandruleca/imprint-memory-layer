"use client";

import { useState } from "react";
import { ChevronRight, Kanban, Layers, Plus, RotateCw } from "lucide-react";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Timestamp, useNow } from "@/components/timestamp";
import { getBoard, getFeatures, type BoardData, type BoardOptions, type BoardTask, type FeatureEntry, type FeaturesData } from "@/lib/api";
import { useUiMode } from "@/lib/ui-mode";
import { useCached } from "@/lib/use-cached";
import { cn } from "@/lib/utils";
import { showFeatureOnBoard } from "./board";
import { STATUS_LABELS, STATUS_MARKS, who } from "./format";
import { CopyId } from "./links";
import { NewTaskDialog } from "./new-task-dialog";
import { ProgressBar, estimateText } from "./progress";
import { TaskSheet, useLinkedTask } from "./task-sheet";

const EMPTY_OPTIONS: BoardOptions = { projects: [], tags: [], assignees: [], holders: [], features: [] };
const ended = (task: BoardTask) => task.status === "done" || task.status === "cancelled";
/** Open Features shown unfolded when there are this few. */
const UNFOLDED_UP_TO = 3;

/** What of a Feature waits for the person, or is stuck: as the Board counts it. */
const NEEDS = [
  { key: "review", label: "to review", tone: "", test: (t: BoardTask) => t.status === "review" },
  { key: "mine", label: "assigned to you", tone: "", test: (t: BoardTask) => t.assignees.includes("user") && !ended(t) },
  { key: "stale", label: "stale", tone: "text-destructive", test: (t: BoardTask) => t.stale },
  { key: "blocked", label: "blocked", tone: "text-destructive", test: (t: BoardTask) => !!t.blocked && !ended(t) },
] as const;

type Adding = { kind: "feature" } | { kind: "task"; feature: string; project: string } | null;

/** The workspace's Features, apart from the Board's columns: each with how
 * far it is, what of it waits for the person, and its Tasks in the order
 * they can be worked in. A Feature's status follows its Tasks (ADR 0242). */
export function FeaturesView() {
  const { simple } = useUiMode();
  const now = useNow(30_000);
  const [archived, setArchived] = useState(false);
  const [finishedOpen, setFinishedOpen] = useState(false);
  const [adding, setAdding] = useState<Adding>(null);
  const list = useCached<FeaturesData>(`tasks-features:${archived ? "all" : "recent"}`, () => getFeatures({ archived }), { refreshInterval: 5000 });
  // The Board's filter options and Tasks, which the drawer offers and links to.
  const board = useCached<BoardData>("tasks-board:{}", () => getBoard({}), { refreshInterval: 15_000 });
  const linked = useLinkedTask();

  if (!list.data?.features) {
    return (
      <div className="space-y-3 px-4 sm:px-8" aria-busy={!list.error}>
        {list.error ? (
          <Alert variant="destructive">
            <AlertTitle>Could not load the Features</AlertTitle>
            <AlertDescription>{list.error.message}. The dashboard keeps trying every few seconds.</AlertDescription>
            <AlertAction><Button variant="outline" size="sm" onClick={() => void list.refresh()}><RotateCw aria-hidden /> Retry</Button></AlertAction>
          </Alert>
        ) : [0, 1, 2].map((i) => <Skeleton key={i} className="h-24 w-full rounded-xl" />)}
      </div>
    );
  }

  const entries = list.data.features;
  const open = entries.filter((e) => !ended(e.feature));
  const finished = entries.filter((e) => ended(e.feature))
    .sort((a, b) => (b.feature.ended_at ?? b.feature.status_at) - (a.feature.ended_at ?? a.feature.status_at));
  const showProject = new Set(entries.map((e) => e.feature.project)).size > 1;
  const options = board.data?.options ?? EMPTY_OPTIONS;
  const known = [...(board.data ? Object.values(board.data.columns).flat() : []), ...entries.map((e) => e.feature)];
  const refresh = () => { void list.refresh(); void board.refresh(); };
  const row = (entry: FeatureEntry, unfolded: boolean) => (
    <FeatureRow key={entry.feature.id} entry={entry} now={now} showProject={showProject} unfolded={unfolded}
      onOpen={linked.open} onAddTask={() => setAdding({ kind: "task", feature: entry.feature.id, project: entry.feature.project })} />
  );

  return (
    <div className="space-y-4 px-4 pb-10 sm:px-8">
      {entries.length === 0 && !archived ? (
        <Empty className="border border-border bg-card">
          <EmptyHeader>
            <EmptyMedia variant="icon"><Layers aria-hidden /></EmptyMedia>
            <EmptyTitle>No Features yet.</EmptyTitle>
            <EmptyDescription>
              A Feature groups the tasks of one piece of work. Its status follows them, and it is Done once they all are. Make one,
              or ask an agent to plan a piece of work onto the Board.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent><Button onClick={() => setAdding({ kind: "feature" })}><Plus aria-hidden /> New Feature</Button></EmptyContent>
        </Empty>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2" data-tour="features-toolbar">
            <p className="text-sm text-muted-foreground">
              {open.length} open{finished.length > 0 && `, ${finished.length} finished`}. A Feature&apos;s status follows its tasks.
            </p>
            <Button size="sm" className="ml-auto" onClick={() => setAdding({ kind: "feature" })}><Plus aria-hidden /> New Feature</Button>
          </div>
          {open.length > 0 ? (
            <ul className="grid gap-3" aria-label="Open Features">{open.map((e) => row(e, open.length <= UNFOLDED_UP_TO))}</ul>
          ) : (
            <p className="rounded-xl border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
              Every Feature is finished.
            </p>
          )}
          {finished.length > 0 && (
            <section aria-label="Finished Features" className="grid gap-2">
              <Button variant="ghost" size="sm" className="-ml-2 justify-self-start text-muted-foreground" aria-expanded={finishedOpen}
                onClick={() => setFinishedOpen(!finishedOpen)}>
                <ChevronRight aria-hidden className={cn("transition-transform", finishedOpen && "rotate-90")} /> Finished ({finished.length})
              </Button>
              {finishedOpen && <ul className="grid gap-3">{finished.map((e) => row(e, false))}</ul>}
            </section>
          )}
          <p className="text-xs text-muted-foreground">
            Finished Features leave this list 14 days after they end.{" "}
            <Button variant="link" size="sm" className="h-auto px-0 text-xs" onClick={() => { setArchived(!archived); setFinishedOpen(true); }}>
              {archived ? "Hide older ones" : "Show them too"}
            </Button>
          </p>
        </>
      )}

      <NewTaskDialog key={adding?.kind === "task" ? adding.feature : "feature"} open={!!adding}
        onOpenChange={(o) => { if (!o) setAdding(null); }} options={options} kind={adding?.kind ?? "feature"}
        defaultFeature={adding?.kind === "task" ? adding.feature : undefined}
        defaultProject={adding?.kind === "task" ? adding.project : options.projects.length === 1 ? options.projects[0] : ""}
        onCreated={(task) => { refresh(); linked.open(task.id); }} />
      <TaskSheet linked={linked} options={options} known={known} now={now} simple={simple} onChanged={refresh} />
    </div>
  );
}

/** One Feature: its status, progress and what waits, and its Tasks on demand. */
function FeatureRow({ entry, now, showProject, unfolded, onOpen, onAddTask }: {
  entry: FeatureEntry; now: Date; showProject: boolean; unfolded: boolean; onOpen: (id: string) => void; onAddTask: () => void;
}) {
  const { feature, summary, tasks } = entry;
  const [expanded, setExpanded] = useState(unfolded);
  const mark = STATUS_MARKS[feature.status];
  const Icon = mark.icon;
  const working = summary.counts.in_progress + summary.counts.review;
  const needs = NEEDS.map((n) => ({ ...n, count: tasks.filter(n.test).length })).filter((n) => n.count > 0);
  const titleId = `feature-${feature.id}`;
  const listId = `feature-${feature.id}-tasks`;
  return (
    <li className="rounded-xl border border-border bg-card" aria-labelledby={titleId}>
      <div className="grid gap-2 p-3 sm:p-4">
        <div className="flex min-w-0 items-start gap-2">
          <Button variant="ghost" size="icon-sm" className="-ml-1 shrink-0" aria-expanded={expanded} aria-controls={listId}
            aria-label={`${expanded ? "Hide" : "Show"} the tasks of ${feature.title}`} onClick={() => setExpanded(!expanded)}>
            <ChevronRight aria-hidden className={cn("transition-transform", expanded && "rotate-90")} />
          </Button>
          <div className="grid min-w-0 flex-1 gap-0.5">
            <h3 id={titleId} className="min-w-0 text-sm font-semibold">
              <button type="button" className="text-left break-words outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring/50"
                onClick={() => onOpen(feature.id)}>{feature.title}</button>
            </h3>
            <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
              <span className={cn("inline-flex items-center gap-1 font-medium", mark.tone)}>
                <Icon className="size-3.5" aria-hidden />{STATUS_LABELS[feature.status]}
              </span>
              <CopyId id={feature.id} />
              {showProject && <span>{feature.project}</span>}
              <span>{feature.priority}</span>
              {feature.ended_at && <span>ended <Timestamp seconds={feature.ended_at} now={now} /></span>}
            </p>
          </div>
        </div>
        <div className="grid gap-1 sm:pl-8">
          <ProgressBar done={summary.done} total={summary.total} working={working} label={`${feature.title}: ${summary.done} of ${summary.total} done`} />
          <p className="text-xs text-muted-foreground">
            {summary.total === 0 ? "No tasks yet" : <>
              {summary.done} of {summary.total} done{working > 0 && ` · ${working} in progress`}
              {!ended(feature) && <> · {estimateText(summary.days_left)}</>}
            </>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 sm:pl-8">
          {needs.map((n) => (
            <Badge key={n.key} variant="outline" className={cn("h-5 font-normal", n.tone)}>
              <span className="font-semibold tabular-nums">{n.count}</span> {n.label}
            </Badge>
          ))}
          <Button variant="ghost" size="xs" className="ml-auto text-muted-foreground" onClick={() => showFeatureOnBoard(feature.id)}
            aria-label={`Show the tasks of ${feature.title} on the Board`}>
            <Kanban aria-hidden /> Show on Board
          </Button>
        </div>
      </div>
      {expanded && (
        <div id={listId} className="border-t border-border px-2 py-2 sm:px-3">
          {tasks.length === 0 ? (
            <p className="px-2 py-2 text-sm text-muted-foreground">
              No tasks yet: ask an agent to plan it, or{" "}
              <Button variant="link" size="sm" className="h-auto px-0" onClick={onAddTask}>add one</Button>.
            </p>
          ) : (
            <>
              <ol className="grid" aria-label={`Tasks of ${feature.title}, in working order`}>
                {tasks.map((task) => <TaskRow key={task.id} task={task} onOpen={onOpen} />)}
              </ol>
              <Button variant="ghost" size="sm" className="mt-1 text-muted-foreground" onClick={onAddTask}><Plus aria-hidden /> Add a task</Button>
            </>
          )}
        </div>
      )}
    </li>
  );
}

/** One Task of a Feature: its status, who holds it and what it waits on. */
function TaskRow({ task, onOpen }: { task: BoardTask; onOpen: (id: string) => void }) {
  const mark = STATUS_MARKS[task.status];
  const Icon = mark.icon;
  const done = ended(task);
  const notes = [
    task.holder && !task.stale && `${who(task.holder)}`,
    !done && task.open_blockers.length > 0 && `waits on ${task.open_blockers.join(", ")}`,
    task.assignees.includes("user") && !done && "assigned to you",
  ].filter(Boolean) as string[];
  return (
    <li>
      <button type="button" onClick={() => onOpen(task.id)}
        aria-label={`${task.id}: ${task.title}, ${STATUS_LABELS[task.status]}. Open`}
        className="flex w-full min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 rounded-md px-2 py-1.5 text-left outline-none hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring/50">
        <Icon className={cn("size-3.5 shrink-0", mark.tone)} aria-hidden />
        <span className="shrink-0 font-mono text-xs text-muted-foreground">{task.id}</span>
        <span className={cn("min-w-0 flex-1 truncate text-sm", done && "text-muted-foreground", task.status === "cancelled" && "line-through")}>
          {task.title}
        </span>
        <span className="flex shrink-0 items-center gap-1.5 text-[11px] text-muted-foreground">
          {task.stale && <Badge variant="outline" className="h-4 border-destructive/40 px-1 text-[10px] text-destructive">Stale</Badge>}
          {task.blocked && !done && <Badge variant="outline" className="h-4 border-destructive/40 px-1 text-[10px] text-destructive">Blocked</Badge>}
          {notes.length > 0 && <span className="max-w-48 truncate">{notes.join(" · ")}</span>}
          <span className="hidden sm:inline">{STATUS_LABELS[task.status]}</span>
        </span>
      </button>
    </li>
  );
}
