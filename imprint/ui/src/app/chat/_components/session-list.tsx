"use client";

import { useRef, useState } from "react";
import { MoreHorizontal, Pencil, SquarePen, Trash2 } from "lucide-react";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import type { ChatSession } from "@/lib/types";

const DAY = 86_400_000;

function groupLabel(updatedAt: number, today: number): string {
  const t = updatedAt * 1000;
  if (t >= today) return "Today";
  if (t >= today - DAY) return "Yesterday";
  if (t >= today - 6 * DAY) return "Previous 7 days";
  if (t >= today - 29 * DAY) return "Previous 30 days";
  return "Older";
}

function groups(sessions: ChatSession[]): [string, ChatSession[]][] {
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  const out: [string, ChatSession[]][] = [];
  for (const session of sessions) {
    const label = groupLabel(session.updated_at, midnight.getTime());
    if (out[out.length - 1]?.[0] === label) out[out.length - 1][1].push(session);
    else out.push([label, [session]]);
  }
  return out;
}

type Props = {
  /** null while loading */
  sessions: ChatSession[] | null;
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
};

function Row({ session, active, onSelect, onRename, onDelete }: {
  session: ChatSession; active: boolean;
  onSelect: () => void; onRename: (title: string) => void; onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(session.title);
  const cancelled = useRef(false);

  if (editing) {
    // Leaving the field is the one place an edit ends: Enter saves, Escape
    // discards.
    return (
      <Input
        autoFocus
        value={title}
        aria-label="Chat title"
        onChange={(event) => setTitle(event.target.value)}
        onBlur={() => {
          setEditing(false);
          const next = title.trim();
          if (!cancelled.current && next && next !== session.title) onRename(next);
          cancelled.current = false;
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") cancelled.current = true;
          if (event.key === "Enter" || event.key === "Escape") event.currentTarget.blur();
        }}
        className="h-8"
      />
    );
  }
  return (
    <div className={cn("group/row flex items-center rounded-lg", active ? "bg-accent" : "hover:bg-muted")}>
      <button
        type="button"
        onClick={onSelect}
        aria-current={active ? "page" : undefined}
        className={cn("min-w-0 flex-1 truncate rounded-lg px-2.5 py-1.5 text-left text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50", active && "font-medium")}
      >
        {session.title}
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={`Actions for ${session.title}`}
              className={cn("mr-1 focus-visible:opacity-100 data-[popup-open]:opacity-100", !active && "opacity-0 group-hover/row:opacity-100")}
            />
          }
        >
          <MoreHorizontal />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-36">
          <DropdownMenuItem onClick={() => { setTitle(session.title); setEditing(true); }}><Pencil /> Rename</DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onClick={onDelete}><Trash2 /> Delete</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

/** The chat history, newest first, grouped by when each chat was last used. */
export function SessionList({ sessions, activeId, onSelect, onNew, onRename, onDelete }: Props) {
  const [deleting, setDeleting] = useState<ChatSession | null>(null);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="p-3">
        <Button variant="outline" className="w-full justify-start" onClick={onNew}>
          <SquarePen /> New chat
        </Button>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <nav aria-label="Chats" className="space-y-4 px-3 pb-3">
          {sessions === null ? (
            <div className="space-y-1.5" aria-busy="true">
              {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-7 w-full rounded-lg" />)}
            </div>
          ) : sessions.length === 0 ? (
            <p className="px-2.5 text-xs text-muted-foreground">Your chats will appear here.</p>
          ) : (
            groups(sessions).map(([label, items]) => (
              <section key={label} aria-label={label} className="space-y-0.5">
                <h3 className="px-2.5 pb-1 text-xs font-medium text-muted-foreground">{label}</h3>
                {items.map((session) => (
                  <Row
                    key={session.id}
                    session={session}
                    active={session.id === activeId}
                    onSelect={() => onSelect(session.id)}
                    onRename={(title) => onRename(session.id, title)}
                    onDelete={() => setDeleting(session)}
                  />
                ))}
              </section>
            ))
          )}
        </nav>
      </ScrollArea>

      <AlertDialog open={deleting !== null} onOpenChange={(open) => { if (!open) setDeleting(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete chat?</AlertDialogTitle>
            <AlertDialogDescription>“{deleting?.title}” and its messages will be deleted.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => { if (deleting) onDelete(deleting.id); setDeleting(null); }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
