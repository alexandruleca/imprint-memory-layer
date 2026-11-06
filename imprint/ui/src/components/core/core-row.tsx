"use client";

import { useState } from "react";
import { BookOpenText, Check, ChevronDown, ChevronRight, Pencil, RotateCcw, Sparkles, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Textarea } from "@/components/ui/textarea";
import { ActionButton, RowError, type RowContext, type RowParts } from "@/components/review/review-list";
import type { CoreKind, CoreMemory } from "@/lib/api";
import { ago } from "@/lib/queue";

export const KINDS: { value: CoreKind; label: string }[] = [
  { value: "preference", label: "Preference" },
  { value: "instruction", label: "Instruction" },
  { value: "fact", label: "Fact about me" },
];
export const EVERYWHERE = "*";
export const scopeLabel = (scope: string) => (scope === EVERYWHERE ? "Every project" : scope);

export const isRecord = (memory: CoreMemory) => memory.tier === "record";

/** What a guideline takes of a session's budget, in tokens of about four
 * characters: its line, "- text" (with " (in project)" for one project),
 * and the line break (core_memories.used_budget). */
export function tokenCost(text: string, scope: string = EVERYWHERE) {
  const line = scope === EVERYWHERE ? text : `${text} (in ${scope})`;
  return Math.ceil((line.length + 3) / 4);
}

const tokens = (n: number) => `${n.toLocaleString()} ${n === 1 ? "token" : "tokens"}`;

/** How many active every-project guidelines do not fit in a session, the
 * way core_memories.session_text fills it: oldest first, until the budget
 * ends. Records are never sent, so they never count. */
export function overflow(memories: CoreMemory[], budget: number) {
  const every = memories.filter((m) => !isRecord(m) && m.state === "active" && m.scope === EVERYWHERE)
    .sort((a, b) => a.created_at - b.created_at);
  let used = 0;
  for (let i = 0; i < every.length; i++) {
    const cost = tokenCost(every[i].text);
    if (used + cost > budget) return every.length - i;
    used += cost;
  }
  return 0;
}

/** The records a guideline generalises that are still listed. */
export const coveredBy = (memory: CoreMemory, byId: ReadonlyMap<string, CoreMemory>) =>
  memory.covers.map((id) => byId.get(id)).filter((m): m is CoreMemory => !!m);

/** The guideline a proposal would replace, while it is listed. */
export const amended = (memory: CoreMemory, byId: ReadonlyMap<string, CoreMemory>) =>
  (memory.amends ? byId.get(memory.amends) : undefined);

/** Where a core memory came from: its own evidence, else, for a guideline,
 * the evidence of the records it generalises. */
export function sourceOf(memory: CoreMemory, byId: ReadonlyMap<string, CoreMemory>) {
  return memory.evidence.find((e) => e.source)
    ?? coveredBy(memory, byId).flatMap((m) => m.evidence).find((e) => e.source);
}

export function originText(memory: CoreMemory) {
  if (memory.origin === "user") return "Written by you";
  const project = memory.evidence.find((e) => e.project)?.project;
  return memory.origin === "organize" ? `From Organize${project ? ` · ${project}` : ""}` : `From an agent${project ? ` · ${project}` : ""}`;
}

