"use client";

import { useUiMode } from "@/lib/ui-mode";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Plus, RefreshCw, Check, History, Layers, RotateCcw, Sparkles, X } from "lucide-react";
import { ListSkeleton } from "@/components/loaders";
import { SourceReader, type ReaderTarget } from "@/components/source-reader";
import { AddCoreDialog } from "@/components/core/add-core-dialog";
import { conflictRow } from "@/components/core/conflict-row";
import {
  EVERYWHERE, coreRow, coveredBy, isConflict, isRecord, overflow, scopeLabel, sourceOf, type CoreState,
} from "@/components/core/core-row";
import { Facets, FromFilter } from "@/components/review/filters";
import { PAGE_SIZES } from "@/components/review/pager";
import { CountTab, ReviewHeader } from "@/components/review/review-header";
import {
  Announcer, RESET, ReviewEmpty, ReviewList, reviewTab, useAnnounce, useReviewNav, type ReviewTabState,
} from "@/components/review/review-list";
import { ShortcutsButton, ShortcutsDialog, type Shortcut } from "@/components/review/shortcuts-dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList } from "@/components/ui/tabs";
import {
  addCoreMemory, changeCoreMemory, getCoreMemories, resolveCoreConflict,
  type CoreKeep, type CoreKind, type CoreMemories, type CoreMemory,
} from "@/lib/api";
import { useHashTab } from "@/lib/use-hash-tab";
import { useReviewShortcuts } from "@/lib/use-review-shortcuts";
import { useStoredNumber } from "@/lib/use-stored";
import { useDecisions } from "@/lib/write-queue";
import { useProjectNames } from "@/lib/use-projects";

const INFO = (
  <div className="space-y-2">
    <p><span className="font-medium">Guidelines</span> are what every agent session starts with: how you like things done, rules to follow, facts about you. Organize drafts them from recorded core memories and only you approve them. You can also write your own.</p>
    <p><span className="font-medium">Recorded</span> core memories are the specific things agents and Organize noticed about you. They are kept for reference and search, and sessions do not receive them.</p>
    <p>Newer information that contradicts an older core memory is shown under <span className="font-medium">Conflicts</span> for you to settle.</p>
  </div>
);
const SIMPLE_INFO = (
  <div className="space-y-2">
    <p>What Imprint knows about you: how you like things done, rules to follow and facts about you. Your AI tools are told these at the start of every session, so you do not have to repeat yourself.</p>
    <p>Imprint suggests new ones from what it notices, and nothing is used until you say yes. You can also write your own.</p>
    <p>When something newer disagrees with something older, you choose which one holds.</p>
  </div>
);
const TABS = ["conflicts", "guidelines", "proposed", "recorded", "retired"] as const;
type Tab = (typeof TABS)[number];
const LISTS: Record<Tab, { section: string; list: string }> = {
  conflicts: { section: "Conflicts", list: "Conflicts to settle" },
  guidelines: { section: "Guidelines", list: "Active guidelines" },
  proposed: { section: "Proposed", list: "Proposed guidelines" },
  recorded: { section: "Recorded", list: "Recorded core memories" },
  retired: { section: "Retired and rejected", list: "Retired core memories" },
};
const UNDO_TOAST = "core-undo";
const KIND_FACETS = [
  { value: "all", label: "All" }, { value: "preference", label: "Preference" },
  { value: "instruction", label: "Instruction" }, { value: "fact", label: "Fact" },
];
const ORIGINS = [{ value: "organize", label: "Organize" }, { value: "agent", label: "Agents" }, { value: "user", label: "You" }];
const LAYERS = [{ value: "guideline", label: "Guidelines" }, { value: "record", label: "Recorded" }];
const SHORTCUTS: Shortcut[] = [
  [["j", "↓"], "Next row"], [["k", "↑"], "Previous row"], [["x"], "Select the row"],
  [["a"], "Approve · Restore · Use the newer or overwrite"], [["r"], "Reject · Retire · Keep the earlier or leave as is"],
  [["b"], "Keep both conflicting ones"], [["e"], "Edit the text"], [["g"], "Make a guideline from recorded ones"],
  [["Enter"], "Show the whole text and what a guideline generalises"], [["o"], "Read where it came from"],
  [["z", "⌘Z"], "Undo the last action"], [["[", "]"], "Previous / next page"], [["/"], "Search"],
  [["1", "5"], "Switch tab"], [["n"], "Write a guideline"], [["Esc"], "Clear the selection"], [["?"], "This list"],
];

