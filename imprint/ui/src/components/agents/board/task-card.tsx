"use client";

import { ArrowRightLeft, MessageSquare, MoreHorizontal } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { TASK_STATUSES, type BoardTask, type TaskStatus } from "@/lib/api";
import { cn } from "@/lib/utils";
import { StatusDot } from "../status";
import { STATUS_LABELS, ago, trouble, who } from "./format";
import { clientName } from "../format";

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
 * The card opens the Task; its menu moves it without dragging. */
export function TaskCard({ task, now, simple, dragging, onOpen, onMove, onDragStart, onDragEnd, onDragOver, onDrop }: {
  task: BoardTask; now: Date; simple: boolean; dragging: boolean;
  onOpen: () => void; onMove: (status: TaskStatus) => void;
  onDragStart: (event: React.DragEvent) => void; onDragEnd: () => void;
  /** Another card dropped on this one goes before it. */
  onDragOver?: (event: React.DragEvent) => void; onDrop?: (event: React.DragEvent) => void;
}) {
  const problems = trouble(task);
  const holder = task.holder;
  return (
    <li draggable onDragStart={onDragStart} onDragEnd={onDragEnd} onDragOver={onDragOver} onDrop={onDrop} data-task={task.id}
      className={cn("group relative rounded-lg border border-border bg-card shadow-xs transition-[opacity,box-shadow] hover:shadow-sm",
        dragging && "opacity-40", task.stale && "border-destructive/40", task.stalling && "border-amber-500/40")}>
      <button type="button" onClick={onOpen}
        aria-label={`${task.id}: ${task.title}, ${task.priority}${problems.length ? `, ${problems.map((p) => p.label).join(", ")}` : ""}. Open`}
        className="block w-full min-w-0 rounded-lg p-3 pr-9 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
        <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <span className="font-mono">{task.id}</span>
          <Badge variant="outline" className={cn("h-4 px-1 text-[10px]", PRIORITY_TONE[task.priority])}>{task.priority}</Badge>
          <span className="truncate">{task.project}</span>
        </span>
        <span className="mt-1 block text-sm leading-snug font-medium break-words">{task.title}</span>
        {task.feature_title && (
          <span className="mt-1 block truncate text-xs text-muted-foreground" title={`Feature ${task.feature}`}>↳ {task.feature_title}</span>
        )}
        {task.tasks.length > 0 && (
          <span className="mt-1 block text-xs text-muted-foreground">Feature · {task.tasks.length} {task.tasks.length === 1 ? "task" : "tasks"}</span>
        )}
        {(task.tags.length > 0 || task.assignees.length > 0) && (
          <span className="mt-2 flex flex-wrap gap-1">
            {task.tags.map((tag) => <Badge key={tag} variant="secondary" className="h-5 px-1.5 text-[11px] font-normal">#{tag}</Badge>)}
            {task.assignees.map((a) => (
              <Badge key={a} variant="outline" className="h-5 px-1.5 text-[11px] font-normal">{a === "user" ? "→ you" : `→ ${a}`}</Badge>
            ))}
          </span>
        )}
        {problems.length > 0 && (
          <span className="mt-2 flex flex-wrap gap-1">
            {problems.map((p) => (
              <Badge key={p.label} variant="outline" title={p.detail} className={cn("h-5 px-1.5 text-[11px]", TONE[p.tone])}>{p.label}</Badge>
            ))}
          </span>
        )}
        {(holder || task.comment_count > 0) && (
          <span className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
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
          </span>
        )}
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger render={
          <Button variant="ghost" size="icon-xs" aria-label={`Move ${task.id}`}
            className="absolute top-2 right-2 opacity-60 group-hover:opacity-100 focus-visible:opacity-100 data-[popup-open]:opacity-100" />
        }>
          <MoreHorizontal aria-hidden />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-44">
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
