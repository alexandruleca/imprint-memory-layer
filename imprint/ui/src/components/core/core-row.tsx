"use client";

import { useState } from "react";
import { Check, Pencil, RotateCcw, X } from "lucide-react";
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

/** What a core memory takes of every session's budget when it applies to
 * every project: its line, "- text", and the line break (core_memories.used_budget). */
export const budgetCost = (text: string) => text.length + 3;

/** How many every-project core memories do not fit in a session, the way
 * core_memories.session_text fills it: oldest first, until the budget ends. */
export function overflow(memories: CoreMemory[], budget: number) {
  const every = memories.filter((m) => m.state === "active" && m.scope === EVERYWHERE).sort((a, b) => a.created_at - b.created_at);
  let used = 0;
  for (let i = 0; i < every.length; i++) {
    if (used + every[i].text.length + 3 > budget) return every.length - i;
    used += every[i].text.length + 3;
  }
  return 0;
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
  error?: string;
  onState: (state: CoreState) => void;
  onEdit: (on: boolean) => void;
  onSave: (text: string) => void;
  onKind: (kind: CoreKind) => void;
  onScope: (scope: string) => void;
  onRetry: () => void;
}

/** One core memory as a review row (spec §5): the text, then its quote,
 * where it came from, what it costs a session and its age; kind and scope
 * beside the actions, under the text below 620 px. */
export function coreRow(memory: CoreMemory, ctx: RowContext, o: CoreRowOptions): RowParts {
  const quote = memory.evidence.find((e) => e.quote)?.quote;
  const label = `“${memory.text.slice(0, 40)}”`;
  const cost = budgetCost(memory.text);
  const over = o.used + cost > o.budget;
  const when = memory.state === "proposed" ? `proposed ${ago(memory.created_at)}`
    : memory.state === "active" ? `added ${ago(memory.approved_at ?? memory.updated_at)}` : `retired ${ago(memory.updated_at)}`;
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
      <ActionButton icon={<X />} label="Retire" hint="Retire (R)" tabIndex={ctx.tabIndex} onClick={() => o.onState("retired")} />
    </>
  ) : <ActionButton icon={<RotateCcw />} label="Restore" hint="Restore (A)" tabIndex={ctx.tabIndex} onClick={() => o.onState("active")} />;
  return {
    body: (
      <>
        {o.editing
          ? <CoreEditor memory={memory} maxText={o.maxText} onSave={o.onSave} onCancel={() => o.onEdit(false)} />
          : <p className={ctx.expanded ? "text-sm leading-relaxed" : "line-clamp-2 text-sm leading-relaxed"}>{memory.text}</p>}
        <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          {quote && <span className="min-w-0 italic">“{quote}”</span>}
          <Badge variant="outline" className="h-4 px-1.5 text-[11px] font-normal">{originText(memory)}</Badge>
          {memory.state === "proposed" && memory.scope === EVERYWHERE && (
            <span className={over ? "font-medium text-amber-700" : undefined} title="Characters this takes of every session's budget">
              +{cost} chars{over && " · over budget"}
            </span>
          )}
          <span>{when}</span>
        </p>
      </>
    ),
    meta: memory.state !== "retired" ? (
      <KindScope kind={memory.kind} scope={memory.scope} projects={o.projects} label={label} tabIndex={ctx.tabIndex}
        onKind={o.onKind} onScope={o.onScope} />
    ) : undefined,
    actions,
    error: o.error ? <RowError message={o.error} tabIndex={ctx.tabIndex} onRetry={o.onRetry} /> : undefined,
  };
}
