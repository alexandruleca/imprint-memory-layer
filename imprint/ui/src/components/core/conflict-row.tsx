"use client";

import type { ReactNode } from "react";
import { BookOpenText, Check, History, Layers, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { ActionButton, RowError, type RowContext, type RowParts } from "@/components/review/review-list";
import { CostNote, amended, isRecord, openConflicts, originText, sourceOf } from "@/components/core/core-row";
import type { CoreKeep, CoreMemory } from "@/lib/api";
import { ago } from "@/lib/queue";
import { cn } from "@/lib/utils";

export interface ConflictRowOptions {
  byId: ReadonlyMap<string, CoreMemory>;
  budget: number;
  used: number;
  error?: string;
  /** "newer" uses the newer one (a rewrite overwrites the guideline),
   * "older" keeps the earlier (a rewrite is left out), "both" keeps both. */
  onSettle: (keep: CoreKeep) => void;
  onRetry: () => void;
  onRead: () => void;
}

function Side({ label, newer, children }: { label: string; newer?: boolean; children: ReactNode }) {
  return (
    <div role="group" aria-label={label}
      className={cn("min-w-0 rounded-md border px-2.5 py-1.5", newer ? "border-foreground/25 bg-background" : "border-border bg-muted/50")}>
      <p className="text-[11px] font-medium text-muted-foreground">{label}</p>
      {children}
    </div>
  );
}

/** One open conflict as a review row: the earlier text(s) and the newer
 * one side by side (stacked below 620 px), why they conflict, then where the
 * newer came from. A record settles by keeping the newer, the earlier or
 * both; Organize's rewrite of a guideline overwrites it or is left out. */
export function conflictRow(memory: CoreMemory, ctx: RowContext, o: ConflictRowOptions): RowParts {
  const rewrite = !isRecord(memory);
  const entries = rewrite ? memory.conflicts.filter((c) => c.id === memory.amends) : openConflicts(memory);
  const guideline = rewrite ? amended(memory, o.byId) : undefined;
  const earlier = rewrite ? (guideline ? [guideline] : [])
    : entries.map((c) => o.byId.get(c.id)).filter((m): m is CoreMemory => !!m);
  const reasons = [...new Set((entries.length ? entries : memory.conflicts).map((c) => c.reason.trim()).filter(Boolean))];
  const source = sourceOf(memory, o.byId);
  const text = (value: string) => <p className={ctx.expanded ? "text-sm leading-relaxed" : "line-clamp-3 text-sm leading-relaxed"}>{value}</p>;
  const button = (keep: CoreKeep, label: string, letter: string, icon: ReactNode, variant: "default" | "outline") => (
    <ActionButton key={keep} icon={icon} label={label} hint={`${label} (${letter})`} variant={variant} tabIndex={ctx.tabIndex}
      onClick={() => o.onSettle(keep)} />
  );
  return {
    body: (
      <>
        <div className="grid gap-2 @min-[620px]/page:grid-cols-2">
          <Side label={rewrite ? "Your guideline" : earlier.length > 1 ? "Earlier ones" : "Earlier"}>
            {earlier.length
              ? earlier.map((m) => <div key={m.id}>{text(m.text)}</div>)
              : <p className="text-sm text-muted-foreground italic">{rewrite ? "That guideline is no longer listed." : "The earlier one is no longer listed."}</p>}
          </Side>
          <Side label={rewrite ? "Rewrite from newer information" : "Newer"} newer>{text(memory.text)}</Side>
        </div>
        {reasons.length > 0 && (
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
            <span className="font-medium text-foreground">Why they conflict:</span> {reasons.join(" · ")}
          </p>
        )}
        <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          <Badge variant="outline" className="h-4 px-1.5 text-[11px] font-normal">{originText(memory)}</Badge>
          {rewrite && <CostNote memory={memory} replaces={guideline} used={o.used} budget={o.budget} />}
          <span>{rewrite ? `proposed ${ago(memory.created_at)}` : `recorded ${ago(memory.created_at)}`}</span>
        </p>
      </>
    ),
    actions: rewrite ? (
      <>
        {button("newer", "Overwrite", "A", <Check />, "default")}
        {button("older", "Leave as is", "R", <X />, "outline")}
      </>
    ) : (
      <>
        {button("newer", "Use the newer", "A", <Check />, "default")}
        {button("older", "Keep the earlier", "R", <History />, "outline")}
        {button("both", "Keep both", "B", <Layers />, "outline")}
      </>
    ),
    details: ctx.expanded && source ? (
      <Button variant="outline" size="sm" tabIndex={ctx.tabIndex} onClick={o.onRead} className="mt-1">
        <BookOpenText /> Read where it came from <Kbd>O</Kbd>
      </Button>
    ) : undefined,
    error: o.error ? <RowError message={o.error} tabIndex={ctx.tabIndex} onRetry={o.onRetry} /> : undefined,
  };
}
