"use client";

import { useState, useSyncExternalStore } from "react";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import type { BoardOptions, BoardTask } from "@/lib/api";
import { TaskDrawer } from "./task-drawer";

// The Task the address opens (/agents/?task=T-…#board): a notification or a
// link opens it, and opening or closing one updates the address. The Board
// and the Features tab share it; only the open tab is mounted.
const TASK_EVENT = "imprint:board-task";

function linkedTask(): string | null {
  return new URLSearchParams(window.location.search).get("task");
}

function subscribeTask(notify: () => void) {
  window.addEventListener("popstate", notify);
  window.addEventListener(TASK_EVENT, notify);
  return () => { window.removeEventListener("popstate", notify); window.removeEventListener(TASK_EVENT, notify); };
}

/** The Task the address names, and the way to open another, remembering
 * the Tasks the drawer came through so it can go back. */
export function useLinkedTask() {
  const openTask = useSyncExternalStore(subscribeTask, linkedTask, () => null);
  const [trail, setTrail] = useState<string[]>([]);

  function open(id: string | null) {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("task", id); else url.searchParams.delete("task");
    window.history.replaceState(null, "", url);
    window.dispatchEvent(new Event(TASK_EVENT));
    if (!id) setTrail([]);
  }

  /** Open a Task the open one links to, remembering the way back. */
  function openLinked(id: string) {
    if (openTask && openTask !== id) setTrail((t) => [...t, openTask]);
    open(id);
  }

  function back() {
    const previous = trail.at(-1);
    setTrail((t) => t.slice(0, -1));
    open(previous ?? null);
  }

  return { openTask, open, openLinked, back, backTo: trail.at(-1) };
}

/** The drawer of the Task the address names. */
export function TaskSheet({ linked, options, known, now, simple, onChanged }: {
  linked: ReturnType<typeof useLinkedTask>; options: BoardOptions; known: BoardTask[]; now: Date; simple: boolean;
  onChanged: () => void;
}) {
  const { openTask, open, openLinked, back, backTo } = linked;
  return (
    <Sheet open={!!openTask} onOpenChange={(o) => { if (!o) open(null); }}>
      <SheetContent className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-2xl">
        {openTask && <TaskDrawer key={openTask} taskId={openTask} options={options} known={known} now={now} simple={simple}
          backTo={backTo} onOpenTask={openLinked} onBack={back}
          onChanged={onChanged} onClose={() => open(null)} />}
      </SheetContent>
    </Sheet>
  );
}