/** What the page writes: a state (PATCH), or how a record's conflicts settle
 * (POST resolve), where "open" undoes a settlement. */
type Decision = CoreState | CoreKeep | "open";
const isSettlement = (decision: Decision): decision is CoreKeep | "open" =>
  decision === "newer" || decision === "older" || decision === "both" || decision === "open";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const short = (memory: CoreMemory) => `“${memory.text.length > 44 ? `${memory.text.slice(0, 42)}…` : memory.text}”`;

/** The tab a core memory is listed in; an open conflict's record is also under Recorded. */
function inTab(memory: CoreMemory, tab: Tab) {
  if (tab === "conflicts") return isConflict(memory);
  if (tab === "retired") return memory.state === "retired";
  if (memory.state === "retired") return false;
  if (isRecord(memory)) return tab === "recorded";
  if (memory.state === "active") return tab === "guidelines";
  return tab === "proposed" && !isConflict(memory);
}

function noun(items: CoreMemory[]) {
  if (items.length === 1) return short(items[0]);
  if (items.every(isRecord)) return `${items.length} recorded core memories`;
  return items.some(isRecord) ? `${items.length} core memories` : `${items.length} guidelines`;
}

/** What the undo toast says was done. */
function decisionNote(to: Decision, items: CoreMemory[]) {
  const from = items[0].state;
  const what = noun(items);
  const rewrite = from === "proposed" && items.every(isConflict);
  switch (to) {
    case "newer": return `Used the newer: ${what}`;
    case "older": return `Kept the earlier over ${what}`;
    case "both": return `Kept both for ${what}`;
    case "open": return `Reopened ${what}`;
    case "active":
      if (rewrite) return `Overwrote ${items.length === 1 ? "your guideline" : "your guidelines"} with ${what}`;
      return `${from === "retired" ? "Restored" : "Approved"} ${what}`;
    case "retired":
      if (rewrite) return `Left ${items.length === 1 ? "your guideline" : "your guidelines"} as is`;
      return `${from === "proposed" ? "Rejected" : "Retired"} ${what}`;
    default: return `Returned to proposals ${what}`;
  }
}

/** The API's core memories with the fields an older server leaves out. */
function normalize(memory: CoreMemory): CoreMemory {
  return { ...memory, tier: memory.tier === "record" ? "record" : "guideline", amends: memory.amends ?? "",
           covers: Array.isArray(memory.covers) ? memory.covers : [],
           conflicts: Array.isArray(memory.conflicts) ? memory.conflicts : [] };
}

