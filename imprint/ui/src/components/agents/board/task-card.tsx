"use client";

import { ArrowRightLeft, Copy, CornerDownRight, Link2, MessageSquare, MoreHorizontal } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { TASK_STATUSES, type BoardTask, type TaskStatus } from "@/lib/api";
import { cn } from "@/lib/utils";
import { StatusDot } from "../status";
import { STATUS_LABELS, ago, trouble, who } from "./format";
import { clientName } from "../format";
import { CopyId, copyTask } from "./links";
import { ProgressBar } from "./progress";

const PRIORITY_TONE: Record<string, string> = {
  P0: "border-destructive/40 bg-destructive/10 text-destructive",
  P1: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  P2: "border-border text-muted-foreground",
  P3: "border-transparent text-muted-foreground/80",
};
const TONE: Record<string, string> = {
  danger: "border-destructive/40 bg-destructive/10 text-destructive",
  warning: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  muted: "border-border text-muted-foreground",
};

/** One Task on the Board: what it is, who holds it, and what holds it up.
 * The title opens the Task, and so does a click anywhere else on the card
 * (its overlay); the id copies itself, the Feature line opens the Feature,
 * and the menu moves the Task without dragging. */
export function TaskCard({ task, now, simple, dragging, showProject = true, showFeature = true, onOpen, onOpenTask, onMove, onDragStart, onDragEnd, onDragOver, onDrop }: {
  task: BoardTask; now: Date; simple: boolean; dragging: boolean;
  /** Off when every Task on the Board has one project, or the column groups by project. */
  showProject?: boolean;
  /** Off when the column groups by Feature. */
  showFeature?: boolean;
  onOpen: () => void; onOpenTask: (id: string) => void; onMove: (status: TaskStatus) => void;
  onDragStart: (event: React.DragEvent) => void; onDragEnd: () => void;
  /** Another card dropped on this one goes before it. */
  onDragOver?: (event: React.DragEvent) => void; onDrop?: (event: React.DragEvent) => void;
}) {
  const problems = trouble(task);
  const holder = task.holder;
  const working = task.progress?.working ?? 0;
  return (
    <li draggable onDragStart={onDragStart} onDragEnd={onDragEnd} onDragOver={onDragOver} onDrop={onDrop} data-task={task.id}
      className={cn("group relative rounded-lg border border-border bg-card p-3 shadow-xs transition-[opacity,box-shadow] hover:shadow-sm",
        dragging && "opacity-40", task.stale && "border-destructive/40", task.stalling && "border-amber-500/40")}>
      <div className="flex min-w-0 items-center gap-1.5 pr-6 text-[11px] text-muted-foreground">
        <CopyId id={task.id} className="relative z-10" />
        <Badge variant="outline" className={cn("h-4 shrink-0 px-1 text-[10px]", PRIORITY_TONE[task.priority])}>{task.priority}</Badge>
        {showProject && <span className="min-w-0 truncate" title={task.project}>{task.project}</span>}
      </div>
      <button type="button" onClick={onOpen}
        aria-label={`${task.id}: ${task.title}, ${task.priority}${problems.length ? `, ${problems.map((p) => p.label).join(", ")}` : ""}. Open`}
        className="mt-1 block w-full text-left text-sm leading-snug font-medium break-words outline-none after:absolute after:inset-0 after:rounded-lg focus-visible:after:ring-3 focus-visible:after:ring-ring/50">
        {task.title}
      </button>
      {showFeature && task.feature && task.feature_title && (
        <button type="button" onClick={() => onOpenTask(task.feature!)} title={`Open the Feature ${task.feature}`}
          className="relative z-10 mt-1 flex max-w-full items-center gap-1 rounded text-left text-xs text-muted-foreground outline-none hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring/50">
          <CornerDownRight className="size-3 shrink-0" aria-hidden /><span className="truncate">{task.feature_title}</span>
        </button>
      )}
      {task.tasks.length > 0 && (
        <div className="mt-1.5 grid gap-1">
          <div className="flex justify-between gap-2 text-xs text-muted-foreground">
            <span>Feature · {task.tasks.length} {task.tasks.length === 1 ? "task" : "tasks"}</span>
            {task.progress && (
              <span className="tabular-nums" title={working ? `${working} in progress or in review` : undefined}>
                {task.progress.done} of {task.progress.total} done{working > 0 && <span className="text-muted-foreground/80"> · {working} under way</span>}
              </span>
            )}
          </div>
          {task.progress && task.progress.total > 0 && (
            <ProgressBar done={task.progress.done} working={working} total={task.progress.total}
              label={`${task.title}: ${task.progress.done} of ${task.progress.total} done`} />
          )}
        </div>
      )}
      {(task.tags.length > 0 || task.assignees.length > 0) && (
        <div className="mt-2 flex flex-wrap gap-1">
          {task.tags.map((tag) => <Badge key={tag} variant="secondary" className="h-5 px-1.5 text-[11px] font-normal">#{tag}</Badge>)}
          {task.assignees.map((a) => (
            <Badge key={a} variant="outline" className="h-5 px-1.5 text-[11px] font-normal">{a === "user" ? "→ you" : `→ ${a}`}</Badge>
          ))}
        </div>
      )}
      {problems.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {problems.map((p) => (
            <Badge key={p.label} variant="outline" title={p.detail} className={cn("h-5 px-1.5 text-[11px]", TONE[p.tone])}>{p.label}</Badge>
          ))}
        </div>
      )}
      {(holder || task.comment_count > 0) && (
        <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
          {holder && (
            <span className="flex min-w-0 items-center gap-1.5" title={`${who(holder)} on ${holder.device}`}>
              <StatusDot connected={!task.stalling} decorative />
              <span className="truncate font-medium text-foreground/80">{holder.name || clientName(holder.client)}</span>
              {!simple && holder.progress_at !== undefined && (
                <span className="shrink-0 tabular-nums" title="Last progress">· {ago(holder.progress_at, now)}</span>
              )}
            </span>
          )}
          {task.comment_count > 0 && (
            <span className="ml-auto flex shrink-0 items-center gap-0.5" aria-label={`${task.comment_count} comments`}>
              <MessageSquare className="size-3" aria-hidden /> {task.comment_count}
            </span>
          )}
        </div>
      )}
      <DropdownMenu>
        <DropdownMenuTrigger render={
          <Button variant="ghost" size="icon-xs" aria-label={`Move ${task.id}`}
            className="absolute top-2 right-2 z-10 opacity-60 group-hover:opacity-100 focus-visible:opacity-100 data-[popup-open]:opacity-100" />
        }>
          <MoreHorizontal aria-hidden />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-48">
          <DropdownMenuGroup>
            <DropdownMenuItem onClick={() => void copyTask(task.id)}><Copy aria-hidden /> Copy id</DropdownMenuItem>
            <DropdownMenuItem onClick={() => void copyTask(task.id, "link")}><Link2 aria-hidden /> Copy link</DropdownMenuItem>
            {task.feature && (
              <DropdownMenuItem onClick={() => onOpenTask(task.feature!)}><CornerDownRight aria-hidden /> Open its Feature</DropdownMenuItem>
            )}
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuLabel className="flex items-center gap-1.5"><ArrowRightLeft className="size-3.5" aria-hidden /> Move to</DropdownMenuLabel>
            {TASK_STATUSES.filter((s) => s !== task.status).map((status) => (
              <DropdownMenuItem key={status} onClick={() => onMove(status)}>{STATUS_LABELS[status]}</DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </li>
  );
}
