"use client";

import { useState } from "react";
import { Bot, ChevronDown, Square } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Switch } from "@/components/ui/switch";
import { Timestamp } from "@/components/timestamp";
import { getDispatch, setDispatch, stopDispatched, type DispatchRun, type DispatchStatus } from "@/lib/api";
import { useCached } from "@/lib/use-cached";
import { cn } from "@/lib/utils";

const OUTCOMES: Record<string, { label: string; tone: string }> = {
  review: { label: "reached Review", tone: "text-emerald-700 dark:text-emerald-300" },
  failed: { label: "failed", tone: "text-destructive" },
  stopped: { label: "stopped", tone: "text-muted-foreground" },
  refused: { label: "not started", tone: "text-muted-foreground" },
};

/**
 * This device's Dispatcher for the workspace on show (ADR 0220): the switch,
 * the Dispatched sessions running with a Stop each, what it would take next,
 * the other devices whose Dispatcher is on, and the last results. Only the
 * person switches it; agents have no tool for it.
 */
export function DispatcherStrip({ now, onOpen }: { now: Date; onOpen: (task: string) => void }) {
  const status = useCached<DispatchStatus>("tasks-dispatch", getDispatch, { refreshInterval: 5000 });
  const [saving, setSaving] = useState(false);
  const data = status.data;
  if (!data) return null;

  async function toggle(on: boolean) {
    setSaving(true);
    try {
      await setDispatch(on);
      await status.refresh();
      toast.success(on ? "Dispatcher on for this device" : "Dispatcher off: running sessions finish");
    } catch (e) {
      toast.error("Not changed", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  }

  async function stop(run: DispatchRun) {
    try {
      await stopDispatched(run.id);
      void status.refresh();
      toast.success(`Stopped the session on ${run.task}`);
    } catch (e) {
      toast.error("Not stopped", { description: e instanceof Error ? e.message : String(e) });
    }
  }

  const ended = data.recent.filter((run) => run.ended_at !== null);
  return (
    <section aria-labelledby="dispatcher-title" data-tour="board-dispatcher"
      className="mx-4 grid gap-2 rounded-lg border bg-card px-3 py-2 text-sm sm:mx-8">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Bot aria-hidden className="size-4 text-muted-foreground" />
        <span id="dispatcher-title" className="font-medium">Dispatcher</span>
        <Switch aria-labelledby="dispatcher-title" checked={data.on} disabled={saving} onCheckedChange={(on) => void toggle(on)} />
        <span className="text-xs text-muted-foreground">
          {data.on
            ? data.autonomy === "off"
              ? "On, but agents take nothing unasked in this workspace."
              : "On for this device: starts Claude Code on Ready tasks, one at a time, in a worktree of its own."
            : "Off. Switch it on to have this device start agents on Ready tasks while nobody works on them."}
        </span>
        {data.devices.length > 0 && (
          <span className="text-xs text-muted-foreground">Also on: {data.devices.map((d) => d.name).join(", ")}</span>
        )}
      </div>

      {data.running.map((run) => (
        <div key={run.id} className="flex flex-wrap items-center gap-2 text-xs">
          <span className="relative flex size-2"><span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-500 opacity-60" /><span className="relative inline-flex size-2 rounded-full bg-emerald-500" /></span>
          <Button variant="link" size="sm" className="h-auto px-0 text-xs font-mono" onClick={() => onOpen(run.task)}>{run.task}</Button>
          <span className="min-w-0 truncate">{run.title}</span>
          <Badge variant="secondary" className="font-normal">{run.client}</Badge>
          <span className="text-muted-foreground">since <Timestamp seconds={run.started_at} now={now} /></span>
          <Button variant="outline" size="xs" className="ml-auto" onClick={() => void stop(run)} aria-label={`Stop the session on ${run.task}`}>
            <Square aria-hidden /> Stop
          </Button>
        </div>
      ))}

      {data.on && data.held && (
        <p className="text-xs text-amber-700 dark:text-amber-300">Starting nothing now: {data.held}.</p>
      )}

      {data.on && !data.held && data.running.length === 0 && (
        <p className="text-xs text-muted-foreground">
          {data.next.length > 0
            ? <>Next: <Button variant="link" size="sm" className="h-auto px-0 text-xs font-mono" onClick={() => onOpen(data.next[0].id)}>{data.next[0].id}</Button> {data.next[0].title}</>
            : "Nothing to take: no Ready standalone task is open to it."}
        </p>
      )}

      {ended.length > 0 && (
        <Collapsible>
          <CollapsibleTrigger render={<Button variant="link" size="sm" className="h-auto gap-1 px-0 text-xs text-muted-foreground" />}>
            <ChevronDown aria-hidden className="size-3" /> Recent results ({ended.length})
          </CollapsibleTrigger>
          <CollapsibleContent>
            <ul className="mt-1 grid gap-1 text-xs" aria-label="Recent Dispatched sessions">
              {ended.map((run) => {
                const outcome = OUTCOMES[run.outcome ?? ""] ?? { label: run.outcome ?? "ended", tone: "" };
                return (
                  <li key={run.id} className="flex flex-wrap items-center gap-2">
                    <Button variant="link" size="sm" className="h-auto px-0 text-xs font-mono" onClick={() => onOpen(run.task)}>{run.task}</Button>
                    <span className="min-w-0 truncate">{run.title}</span>
                    <Badge variant="outline" className="font-normal">{run.client}</Badge>
                    <span className={cn("font-medium", outcome.tone)} title={run.detail ?? undefined}>{outcome.label}</span>
                    <Timestamp seconds={run.ended_at ?? run.started_at} now={now} className="text-muted-foreground" />
                  </li>
                );
              })}
            </ul>
          </CollapsibleContent>
        </Collapsible>
      )}
    </section>
  );
}