function CoreTab({ tab, memories, data, projects, byId, covered, state, onState, size, onSize, root, saving, errors, editing,
                   onEdit, decide, retry, change, read, onWrite }: {
  tab: Tab; memories: CoreMemory[]; data: CoreMemories; projects: string[];
  byId: ReadonlyMap<string, CoreMemory>; covered: ReadonlySet<string>;
  state: ReviewTabState; onState: (patch: Partial<ReviewTabState>) => void; size: number; onSize: (size: number) => void;
  root: React.RefObject<HTMLElement | null>; saving: number; errors: ReadonlyMap<string, { error: string }>;
  editing: string | null; onEdit: (id: string | null) => void;
  decide: (to: Decision, ids: string[]) => void; retry: (id: string) => void;
  change: (memory: CoreMemory, values: Partial<Pick<CoreMemory, "text" | "kind" | "scope">>) => void;
  read: (memory: CoreMemory) => void;
  /** Open the dialog to write a guideline, made from these records when any. */
  onWrite: (covers?: CoreMemory[]) => void;
}) {
  const announce = useAnnounce();
  const conflicts = tab === "conflicts";
  const base = memories.filter((m) => inTab(m, tab));
  // `group` holds the Scope filter here: core memories have no rail; `view`
  // the Retired tab's layer.
  const scope = state.group ?? "";
  const layer = tab === "retired" ? state.view ?? "" : "";
  const query = state.query.trim().toLowerCase();
  const searched = (m: CoreMemory) => [
    m.text, m.scope, ...m.evidence.map((e) => e.quote ?? ""), ...coveredBy(m, byId).map((r) => r.text),
    ...(conflicts ? m.conflicts.flatMap((c) => [c.reason, byId.get(c.id)?.text ?? ""]) : []),
  ];
  const list = base
    .filter((m) => state.filter === "all" || m.kind === state.filter)
    .filter((m) => !scope || m.scope === scope)
    .filter((m) => !state.from || m.origin === state.from)
    .filter((m) => !layer || m.tier === layer)
    .filter((m) => !query || searched(m).join("\n").toLowerCase().includes(query))
    .sort((a, b) => (tab === "proposed" || conflicts ? b.created_at - a.created_at : b.updated_at - a.updated_at));
  const nav = useReviewNav({ items: list, getKey: (m: CoreMemory) => m.id, state, onState, size });
  const filtered = !!query || state.filter !== "all" || !!scope || !!state.from || !!layer;
  const shown = new Map(list.map((m) => [m.id, m]));

  const act = (to: CoreState, ids: string[]) => {
    if (!ids.length) { announce("Choose a row first: j and k move between rows"); return; }
    onState(nav.after(ids));
    decide(to, ids);
  };
  /** Settle conflicts: records by what they keep; a rewrite of a guideline
   * is approved ("newer") or rejected ("older"), and has no "both". */
  const settle = (keep: CoreKeep, ids: string[]) => {
    if (!ids.length) { announce("Choose a row first: j and k move between rows"); return; }
    const records = ids.filter((id) => { const m = shown.get(id); return !!m && isRecord(m); });
    const rewrites = keep === "both" ? [] : ids.filter((id) => !records.includes(id));
    if (!records.length && !rewrites.length) { announce("A rewrite is overwritten or left as is; Keep both is for recorded ones"); return; }
    onState(nav.after([...records, ...rewrites]));
    if (records.length) decide(keep, records);
    if (rewrites.length) decide(keep === "newer" ? "active" : "retired", rewrites);
  };
  const make = (ids: string[]) => {
    if (!ids.length) { announce("Choose a row first: j and k move between rows"); return; }
    onWrite(ids.map((id) => shown.get(id)).filter((m): m is CoreMemory => !!m));
  };
  const nothing = () => announce("Nothing to do with that key here");
  const approves = tab === "proposed" || tab === "retired";
  useReviewShortcuts(root, {
    j: () => nav.move(1), ArrowDown: () => nav.move(1), k: () => nav.move(-1), ArrowUp: () => nav.move(-1),
    x: () => { if (nav.current) nav.toggle(nav.current, false); },
    a: conflicts ? () => settle("newer", nav.targets()) : approves ? () => act("active", nav.targets()) : nothing,
    r: conflicts ? () => settle("older", nav.targets()) : tab === "retired" ? nothing : () => act("retired", nav.targets()),
    A: conflicts ? () => settle("newer", nav.targets()) : approves ? () => act("active", nav.targets()) : nothing,
    R: conflicts ? () => settle("older", nav.targets()) : tab === "retired" ? nothing : () => act("retired", nav.targets()),
    b: conflicts ? () => settle("both", nav.targets()) : nothing,
    g: tab === "recorded" ? () => make(nav.targets()) : nothing,
    u: nothing,
    e: tab === "retired" || conflicts ? nothing : () => { if (nav.current) onEdit(nav.current); },
    Enter: () => { if (nav.current) nav.expand(nav.current); },
    o: () => { const m = nav.current ? shown.get(nav.current) : undefined; if (m) read(m); },
    "[": () => nav.goTo(nav.page - 1, true),
    "]": () => nav.goTo(nav.page + 1, true),
    "/": () => document.getElementById("review-search")?.focus(),
    Escape: () => {
      if (state.selected.size) nav.clear();
      else if (state.expanded) onState({ expanded: null });
    },
  });

  const scopes = [...new Set(base.map((m) => m.scope))].sort((a, b) => (a === EVERYWHERE ? -1 : b === EVERYWHERE ? 1 : a.localeCompare(b)));
  const origins = ORIGINS.filter((origin) => base.some((m) => m.origin === origin.value || (origin.value === "agent" && m.origin !== "user" && m.origin !== "organize")));
  const layers = LAYERS.filter((option) => base.some((m) => m.tier === option.value));
  const filters = conflicts ? undefined : (
    <>
      <Facets label="Kind" value={state.filter} onChange={(filter) => onState({ ...RESET, filter })}
        facets={KIND_FACETS.map((facet) => ({ ...facet, count: base.filter((m) => facet.value === "all" || m.kind === facet.value).length }))} />
      {tab === "retired" && layers.length > 1 && (
        <FromFilter label="Layer" anyLabel="Guidelines and recorded" value={layer} onChange={(next) => onState({ ...RESET, view: next || null })}
          options={layers} />
      )}
      {scopes.length > 1 && (
        <FromFilter label="Scope" anyLabel="Any scope" value={scope} onChange={(next) => onState({ ...RESET, group: next })}
          options={scopes.map((s) => ({ value: s, label: scopeLabel(s) }))} />
      )}
      {origins.length > 1 && (
        <FromFilter label="From" anyLabel="From anyone" value={state.from} onChange={(from) => onState({ ...RESET, from })} options={origins} />
      )}
    </>
  );
  const bulk = (ids: string[]) => {
    const n = ids.length;
    if (conflicts) return (
      <>
        <Button size="sm" onClick={() => settle("newer", ids)}><Check /> Use the newer {n}</Button>
        <Button size="sm" variant="outline" onClick={() => settle("older", ids)}><History /> Keep the earlier {n}</Button>
        {ids.some((id) => { const m = shown.get(id); return !!m && isRecord(m); }) && (
          <Button size="sm" variant="outline" onClick={() => settle("both", ids)}><Layers /> Keep both</Button>
        )}
      </>
    );
    if (tab === "proposed") return (
      <>
        <Button size="sm" onClick={() => act("active", ids)}><Check /> Approve {n}</Button>
        <Button size="sm" variant="outline" onClick={() => act("retired", ids)}><X /> Reject {n}</Button>
      </>
    );
    if (tab === "recorded") return (
      <>
        <Button size="sm" variant="outline" onClick={() => make(ids)}><Sparkles /> Make a guideline from {n}</Button>
        <Button size="sm" variant="outline" onClick={() => act("retired", ids)}><X /> Retire {n}</Button>
      </>
    );
    if (tab === "guidelines") return <Button size="sm" variant="outline" onClick={() => act("retired", ids)}><X /> Retire {n}</Button>;
    return <Button size="sm" variant="outline" onClick={() => act("active", ids)}><RotateCcw /> Restore {n}</Button>;
  };
  const write = <Button variant="outline" data-autofocus="" onClick={() => onWrite()}><Plus /> Write a guideline</Button>;
  const empty = conflicts ? (
    <ReviewEmpty title="No conflicts." description="When newer information contradicts an older core memory, both are shown here for you to settle." />
  ) : tab === "guidelines" ? (
    <ReviewEmpty title="No guidelines yet." description="Organize drafts guidelines from your recorded core memories for you to approve, and you can write one. Sessions start without any until then.">
      {write}
    </ReviewEmpty>
  ) : tab === "proposed" ? (
    <ReviewEmpty title="No proposals." description="Organize proposes a guideline when recorded core memories share a pattern, and a rewrite of one you have when new ones fit it.">
      {write}
    </ReviewEmpty>
  ) : tab === "recorded" ? (
    <ReviewEmpty title="Nothing recorded yet." description="Agents and Organize record the specific things they notice about you here. They are kept for reference and never sent to agents." />
  ) : <ReviewEmpty title="Nothing retired." description="Retired guidelines, rejected proposals and recorded core memories you removed are kept here and can be restored." />;

  return (
    <section aria-label={LISTS[tab].section} className="flex min-h-0 min-w-0 flex-col">
      <ReviewList nav={nav} state={state} onState={onState} size={size} onSize={onSize}
        label={LISTS[tab].list} rowLabel={(m) => m.text} searchLabel="Search text and quotes" filters={filters} filtered={filtered}
        onClearFilters={() => onState({ ...RESET, query: "", filter: "all", from: "", group: "", view: null })}
        noMatch="No core memories match these filters." bulk={bulk} saving={saving} empty={empty} metaBelow
        row={(m, context) => conflicts ? conflictRow(m, context, {
          byId, budget: data.budget, used: data.used, error: errors.get(m.id)?.error,
          onSettle: (keep) => settle(keep, [m.id]),
          onRetry: () => retry(m.id),
          onRead: () => read(m),
        }) : coreRow(m, context, {
          projects, maxText: data.max_text, budget: data.budget, used: data.used, editing: editing === m.id,
          byId, covered, error: errors.get(m.id)?.error,
          onState: (to) => act(to, [m.id]),
          onEdit: (on) => onEdit(on ? m.id : null),
          onSave: (text) => { change(m, { text }); onEdit(null); },
          onKind: (kind) => change(m, { kind }),
          onScope: (next) => change(m, { scope: next }),
          onRetry: () => retry(m.id),
          onExpand: () => nav.expand(m.id),
          onGuideline: () => onWrite([m]),
          onRead: () => read(m),
        })} />
    </section>
  );
}

