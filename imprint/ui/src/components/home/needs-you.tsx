"use client";

import { useRouter } from "next/navigation";
import { useState, useSyncExternalStore } from "react";
import { toast } from "sonner";
import { Bot, ChevronDown, CircleHelp, Clock, Eye, Hand, MessageSquare, UserRound, X, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Timestamp } from "@/components/timestamp";
import { useNow } from "@/components/home/activity";
import { dismissNeedsYou, getNeedsYou, type NeedsYouList, type NeedsYouRow } from "@/lib/api";
import { useCached } from "@/lib/use-cached";
import { cn } from "@/lib/utils";

const FOLD_KEY = "imprint.board.needs-you-folded";
// The Board list's fold, kept in this browser, and in memory when storage is unavailable.
const foldListeners = new Set<() => void>();
let foldedInMemory = false;
function readFolded(): boolean {
  try { return window.localStorage.getItem(FOLD_KEY) === "1"; } catch { return foldedInMemory; }
}
function subscribeFolded(notify: () => void) {
  foldListeners.add(notify);
  return () => { foldListeners.delete(notify); };
}
function writeFolded(next: boolean) {
  foldedInMemory = next;
  try { window.localStorage.setItem(FOLD_KEY, next ? "1" : "0"); } catch { /* kept in memory */ }
  foldListeners.forEach((notify) => notify());
}

const KINDS: Record<NeedsYouRow["kind"], { icon: LucideIcon; label: string }> = {
  comment: { icon: MessageSquare, label: "Comment for you" },
  answer: { icon: CircleHelp, label: "Waiting for your answer" },
  assigned: { icon: UserRound, label: "Assigned to you" },
  review: { icon: Eye, label: "To review" },
  fed: { icon: Hand, label: "Waiting for your move" },
  stale: { icon: Clock, label: "Stale" },
  dispatcher: { icon: Bot, label: "Dispatcher" },
};

/** Open a Task where the person works on it: the desktop app's Agents window, else the Board on this page. */
export function useOpenTask() {
  const router = useRouter();
  return (id: string | null) => {
    if (!id) { router.push("/agents/#board"); return; }
    if (window.electronAPI?.openAgents) window.electronAPI.openAgents(id);
    else router.push(`/agents/?task=${encodeURIComponent(id)}#board`);
  };
}

/**
 * What waits for the person, each row with its reason (ADR 0241): comments
 * written to them, tasks assigned to them or waiting for their review or
 * move, stale work left elsewhere, a Dispatcher that switched itself off,
 * and agents waiting for an answer. A row leaves when its reason ends, or
 * when the person dismisses it.
 */
export function NeedsYou({ className, onOpen, foldable = false }: {
  className?: string;
  /** Open a Task in place, as the Board does in its drawer; else on the Board page or the Agents window. */
  onOpen?: (task: string) => void;
  /** The Board's list folds away, remembered in this browser (ADR 0241). */
  foldable?: boolean;
}) {
  const list = useCached<NeedsYouList>("tasks-needs-you", () => getNeedsYou(), { refreshInterval: 15_000, persist: false });
  const navigate = useOpenTask();
  const openTask = (id: string | null) => (id && onOpen ? onOpen(id) : navigate(id));
  const now = new Date(useNow(30_000));
  const [gone, setGone] = useState<Set<string>>(new Set());
  const folded = useSyncExternalStore(subscribeFolded, () => foldable && readFolded(), () => false);
  const fold = writeFolded;
  // An older API answers without the list: nothing to show.
  if (!list.data || !Array.isArray(list.data.rows)) return null;
  const rows = list.data.rows.filter((row) => !gone.has(row.id));
  const here = list.data.workspace;

  async function dismiss(row: NeedsYouRow) {
    setGone((before) => new Set(before).add(row.id));
    try {
      await dismissNeedsYou(row);
      void list.refresh();
    } catch (e) {
      setGone((before) => { const after = new Set(before); after.delete(row.id); return after; });
      toast.error("Not dismissed", { description: e instanceof Error ? e.message : String(e) });
    }
  }

  return (
    <Card className={className} data-tour="home-needs-you" aria-labelledby="needs-you-title">
      <CardHeader>
        <CardTitle className="text-sm">
          {foldable ? (
            <h2 id="needs-you-title">
              <Button variant="ghost" size="sm" className="-ml-2 h-auto gap-1 px-2 py-0.5 text-sm font-semibold" aria-expanded={!folded}
                onClick={() => fold(!folded)}>
                <ChevronDown aria-hidden className={cn("size-4 transition-transform", folded && "-rotate-90")} />
                Needs you{rows.length > 0 ? ` (${rows.length})` : ""}
              </Button>
            </h2>
          ) : <h2 id="needs-you-title">Needs you{rows.length > 0 ? ` (${rows.length})` : ""}</h2>}
        </CardTitle>
        {!folded && <CardDescription className="text-xs">What your agents and the board are waiting on you for. Each stays until it is dealt with or you dismiss it.</CardDescription>}
      </CardHeader>
      {!folded && <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing waits for you.</p>
        ) : (
          <ul className="grid gap-2" aria-label="What needs you">
            {rows.map((row) => {
              const kind = KINDS[row.kind] ?? KINDS.assigned;
              const Icon = kind.icon;
              const elsewhere = row.workspace && here && row.workspace !== here;
              return (
                <li key={row.id} className="flex items-start gap-2 rounded-md border bg-card px-2.5 py-2 text-sm">
                  <Icon aria-label={kind.label} className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      {elsewhere ? (
                        <span className="font-medium">{row.task ? `${row.task} ` : ""}{row.title} <span className="text-xs font-normal text-muted-foreground">(workspace {row.workspace})</span></span>
                      ) : (
                        <Button variant="link" size="sm" className="h-auto px-0 text-left font-medium whitespace-normal" onClick={() => openTask(row.task)}>
                          {row.task && <span className="font-mono">{row.task}</span>} {row.title}
                        </Button>
                      )}
                      {row.since !== null && <Timestamp seconds={row.since} now={now} className="text-xs text-muted-foreground" />}
                    </div>
                    <p className="break-words text-xs text-muted-foreground">{row.reason}</p>
                  </div>
                  <Button variant="ghost" size="icon-xs" aria-label={`Dismiss: ${row.title}`} title="Dismiss" onClick={() => void dismiss(row)}>
                    <X aria-hidden />
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>}
    </Card>
  );
}