/** Kind and scope, as native selects so they stay small inside a row. */
export function KindScope({ kind, scope, projects, onKind, onScope, label, tabIndex }: {
  kind: CoreKind; scope: string; projects: string[]; label: string; tabIndex?: number;
  onKind: (kind: CoreKind) => void; onScope: (scope: string) => void;
}) {
  const scopes = [EVERYWHERE, ...projects, ...(scope !== EVERYWHERE && !projects.includes(scope) ? [scope] : [])];
  return (
    <div className="flex flex-wrap gap-1.5 text-xs">
      <select aria-label={`Kind of ${label}`} value={kind} tabIndex={tabIndex} onChange={(e) => onKind(e.target.value as CoreKind)}
        className="h-7 rounded-md border border-input bg-card px-1.5 text-foreground">
        {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
      </select>
      <select aria-label={`Where ${label} applies`} value={scope} tabIndex={tabIndex} onChange={(e) => onScope(e.target.value)}
        className="h-7 max-w-40 rounded-md border border-input bg-card px-1.5 text-foreground">
        {scopes.map((s) => <option key={s} value={s}>{scopeLabel(s)}</option>)}
      </select>
    </div>
  );
}

/** The inline editor: Escape cancels, ⌘/Ctrl+Enter saves. */
export function CoreEditor({ memory, maxText, onSave, onCancel }: {
  memory: CoreMemory; maxText: number; onSave: (text: string) => void; onCancel: () => void;
}) {
  const [draft, setDraft] = useState(memory.text);
  const changed = draft.trim() !== "" && draft !== memory.text;
  return (
    <div className="space-y-2" data-row-details="">
      <Textarea aria-label="Core memory text" value={draft} maxLength={maxText} autoFocus
        onChange={(e) => setDraft(e.target.value)}
        onFocus={(e) => e.currentTarget.setSelectionRange(e.currentTarget.value.length, e.currentTarget.value.length)}
        onKeyDown={(e) => {
          if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onCancel(); }
          else if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && changed) { e.preventDefault(); onSave(draft); }
        }} />
      <div className="flex gap-2">
        <Button size="sm" disabled={!changed} onClick={() => onSave(draft)}><Check /> Save <Kbd className="bg-primary-foreground/15 text-primary-foreground">⌘↵</Kbd></Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

export type CoreState = CoreMemory["state"];

export interface CoreRowOptions {
  projects: string[];
  maxText: number;
  budget: number;
  used: number;
  editing: boolean;
  /** Every core memory by id: what a guideline generalises, what a proposal replaces. */
  byId: ReadonlyMap<string, CoreMemory>;
  /** Records an active guideline generalises. */
  covered: ReadonlySet<string>;
  error?: string;
  onState: (state: CoreState) => void;
  onEdit: (on: boolean) => void;
  onSave: (text: string) => void;
  onKind: (kind: CoreKind) => void;
  onScope: (scope: string) => void;
  onRetry: () => void;
  /** Show or hide what the row generalises. */
  onExpand: () => void;
  /** A record's "Make a guideline". */
  onGuideline: () => void;
  onRead: () => void;
}

/** What approving a proposal adds to the sessions it applies to: its cost,
 * less that of the guideline it replaces there. */
function approvalCost(memory: CoreMemory, replaces: CoreMemory | undefined) {
  const cost = tokenCost(memory.text, memory.scope);
  return replaces?.state === "active" && replaces.scope === memory.scope ? cost - tokenCost(replaces.text, replaces.scope) : cost;
}

/** "+N tokens" a proposal adds to a session, and whether that passes the budget. */
export function CostNote({ memory, replaces, used, budget }: {
  memory: CoreMemory; replaces: CoreMemory | undefined; used: number; budget: number;
}) {
  const cost = approvalCost(memory, replaces);
  const over = memory.scope === EVERYWHERE && cost > 0 && used + cost > budget;
  return (
    <span className={over ? "font-medium text-amber-700" : undefined}
      title={memory.scope === EVERYWHERE ? "Tokens approving it adds to every session" : `Tokens approving it adds to sessions in ${memory.scope}`}>
      {cost < 0 ? `−${tokens(-cost)}` : `+${tokens(cost)}`}{over && " · over budget"}
    </span>
  );
}

/** A record's conflicts with older records that are not settled yet. */
export const openConflicts = (memory: CoreMemory) => memory.conflicts.filter((c) => !c.resolution);

/** Whether a core memory waits on Conflicts: an active record that
 * contradicts older ones, or Organize's rewrite of a guideline after newer
 * statements contradicted it, while it is proposed. */
export function isConflict(memory: CoreMemory) {
  if (isRecord(memory)) return memory.state === "active" && openConflicts(memory).length > 0;
  return memory.state === "proposed" && !!memory.amends && memory.conflicts.length > 0;
}

/** One core memory as a review row (spec §5): the text, what a proposal
 * replaces, then its quote, where it came from, what it costs a session,
 * what it generalises and its age; kind and scope beside the actions, under
 * the text below 620 px. Expanded, a guideline lists the recorded core
 * memories it generalises. */
export function coreRow(memory: CoreMemory, ctx: RowContext, o: CoreRowOptions): RowParts {
  const record = isRecord(memory);
  const quote = memory.evidence.find((e) => e.quote)?.quote;
  const label = `“${memory.text.slice(0, 40)}”`;
  const covers = record ? [] : coveredBy(memory, o.byId);
  const replaces = memory.state === "proposed" ? amended(memory, o.byId) : undefined;
  const source = sourceOf(memory, o.byId);
  const when = memory.state === "proposed" ? `proposed ${ago(memory.created_at)}`
    : memory.state === "retired" ? `retired ${ago(memory.updated_at)}`
      : record ? `recorded ${ago(memory.created_at)}` : `added ${ago(memory.approved_at ?? memory.updated_at)}`;
  const edit = memory.state !== "retired" && !o.editing && (
    <ActionButton key="edit" icon={<Pencil />} label="Edit" hint="Edit (E)" variant="ghost" tabIndex={ctx.tabIndex}
      onClick={() => o.onEdit(true)} className="[&>span]:sr-only px-1.5" />
  );
  const actions = memory.state === "proposed" ? (
    <>
      <ActionButton icon={<Check />} label="Approve" hint="Approve (A)" variant="default" tabIndex={ctx.tabIndex} onClick={() => o.onState("active")} />
      <ActionButton icon={<X />} label="Reject" hint="Reject (R)" tabIndex={ctx.tabIndex} onClick={() => o.onState("retired")} />
      {edit}
    </>
  ) : memory.state === "active" ? (
    <>
      {edit}
      {record && (
        <ActionButton icon={<Sparkles />} label="Make a guideline" hint="Make a guideline from it (G)" tabIndex={ctx.tabIndex} onClick={o.onGuideline} />
      )}
      <ActionButton icon={<X />} label="Retire" hint="Retire (R)" tabIndex={ctx.tabIndex} onClick={() => o.onState("retired")} />
    </>
  ) : <ActionButton icon={<RotateCcw />} label="Restore" hint="Restore (A)" tabIndex={ctx.tabIndex} onClick={() => o.onState("active")} />;
  const details = ctx.expanded && !o.editing && (covers.length > 0 || source) ? (
    <div className="mt-1 space-y-2 rounded-lg border border-border bg-background p-3 text-sm">
      {covers.length > 0 && (
        <div role="group" aria-label="Recorded core memories it generalises" className="space-y-1">
          <p className="text-xs font-medium text-muted-foreground">Recorded core memories it generalises</p>
          {covers.map((m) => (
            <p key={m.id} className="border-l-2 border-border pl-2 leading-relaxed">
              {m.text}{m.state === "retired" && <span className="text-xs text-muted-foreground"> · retired</span>}
            </p>
          ))}
        </div>
      )}
      {source && (
        <Button variant="outline" size="sm" tabIndex={ctx.tabIndex} onClick={o.onRead}>
          <BookOpenText /> Read where it came from <Kbd>O</Kbd>
        </Button>
      )}
    </div>
  ) : undefined;
  return {
    body: (
      <>
        {o.editing
          ? <CoreEditor memory={memory} maxText={o.maxText} onSave={o.onSave} onCancel={() => o.onEdit(false)} />
          : <p className={ctx.expanded ? "text-sm leading-relaxed" : "line-clamp-2 text-sm leading-relaxed"}>{memory.text}</p>}
        {replaces && (
          <div role="group" aria-label="Replaces" className="mt-1.5 rounded-r-md border-l-2 border-amber-600/60 bg-muted/60 px-2 py-1 text-xs leading-relaxed">
            <span className="font-medium text-foreground">Replaces</span>{" "}
            <span className="text-muted-foreground">{replaces.text}</span>
          </div>
        )}
        <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          {memory.state === "retired" && (
            <Badge variant="secondary" className="h-4 px-1.5 text-[11px]">{record ? "Recorded" : "Guideline"}</Badge>
          )}
          {quote && <span className="min-w-0 italic">“{quote}”</span>}
          <Badge variant="outline" className="h-4 px-1.5 text-[11px] font-normal">{originText(memory)}</Badge>
          {record && memory.state === "active" && o.covered.has(memory.id) && (
            <Badge variant="outline" className="h-4 px-1.5 text-[11px] font-normal">In a guideline</Badge>
          )}
          {record && isConflict(memory) && (
            <Badge variant="outline" className="h-4 border-amber-600/50 px-1.5 text-[11px] font-normal text-amber-800 dark:text-amber-300">
              Conflicts with an earlier one
            </Badge>
          )}
          {covers.length > 0 && (
            <button type="button" aria-expanded={ctx.expanded} tabIndex={ctx.tabIndex} onClick={o.onExpand}
              className="inline-flex items-center gap-0.5 rounded-sm whitespace-nowrap underline underline-offset-2 outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
              {ctx.expanded ? <ChevronDown className="size-3" aria-hidden /> : <ChevronRight className="size-3" aria-hidden />}
              Generalises {covers.length} recorded
            </button>
          )}
          {memory.state === "proposed" && !record && <CostNote memory={memory} replaces={replaces} used={o.used} budget={o.budget} />}
          <span>{when}</span>
        </p>
      </>
    ),
    meta: memory.state !== "retired" ? (
      <KindScope kind={memory.kind} scope={memory.scope} projects={o.projects} label={label} tabIndex={ctx.tabIndex}
        onKind={o.onKind} onScope={o.onScope} />
    ) : undefined,
    actions,
    details,
    error: o.error ? <RowError message={o.error} tabIndex={ctx.tabIndex} onRetry={o.onRetry} /> : undefined,
  };
}