export default function CoreMemoriesPage() {
  const root = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<CoreMemories | null>(null);
  const [error, setError] = useState("");
  const projects = useProjectNames({ order: "name" }).names;
  const [tabs, setTabs] = useState<Record<Tab, ReviewTabState>>(
    () => Object.fromEntries(TABS.map((t) => [t, reviewTab()])) as Record<Tab, ReviewTabState>);
  const [editing, setEditing] = useState<string | null>(null);
  // The dialog, and the records a guideline is made from. A new key starts
  // it afresh; writing one by hand again keeps an unsent draft.
  const [dialog, setDialog] = useState<{ open: boolean; key: number; covers: CoreMemory[] }>({ open: false, key: 0, covers: [] });
  const [help, setHelp] = useState(false);
  const [reading, setReading] = useState<ReaderTarget | null>(null);
  const [linked, openTab] = useHashTab(TABS);
  const { simple } = useUiMode();
  const [size, setSize] = useStoredNumber("imprint.review.page-size", 20, PAGE_SIZES);
  const reload = useRef<() => void>(() => {});
  // The tab a link without a hash opens, chosen once from the first load so
  // settling the last conflict or proposal does not switch tabs under the cursor.
  const [fallback, setFallback] = useState<Tab | null>(null);

  const engine = useDecisions<Decision, Decision>({
    send: async (to, ids, keepalive) => {
      const failed: { key: string; error: string }[] = [];
      const init = keepalive ? { keepalive } : undefined;
      for (const id of ids) {
        try {
          if (isSettlement(to)) await resolveCoreConflict(id, to, init);
          else await changeCoreMemory(id, { state: to }, init);
        } catch (e) { failed.push({ key: id, error: message(e) }); }
      }
      return failed;
    },
    result: (to) => to,
    // A settlement's `from` is "open": its undo reopens it.
    inverse: (_to, from) => [from],
    onDrain: () => reload.current(),
    onFailed: (to, failed) => toast.error(`${failed.length} could not be ${isSettlement(to) ? (to === "open" ? "reopened" : "settled")
      : to === "active" ? "made active" : to === "retired" ? "retired" : "returned to proposals"}`, { description: failed[0].error }),
  });
  const prune = engine.prune;

  const absorb = useCallback((next: CoreMemories) => {
    const memories = (Array.isArray(next?.memories) ? next.memories : []).map(normalize);
    setData({ ...next, memories });
    setFallback((prev) => prev ?? (memories.some(isConflict) ? "conflicts"
      : memories.some((m) => inTab(m, "proposed")) ? "proposed" : "guidelines"));
    setError("");
    prune();
  }, [prune]);
  const load = useCallback(async () => {
    try { absorb(await getCoreMemories()); } catch (e) { setError(message(e)); }
  }, [absorb]);
  useEffect(() => { reload.current = () => void load(); }, [load]);
  useEffect(() => {
    let live = true;
    getCoreMemories().then((next) => { if (live) absorb(next); }, (e) => { if (live) setError(message(e)); });
    return () => { live = false; };
  }, [absorb]);

  // Decisions show before the server answers, with what the server does
  // alongside: approving an amendment retires the guideline it replaces and
  // returning it to the proposals restores that one; keeping the newer
  // retires the earlier records, keeping the earlier retires this one, and
  // reopening brings back what the settlement retired.
  const memories = useMemo(() => {
    const listed = data?.memories ?? [];
    const swaps = new Map<string, CoreState>();
    for (const m of listed) {
      const to = engine.overrides.get(m.id);
      if (!to || to === m.state) continue;
      if (to === "newer") m.conflicts.filter((c) => !c.resolution).forEach((c) => swaps.set(c.id, "retired"));
      else if (to === "open") m.conflicts.filter((c) => c.resolution === "newer").forEach((c) => swaps.set(c.id, "active"));
      else if (m.amends && m.state === "proposed" && to === "active") swaps.set(m.amends, "retired");
      else if (m.amends && m.state === "active" && to === "proposed") swaps.set(m.amends, "active");
    }
    return listed.map((m): CoreMemory => {
      const to = engine.overrides.get(m.id);
      if (to && isSettlement(to)) {
        const reopen = to === "open";
        const state = to === "older" ? "retired" : reopen && m.conflicts.some((c) => c.resolution === "older") ? "active" : m.state;
        const conflicts = m.conflicts.map((c) => (reopen ? (c.resolution ? { ...c, resolution: "" as const } : c)
          : c.resolution ? c : { ...c, resolution: to }));
        return { ...m, state, conflicts };
      }
      const swap = swaps.get(m.id);
      const state = to ?? (swap && m.state === (swap === "retired" ? "active" : "retired") ? swap : undefined);
      return state && state !== m.state ? { ...m, state } : m;
    });
  }, [data, engine.overrides]);
  const byId = useMemo(() => new Map(memories.map((m) => [m.id, m])), [memories]);
  const covered = useMemo(() => new Set(memories.filter((m) => !isRecord(m) && m.state === "active").flatMap((m) => m.covers)), [memories]);
  const count = (which: Tab) => memories.filter((m) => inTab(m, which)).length;
  const tab: Tab = linked ?? fallback ?? "guidelines";
  const shownUsed = data?.used ?? 0;
  const left = data ? overflow(memories, data.budget) : 0;

  const patchTab = useCallback((which: Tab, patch: Partial<ReviewTabState>) => {
    setTabs((prev) => ({ ...prev, [which]: { ...prev[which], ...patch } }));
  }, []);

  function undo() {
    const undone = engine.undo();
    if (!undone) { toast("Nothing to undo", { id: UNDO_TOAST }); return; }
    // Functional: the toast's Undo may run from an older render.
    setTabs((prev) => ({ ...prev, [tab]: { ...prev[tab], focus: undone.keys[0], reveal: true, selected: new Set(),
                                            focusToken: prev[tab].focusToken + 1 } }));
    toast(`Undone: ${undone.note.charAt(0).toLowerCase()}${undone.note.slice(1)}`, { id: UNDO_TOAST, duration: 6000 });
  }

  function decide(to: Decision, ids: string[]) {
    const items = ids.map((id) => byId.get(id)).filter((m): m is CoreMemory => !!m);
    if (!items.length) return;
    const note = decisionNote(to, items);
    const replaced = to === "active" && items.length === 1 && items[0].state === "proposed" ? byId.get(items[0].amends) : undefined;
    const description = to === "newer" ? `The earlier ${items.length === 1 ? "one is" : "ones are"} retired.`
      : to === "older" ? `The newer ${items.length === 1 ? "one is" : "ones are"} retired.`
        : replaced ? `It replaces ${short(replaced)} from the next session start.`
          : to === "active" && !items.some(isRecord) ? "Sessions receive it from their next start." : undefined;
    engine.decide(to, items.map((m) => m.id), new Map(items.map((m) => [m.id, isSettlement(to) ? "open" : m.state])), note);
    toast(note, { id: UNDO_TOAST, duration: 8000, action: { label: "Undo", onClick: () => undo() }, description });
  }

  async function change(memory: CoreMemory, values: Partial<Pick<CoreMemory, "text" | "kind" | "scope">>) {
    try {
      await changeCoreMemory(memory.id, values);
      toast(values.text ? "Saved" : values.kind ? "Kind changed" : "Where it applies changed", { id: "core-change" });
      await load();
    } catch (e) {
      toast.error(values.text ? "Could not save the core memory" : values.kind ? "Could not change the kind" : "Could not change where it applies",
                  { description: message(e) });
    }
  }

  function write(covers: CoreMemory[] = []) {
    setDialog((prev) => ({ open: true, covers, key: covers.length || prev.covers.length ? prev.key + 1 : prev.key }));
  }

  async function add(text: string, kind: CoreKind, scope: string, covers: string[]) {
    try {
      await addCoreMemory(text, kind, scope, covers);
      if (covers.length) {
        // Stay with the records: the next one may make another guideline.
        patchTab("recorded", { selected: new Set(), anchor: null });
        toast.success("Guideline added", {
          description: `It generalises ${covers.length} recorded core ${covers.length === 1 ? "memory" : "memories"}. Sessions receive it from their next start.`,
          action: { label: "Show", onClick: () => openTab("guidelines") },
        });
      } else {
        toast.success("Guideline added", { description: "Sessions receive it from their next start." });
        openTab("guidelines");
      }
      await load();
      return true;
    } catch (e) {
      toast.error("Could not add the guideline", { description: message(e) });
      return false;
    }
  }

  function read(memory: CoreMemory) {
    const evidence = sourceOf(memory, byId);
    if (evidence?.source) setReading({ source: evidence.source, project: evidence.project, title: evidence.source });
    else toast("Nothing to open", { description: "This core memory names no source." });
  }

  useReviewShortcuts(root, {
    ...Object.fromEntries(TABS.map((t, i) => [String(i + 1), () => openTab(t)])),
    n: () => write(), "?": () => setHelp(true), z: undo, "mod+z": undo,
  });

  return (
    <Announcer>
      <div ref={root} className="@container/page flex min-h-full flex-col lg:h-full">
        <ReviewHeader title={simple ? "About you" : "Core memories"} info={simple ? SIMPLE_INFO : INFO} infoLabel={simple ? "About this page" : "About core memories"}
          actions={<Button data-tour="core-write" onClick={() => write()}><Plus /> {simple ? "Tell Imprint something" : "Write a guideline"}{" "}
            {!simple && <Kbd aria-hidden className="bg-primary-foreground/15 text-primary-foreground @max-[620px]/page:hidden">N</Kbd>}</Button>} />
        {error && (
          <div className="px-4 pb-3 sm:px-8">
            <Alert variant="destructive">
              <AlertTitle>Could not load core memories: {error}</AlertTitle>
              <AlertDescription>
                <Button variant="outline" size="sm" className="mt-1" onClick={() => void load()}><RefreshCw /> Try again</Button>
              </AlertDescription>
            </Alert>
          </div>
        )}
        {!data ? (!error && (
          <div className="space-y-3 px-4 sm:px-8" aria-busy="true">
            <Skeleton className="h-10 w-full rounded-xl" />
            <p className="flex gap-6 border-b border-border pb-2 text-sm text-muted-foreground">
              <span>Conflicts</span><span>Guidelines</span><span>Proposed</span><span>Recorded</span><span>Retired</span>
            </p>
            <ListSkeleton count={6} />
          </div>
        )) : (
          <>
            {/* The simple dashboard (ADR 0182) leaves out the session budget, the records and what was retired. */}
            {!simple && <div className="mx-4 mb-3 space-y-1.5 sm:mx-8">
              <div className="flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span>Each session receives up to {data.budget.toLocaleString()} tokens of guidelines</span>
                <span className={left ? "font-medium text-warning tabular-nums" : "tabular-nums"}>
                  {shownUsed.toLocaleString()} used by the guidelines for every project
                  {left > 0 && ` · ${left} every-project ${left === 1 ? "guideline doesn't" : "guidelines don't"} fit in a session`}
                </span>
              </div>
              <Progress value={Math.min(100, (100 * shownUsed) / data.budget)} aria-label="Session budget used" />
            </div>}
            <Tabs value={tab} onValueChange={(value) => openTab(value as Tab)} className="flex min-h-0 flex-1 flex-col gap-0">
              <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-border bg-background px-4 sm:px-8 lg:static">
                <TabsList variant="line" data-tour="core-tabs" className="h-10 min-w-0 justify-start overflow-x-auto">
                  <CountTab value="conflicts" name={simple ? "To settle" : "Conflicts"} count={count("conflicts")} pill />
                  <CountTab value="guidelines" name={simple ? "Known about you" : "Guidelines"} count={count("guidelines")} />
                  <CountTab value="proposed" name={simple ? "Suggested" : "Proposed"} count={count("proposed")} pill />
                  {(!simple || tab === "recorded") && <CountTab value="recorded" name="Recorded" count={count("recorded")} />}
                  {(!simple || tab === "retired") && <CountTab value="retired" name="Retired" count={count("retired")} />}
                </TabsList>
                <span className="flex-1" />
                {!simple && <ShortcutsButton onClick={() => setHelp(true)} />}
              </div>
              {TABS.map((which) => (
                <TabsContent key={which} value={which} className="flex min-h-0 flex-1 flex-col px-4 pt-3 pb-4 sm:px-8">
                  <CoreTab tab={which} memories={memories} data={data} projects={projects} byId={byId} covered={covered}
                    state={tabs[which]} onState={(patch) => patchTab(which, patch)} size={size}
                    onSize={(next) => {
                      setSize(next);
                      setTabs((prev) => Object.fromEntries(TABS.map((t) => [t, { ...prev[t], page: 0, reveal: false }])) as Record<Tab, ReviewTabState>);
                    }}
                    root={root} saving={engine.pending} errors={engine.errors} editing={editing} onEdit={setEditing}
                    decide={decide}
                    retry={(id) => {
                      const m = byId.get(id);
                      const kind = engine.errors.get(id)?.kind;
                      if (m) engine.retry(id, kind && isSettlement(kind) ? "open" : m.state);
                    }}
                    change={(m, values) => void change(m, values)} read={read} onWrite={write} />
                </TabsContent>
              ))}
            </Tabs>
          </>
        )}
      </div>
      {data && (
        <AddCoreDialog key={dialog.key} open={dialog.open} onOpenChange={(open) => setDialog((prev) => ({ ...prev, open }))}
          covers={dialog.covers} projects={projects} maxText={data.max_text} onAdd={add} />
      )}
      <SourceReader target={reading} onClose={() => setReading(null)} />
      <ShortcutsDialog open={help} onOpenChange={setHelp} shortcuts={SHORTCUTS} />
    </Announcer>
  );
}
