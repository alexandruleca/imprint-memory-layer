"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Plus, RefreshCw, Check, RotateCcw, X } from "lucide-react";
import { ListSkeleton } from "@/components/loaders";
import { SourceReader, type ReaderTarget } from "@/components/source-reader";
import { AddCoreDialog } from "@/components/core/add-core-dialog";
import { EVERYWHERE, coreRow, overflow, scopeLabel, type CoreState } from "@/components/core/core-row";
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
import { addCoreMemory, changeCoreMemory, getCoreMemories, getStats, type CoreKind, type CoreMemories, type CoreMemory } from "@/lib/api";
import { useHashTab } from "@/lib/use-hash-tab";
import { useReviewShortcuts } from "@/lib/use-review-shortcuts";
import { useStoredNumber } from "@/lib/use-stored";
import { useDecisions } from "@/lib/write-queue";

const DESCRIPTION = "Short statements about you that every agent session receives at its start: how you like things done, rules to follow, facts about you. Only you make one active. Agents and Organize can only propose them.";
const TABS = ["proposed", "active", "retired"] as const;
type Tab = (typeof TABS)[number];
const UNDO_TOAST = "core-undo";
const KIND_FACETS = [
  { value: "all", label: "All" }, { value: "preference", label: "Preference" },
  { value: "instruction", label: "Instruction" }, { value: "fact", label: "Fact" },
];
const ORIGINS = [{ value: "organize", label: "Organize" }, { value: "agent", label: "Agents" }, { value: "user", label: "You" }];
const SHORTCUTS: Shortcut[] = [
  [["j", "↓"], "Next row"], [["k", "↑"], "Previous row"], [["x"], "Select the row"],
  [["a"], "Approve · Restore"], [["r"], "Reject · Retire"], [["e"], "Edit the text"],
  [["Enter"], "Show the whole text"], [["o"], "Read where it came from"], [["z", "⌘Z"], "Undo the last action"],
  [["[", "]"], "Previous / next page"], [["/"], "Search"], [["1", "3"], "Switch tab"], [["n"], "Add a core memory"],
  [["Esc"], "Clear the selection"], [["?"], "This list"],
];
const VERB: Record<CoreState, string> = { active: "Approved", retired: "Retired", proposed: "Returned to proposals" };
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const short = (memory: CoreMemory) => `“${memory.text.length > 44 ? `${memory.text.slice(0, 42)}…` : memory.text}”`;

function verb(to: CoreState, from: CoreState) {
  if (to === "active") return from === "retired" ? "Restored" : "Approved";
  if (to === "retired") return from === "proposed" ? "Rejected" : "Retired";
  return VERB[to];
}

function CoreTab({ tab, memories, data, projects, state, onState, size, onSize, root, saving, errors, editing, onEdit,
                   decide, retry, change, read, onAdd }: {
  tab: Tab; memories: CoreMemory[]; data: CoreMemories; projects: string[];
  state: ReviewTabState; onState: (patch: Partial<ReviewTabState>) => void; size: number; onSize: (size: number) => void;
  root: React.RefObject<HTMLElement | null>; saving: number; errors: ReadonlyMap<string, { error: string }>;
  editing: string | null; onEdit: (id: string | null) => void;
  decide: (to: CoreState, ids: string[]) => void; retry: (id: string) => void;
  change: (memory: CoreMemory, values: Partial<Pick<CoreMemory, "text" | "kind" | "scope">>) => void;
  read: (memory: CoreMemory) => void; onAdd: () => void;
}) {
  const announce = useAnnounce();
  const base = memories.filter((m) => m.state === tab);
  // `group` holds the Scope filter here: core memories have no rail.
  const scope = state.group ?? "";
  const query = state.query.trim().toLowerCase();
  const list = base
    .filter((m) => state.filter === "all" || m.kind === state.filter)
    .filter((m) => !scope || m.scope === scope)
    .filter((m) => !state.from || m.origin === state.from)
    .filter((m) => !query || [m.text, m.scope, ...m.evidence.map((e) => e.quote ?? "")].join("\n").toLowerCase().includes(query))
    .sort((a, b) => (tab === "proposed" ? b.created_at - a.created_at : b.updated_at - a.updated_at));
  const nav = useReviewNav({ items: list, getKey: (m: CoreMemory) => m.id, state, onState, size });
  const filtered = !!query || state.filter !== "all" || !!scope || !!state.from;
  const byId = new Map(list.map((m) => [m.id, m]));

  const act = (to: CoreState, ids: string[]) => {
    if (!ids.length) { announce("Choose a row first: j and k move between rows"); return; }
    onState(nav.after(ids));
    decide(to, ids);
  };
  const nothing = () => announce("Nothing to do with that key here");
  useReviewShortcuts(root, {
    j: () => nav.move(1), ArrowDown: () => nav.move(1), k: () => nav.move(-1), ArrowUp: () => nav.move(-1),
    x: () => { if (nav.current) nav.toggle(nav.current, false); },
    a: tab === "active" ? nothing : () => act("active", nav.targets()),
    r: tab === "retired" ? nothing : () => act("retired", nav.targets()),
    A: tab === "active" ? nothing : () => act("active", nav.targets()),
    R: tab === "retired" ? nothing : () => act("retired", nav.targets()),
    u: nothing,
    e: tab === "retired" ? nothing : () => { if (nav.current) onEdit(nav.current); },
    Enter: () => { if (nav.current) nav.expand(nav.current); },
    o: () => { const m = nav.current ? byId.get(nav.current) : undefined; if (m) read(m); },
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
  const filters = (
    <>
      <Facets label="Kind" value={state.filter} onChange={(filter) => onState({ ...RESET, filter })}
        facets={KIND_FACETS.map((facet) => ({ ...facet, count: base.filter((m) => facet.value === "all" || m.kind === facet.value).length }))} />
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
    if (tab === "proposed") return (
      <>
        <Button size="sm" onClick={() => act("active", ids)}><Check /> Approve {n}</Button>
        <Button size="sm" variant="outline" onClick={() => act("retired", ids)}><X /> Reject {n}</Button>
      </>
    );
    if (tab === "active") return <Button size="sm" variant="outline" onClick={() => act("retired", ids)}><X /> Retire {n}</Button>;
    return <Button size="sm" variant="outline" onClick={() => act("active", ids)}><RotateCcw /> Restore {n}</Button>;
  };
  const empty = tab === "proposed" ? (
    <ReviewEmpty title="No proposals." description="Agents and Organize suggest them; you can also add one.">
      <Button variant="outline" data-autofocus="" onClick={onAdd}><Plus /> Add core memory</Button>
    </ReviewEmpty>
  ) : tab === "active" ? (
    <ReviewEmpty title="No active core memories" description="Add one, or approve a proposal. Sessions start without any until then." />
  ) : <ReviewEmpty title="Nothing retired." description="Rejected proposals and retired core memories are kept here and can be restored." />;

  return (
    <section aria-label={tab === "proposed" ? "Proposed" : tab === "active" ? "Active" : "Retired and rejected"} className="flex min-h-0 min-w-0 flex-col">
      <ReviewList nav={nav} state={state} onState={onState} size={size} onSize={onSize}
        label={tab === "proposed" ? "Proposed core memories" : tab === "active" ? "Active core memories" : "Retired core memories"}
        rowLabel={(m) => m.text} searchLabel="Search text and quotes" filters={filters} filtered={filtered}
        onClearFilters={() => onState({ ...RESET, query: "", filter: "all", from: "", group: "" })}
        noMatch="No core memories match these filters." bulk={bulk} saving={saving} empty={empty} metaBelow
        row={(m, context) => coreRow(m, context, {
          projects, maxText: data.max_text, budget: data.budget, used: data.used, editing: editing === m.id,
          error: errors.get(m.id)?.error,
          onState: (to) => act(to, [m.id]),
          onEdit: (on) => onEdit(on ? m.id : null),
          onSave: (text) => { change(m, { text }); onEdit(null); },
          onKind: (kind) => change(m, { kind }),
          onScope: (next) => change(m, { scope: next }),
          onRetry: () => retry(m.id),
        })} />
    </section>
  );
}

export default function CoreMemoriesPage() {
  const root = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<CoreMemories | null>(null);
  const [error, setError] = useState("");
  const [projects, setProjects] = useState<string[]>([]);
  const [tabs, setTabs] = useState<Record<Tab, ReviewTabState>>(() => ({ proposed: reviewTab(), active: reviewTab(), retired: reviewTab() }));
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [help, setHelp] = useState(false);
  const [reading, setReading] = useState<ReaderTarget | null>(null);
  const [linked, openTab] = useHashTab(TABS);
  const [size, setSize] = useStoredNumber("imprint.review.page-size", 20, PAGE_SIZES);
  const reload = useRef<() => void>(() => {});
  // The tab a link without a hash opens, chosen once from the first load so
  // approving the last proposal does not switch tabs under the cursor.
  const [fallback, setFallback] = useState<Tab | null>(null);

  const engine = useDecisions<CoreState, CoreState>({
    send: async (to, ids, keepalive) => {
      const failed: { key: string; error: string }[] = [];
      for (const id of ids) {
        try { await changeCoreMemory(id, { state: to }, keepalive ? { keepalive } : undefined); }
        catch (e) { failed.push({ key: id, error: message(e) }); }
      }
      return failed;
    },
    result: (to) => to,
    inverse: (_to, from) => [from],
    onDrain: () => reload.current(),
    onFailed: (to, failed) => toast.error(`${failed.length} could not be ${to === "active" ? "made active" : to === "retired" ? "retired" : "returned to proposals"}`,
                                          { description: failed[0].error }),
  });
  const prune = engine.prune;

  const absorb = useCallback((next: CoreMemories) => {
    const memories = Array.isArray(next?.memories) ? next.memories : [];
    setData({ ...next, memories });
    setFallback((prev) => prev ?? (memories.some((m) => m.state === "proposed") ? "proposed" : "active"));
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
    getStats().then((s) => { if (live) setProjects(s.projects.map(([name]) => name).filter(Boolean).sort()); }).catch(() => {});
    return () => { live = false; };
  }, [absorb]);

  const memories = useMemo(() => (data?.memories ?? []).map((m) => {
    const state = engine.overrides.get(m.id);
    return state && state !== m.state ? { ...m, state } : m;
  }), [data, engine.overrides]);
  const byId = useMemo(() => new Map(memories.map((m) => [m.id, m])), [memories]);
  const count = (state: CoreState) => memories.filter((m) => m.state === state).length;
  const tab: Tab = linked ?? fallback ?? "active";
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

  function decide(to: CoreState, ids: string[]) {
    const items = ids.map((id) => byId.get(id)).filter((m): m is CoreMemory => !!m);
    if (!items.length) return;
    const from = items[0].state;
    const note = `${verb(to, from)} ${items.length === 1 ? short(items[0]) : `${items.length} core memories`}`;
    engine.decide(to, items.map((m) => m.id), new Map(items.map((m) => [m.id, m.state])), note);
    toast(note, { id: UNDO_TOAST, duration: 8000, action: { label: "Undo", onClick: () => undo() },
                  description: to === "active" ? "Sessions receive it from their next start." : undefined });
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

  async function add(text: string, kind: CoreKind, scope: string) {
    try {
      await addCoreMemory(text, kind, scope);
      toast.success("Added", { description: "Sessions receive it from their next start." });
      openTab("active");
      await load();
      return true;
    } catch (e) {
      toast.error("Could not add the core memory", { description: message(e) });
      return false;
    }
  }

  function read(memory: CoreMemory) {
    const evidence = memory.evidence.find((e) => e.source);
    if (evidence?.source) setReading({ source: evidence.source, project: evidence.project, title: evidence.source });
    else toast("Nothing to open", { description: "This core memory names no source." });
  }

  useReviewShortcuts(root, {
    "1": () => openTab("proposed"), "2": () => openTab("active"), "3": () => openTab("retired"),
    n: () => setAdding(true), "?": () => setHelp(true), z: undo, "mod+z": undo,
  });

  return (
    <Announcer>
      <div ref={root} className="@container/page flex min-h-full flex-col lg:h-full">
        <ReviewHeader title="Core memories" count={data ? count("active") : undefined} info={<p>{DESCRIPTION}</p>} infoLabel="About core memories"
          actions={<Button onClick={() => setAdding(true)}><Plus /> Add core memory <Kbd aria-hidden className="bg-primary-foreground/15 text-primary-foreground @max-[620px]/page:hidden">N</Kbd></Button>} />
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
            <p className="flex gap-6 border-b border-border pb-2 text-sm text-muted-foreground"><span>Proposed</span><span>Active</span><span>Retired</span></p>
            <ListSkeleton count={6} />
          </div>
        )) : (
          <>
            <div className="mx-4 mb-3 space-y-1.5 sm:mx-8">
              <div className="flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span>Each session receives up to {data.budget.toLocaleString()} characters of them</span>
                <span className={left ? "font-medium text-amber-700 tabular-nums" : "tabular-nums"}>
                  {shownUsed.toLocaleString()} used by the ones for every project
                  {left > 0 && ` · ${left} every-project ${left === 1 ? "memory doesn't" : "memories don't"} fit in a session`}
                </span>
              </div>
              <Progress value={Math.min(100, (100 * shownUsed) / data.budget)} aria-label="Session budget used" />
            </div>
            <Tabs value={tab} onValueChange={(value) => openTab(value as Tab)} className="flex min-h-0 flex-1 flex-col gap-0">
              <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-border bg-background px-4 sm:px-8 lg:static">
                <TabsList variant="line" className="h-10 min-w-0 justify-start overflow-x-auto">
                  <CountTab value="proposed" name="Proposed" count={count("proposed")} pill />
                  <CountTab value="active" name="Active" count={count("active")} />
                  <CountTab value="retired" name="Retired" count={count("retired")} />
                </TabsList>
                <span className="flex-1" />
                <ShortcutsButton onClick={() => setHelp(true)} />
              </div>
              {TABS.map((which) => (
                <TabsContent key={which} value={which} className="flex min-h-0 flex-1 flex-col px-4 pt-3 pb-4 sm:px-8">
                  <CoreTab tab={which} memories={memories} data={data} projects={projects} state={tabs[which]}
                    onState={(patch) => patchTab(which, patch)} size={size}
                    onSize={(next) => { setSize(next); setTabs((prev) => ({ proposed: { ...prev.proposed, page: 0, reveal: false }, active: { ...prev.active, page: 0, reveal: false }, retired: { ...prev.retired, page: 0, reveal: false } })); }}
                    root={root} saving={engine.pending} errors={engine.errors} editing={editing} onEdit={setEditing}
                    decide={decide} retry={(id) => { const m = byId.get(id); if (m) engine.retry(id, m.state); }}
                    change={(m, values) => void change(m, values)} read={read} onAdd={() => setAdding(true)} />
                </TabsContent>
              ))}
            </Tabs>
          </>
        )}
      </div>
      {data && <AddCoreDialog open={adding} onOpenChange={setAdding} projects={projects} maxText={data.max_text} onAdd={add} />}
      <SourceReader target={reading} onClose={() => setReading(null)} />
      <ShortcutsDialog open={help} onOpenChange={setHelp} shortcuts={SHORTCUTS} />
    </Announcer>
  );
}
