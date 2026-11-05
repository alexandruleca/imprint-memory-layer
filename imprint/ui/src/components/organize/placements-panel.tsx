"use client";

import { useMemo, useState, type RefObject } from "react";
import Link from "next/link";
import { ArrowRight, ArrowUpDown, Check, RotateCcw, Undo2, WandSparkles, X } from "lucide-react";
import { Hint } from "@/components/hint";
import { Facets, FromFilter, type Facet } from "@/components/review/filters";
import { GroupRail, GroupSelect, type RailGroup } from "@/components/review/group-rail";
import {
  RESET, ReviewEmpty, ReviewList, useAnnounce, useReviewNav, type ReviewTabState,
} from "@/components/review/review-list";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { originLabel, proposalKey, proposalRow, type OrganizeKind } from "@/components/organize/proposal-row";
import type { OrganizeProposal } from "@/lib/api";
import { ago } from "@/lib/queue";
import { useReviewShortcuts } from "@/lib/use-review-shortcuts";
import { cn } from "@/lib/utils";

export type PlacementTab = "review" | "moved" | "decided";

export const inTab = (tab: PlacementTab, status: OrganizeProposal["status"]) =>
  tab === "review" ? status === "pending" : tab === "moved" ? status === "auto" : status === "accepted" || status === "rejected";

/** A profile without its "Project profile: name" heading. */
export const profileText = (text: string) => text.replace(/^Project profile: [^\n]*\n\n/, "");
const firstSentence = (text: string) => profileText(text).split(/(?<=[.!?])\s/)[0];

/** What every placement tab needs from the page. */
export interface PlacementsContext {
  /** Every proposal, with the page's own decisions applied. */
  proposals: OrganizeProposal[];
  /** The order rows were first shown in; new arrivals go last. */
  order: ReadonlyMap<string, number>;
  profiles: ReadonlyMap<string, { text: string; updated: number }>;
  autoMove: boolean;
  lock: string | null;
  saving: number;
  errors: ReadonlyMap<string, { error: string }>;
  /** Automatic moves after this time are new to this browser. */
  lastSeen: number;
  neverRan: boolean;
  size: number;
  onSize: (size: number) => void;
  root: RefObject<HTMLElement | null>;
  act: (kind: OrganizeKind, keys: string[]) => void;
  retry: (key: string) => void;
  read: (proposal: OrganizeProposal) => void;
  openTab: (tab: PlacementTab, patch?: Partial<ReviewTabState>) => void;
  markSeen: () => void;
  run: () => void;
}

const EVIDENCE: Facet[] = [
  { value: "all", label: "All" }, { value: "path", label: "Folder" }, { value: "name", label: "Name" }, { value: "local-model", label: "Model" },
];
const DECISIONS: Facet[] = [{ value: "all", label: "All" }, { value: "accepted", label: "Moved by you" }, { value: "rejected", label: "Kept here" }];
const SORTS = [{ value: "newest", label: "Newest" }, { value: "chunks", label: "Most chunks" }, { value: "source", label: "Source A–Z" }];

function matches(p: OrganizeProposal, tab: PlacementTab, filter: string) {
  if (filter === "all") return true;
  if (tab === "decided") return p.status === filter;
  return filter === "strong" ? p.strength === "strong" : p.method === filter;
}

/** To review, Moved automatically and Decided: one list each, with a rail of
 * target projects on the first two. */
export function PlacementsPanel({ tab, state, onState, ctx }: {
  tab: PlacementTab; state: ReviewTabState; onState: (patch: Partial<ReviewTabState>) => void; ctx: PlacementsContext;
}) {
  const announce = useAnnounce();
  const [confirm, setConfirm] = useState<string[] | null>(null);
  const rail = tab !== "decided";
  const fresh = (p: OrganizeProposal) => (p.decided_at ?? 0) > ctx.lastSeen;

  const base = useMemo(() => ctx.proposals.filter((p) => inTab(tab, p.status)), [ctx.proposals, tab]);
  const freshCount = tab === "moved" ? base.filter(fresh).length : 0;
  const view = tab === "moved" ? state.view ?? (freshCount ? "new" : "all") : "all";
  const viewBase = view === "new" ? base.filter(fresh) : base;
  const group = rail ? state.group ?? "" : "";

  const groups: RailGroup[] = (() => {
    if (!rail) return [];
    const by = new Map<string, OrganizeProposal[]>();
    for (const p of viewBase) by.set(p.target, [...(by.get(p.target) ?? []), p]);
    const listed = [...by.entries()].map(([name, items]): RailGroup => {
      const count = (method: string) => items.filter((p) => p.method === method).length;
      return {
        name, count: items.length,
        hint: tab === "review" ? `Folder ${count("path")} · Name ${count("name")} · Model ${count("local-model")}` : undefined,
        fresh: tab === "moved" && view === "all" && items.some(fresh),
      };
    }).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
    return group && !by.has(group) ? [{ name: group, count: 0 }, ...listed] : listed;
  })();

  const listFor = (target: string) => {
    let out = target ? viewBase.filter((p) => p.target === target) : viewBase;
    out = out.filter((p) => matches(p, tab, state.filter));
    if (state.from) out = out.filter((p) => `o:${p.origin}` === state.from);
    const query = state.query.trim().toLowerCase();
    if (query) {
      out = out.filter((p) => [p.source, p.title ?? "", p.evidence[0]?.quote ?? "", p.preview, p.target, originLabel(p.origin)]
        .join("\n").toLowerCase().includes(query));
    }
    const sorted = [...out];
    if (tab !== "review") sorted.sort((a, b) => (b.decided_at ?? 0) - (a.decided_at ?? 0));
    else if (state.sort === "chunks") sorted.sort((a, b) => b.chunks - a.chunks);
    else if (state.sort === "source") sorted.sort((a, b) => a.source.localeCompare(b.source));
    else sorted.sort((a, b) => (ctx.order.get(proposalKey(a)) ?? 0) - (ctx.order.get(proposalKey(b)) ?? 0));
    return sorted;
  };
  const list = listFor(group);
  const inGroup = group ? viewBase.filter((p) => p.target === group) : viewBase;
  const nav = useReviewNav({ items: list, getKey: proposalKey, state, onState, size: ctx.size });
  const byKey = new Map(list.map((p) => [proposalKey(p), p]));
  const filtered = !!state.query.trim() || state.filter !== "all" || !!state.from;

  const switchGroup = (next: string, moveFocus: boolean) => {
    const first = listFor(next)[0];
    onState({ ...RESET, group: next, ...(moveFocus && first
      ? { focus: proposalKey(first), reveal: true, focusToken: state.focusToken + 1 } : {}) });
    announce(`${next || "All projects"}: ${listFor(next).length} rows`);
  };
  const stepGroup = (delta: number) => {
    const names = ["", ...groups.map((g) => g.name)];
    switchGroup(names[(names.indexOf(group) + delta + names.length) % names.length], true);
  };

  const act = (kind: OrganizeKind, keys: string[]) => {
    if (!keys.length) { announce("Choose a row first: j and k move between rows"); return; }
    if ((kind === "move" || kind === "back") && ctx.lock) { announce(`Moves wait while ${ctx.lock} runs. Keep here still works.`); return; }
    onState(nav.after(keys));
    ctx.act(kind, keys);
  };
  const nothing = () => announce("Nothing to do with that key here");
  const onKey = (kind: OrganizeKind) => () => act(kind, nav.targets());
  useReviewShortcuts(ctx.root, {
    j: () => nav.move(1), ArrowDown: () => nav.move(1), k: () => nav.move(-1), ArrowUp: () => nav.move(-1),
    J: rail ? () => stepGroup(1) : nothing, K: rail ? () => stepGroup(-1) : nothing,
    x: () => { if (nav.current) nav.toggle(nav.current, false); },
    a: tab === "review" ? onKey("move") : nothing,
    r: tab === "review" ? onKey("keep") : nothing,
    // Shift+A and Shift+R: the same, for the selection.
    A: tab === "review" ? onKey("move") : nothing,
    R: tab === "review" ? onKey("keep") : nothing,
    u: tab === "moved" ? onKey("back") : tab === "decided" ? onKey("reopen") : nothing,
    e: nothing,
    Enter: () => { if (nav.current) nav.expand(nav.current); },
    o: () => { const p = nav.current ? byKey.get(nav.current) : undefined; if (p) ctx.read(p); },
    "[": () => nav.goTo(nav.page - 1, true),
    "]": () => nav.goTo(nav.page + 1, true),
    "/": () => document.getElementById("review-search")?.focus(),
    Escape: () => {
      if (state.selected.size) nav.clear();
      else if (state.expanded) onState({ expanded: null });
    },
  });

  const locked = (label: string) => (ctx.lock ? `Moves wait while ${ctx.lock} runs` : label);
  const bulk = (keys: string[]) => {
    const n = keys.length;
    if (tab === "review") return (
      <>
        <Hint label={locked(`Move ${n} (A)`)}>
          <Button size="sm" disabled={!!ctx.lock} focusableWhenDisabled onClick={() => act("move", keys)}><Check /> Move {n}</Button>
        </Hint>
        <Button size="sm" variant="outline" onClick={() => act("keep", keys)}><X /> Keep {n} here</Button>
      </>
    );
    if (tab === "moved") return (
      <Hint label={locked(`Move back ${n} (U)`)}>
        <Button size="sm" variant="outline" disabled={!!ctx.lock} focusableWhenDisabled onClick={() => act("back", keys)}><Undo2 /> Move back {n}</Button>
      </Hint>
    );
    return <Button size="sm" variant="outline" onClick={() => act("reopen", keys)}><RotateCcw /> Reopen {n}</Button>;
  };

  // ── Toolbar filters ─────────────────────────────────────────
  const facets: Facet[] | null = tab === "review"
    ? [...EVIDENCE, ...(ctx.autoMove ? [] : [{ value: "strong", label: "Strong" }])]
    : tab === "decided" ? DECISIONS : null;
  const origins = [...new Set(viewBase.map((p) => p.origin))].sort((a, b) => originLabel(a).localeCompare(originLabel(b)));
  const filters = (
    <>
      {facets && (
        <Facets label={tab === "decided" ? "Decision" : "Evidence"} value={state.filter}
          facets={facets.map((facet) => ({ ...facet, count: inGroup.filter((p) => matches(p, tab, facet.value)).length }))}
          onChange={(filter) => onState({ ...RESET, filter })} />
      )}
      {tab === "review" && origins.length > 1 && (
        <FromFilter label="From" anyLabel="From anywhere" value={state.from}
          options={origins.map((origin) => ({ value: `o:${origin}`, label: originLabel(origin) }))}
          onChange={(from) => onState({ ...RESET, from })} />
      )}
      {tab === "review" && (
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="Sort" className="text-muted-foreground" />}>
            <ArrowUpDown />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-44">
            <DropdownMenuGroup>
              <DropdownMenuLabel>Sort</DropdownMenuLabel>
              <DropdownMenuRadioGroup value={state.sort} onValueChange={(sort) => onState({ ...RESET, sort })}>
                {SORTS.map((sort) => <DropdownMenuRadioItem key={sort.value} value={sort.value}>{sort.label}</DropdownMenuRadioItem>)}
              </DropdownMenuRadioGroup>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </>
  );

  // ── Empty states ────────────────────────────────────────────
  const settings = <Link href="/settings/#settings-organize" className="text-sm underline underline-offset-2">Settings ↗</Link>;
  const autoTotal = ctx.proposals.filter((p) => p.status === "auto").length;
  const autoFresh = ctx.proposals.filter((p) => p.status === "auto" && fresh(p)).length;
  const empty = (() => {
    if (tab === "review") {
      if (ctx.neverRan) return (
        <ReviewEmpty title="Organize hasn't run in this workspace yet"
          description="It finds the project each catch-all memory belongs to, with the evidence.">
          <Button data-autofocus="" onClick={ctx.run}><WandSparkles /> Run Organize</Button>
          <p className="text-xs text-muted-foreground">or let it run by itself ({settings})</p>
        </ReviewEmpty>
      );
      if (group) {
        const next = groups.find((g) => g.name !== group && g.count > 0);
        return (
          <ReviewEmpty title={`${group} is done.`}
            description={next ? `${base.length.toLocaleString()} more wait in other projects.` : "Nothing else waits."}>
            {next
              ? <Button data-autofocus="" onClick={() => switchGroup(next.name, true)}>Next: {next.name} · {next.count} <ArrowRight /></Button>
              : <Button variant="outline" data-autofocus="" onClick={() => switchGroup("", false)}>Show all projects</Button>}
          </ReviewEmpty>
        );
      }
      return (
        <ReviewEmpty title="You're caught up."
          description={`Nothing waits for review.${autoTotal ? ` ${autoTotal.toLocaleString()} sources moved by themselves${autoFresh ? `, ${autoFresh} of them since you last looked` : ""}.` : ""}`}>
          {autoTotal > 0 && <Button variant="outline" data-autofocus="" onClick={() => ctx.openTab("moved")}>Check them <ArrowRight /></Button>}
        </ReviewEmpty>
      );
    }
    if (tab === "moved") {
      if (!base.length && !ctx.autoMove) return (
        <ReviewEmpty title="Automatic moves are off." description="Strong matches wait in To review, marked Strong evidence.">{settings}</ReviewEmpty>
      );
      if (!base.length) return (
        <ReviewEmpty title="Nothing moved by itself yet."
          description="A source moves on its own when it names only one project's folder, or only that project's name at least twice." />
      );
      if (view === "new" && !viewBase.length) return (
        <ReviewEmpty title="Nothing new since you last looked.">
          <Button variant="outline" data-autofocus="" onClick={() => onState({ ...RESET, view: "all" })}>Show all {base.length.toLocaleString()}</Button>
        </ReviewEmpty>
      );
      return (
        <ReviewEmpty title={`Nothing left that moved to ${group} by itself.`}>
          <Button variant="outline" data-autofocus="" onClick={() => switchGroup("", false)}>Show all projects</Button>
        </ReviewEmpty>
      );
    }
    return <ReviewEmpty title="Nothing decided yet." description="Your moves and keeps are listed here and can be reopened." />;
  })();

  const profile = group ? ctx.profiles.get(group) : undefined;
  const header = rail && (
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
      <GroupSelect groups={groups} total={viewBase.length} value={group} onChange={(next) => switchGroup(next, false)} />
      <h2 className="hidden min-w-0 truncate text-base font-semibold @min-[760px]/page:block">{group || "All projects"}</h2>
      <span className="text-sm text-muted-foreground tabular-nums @max-[620px]/page:hidden">
        {inGroup.length.toLocaleString()} {tab === "review" ? "waiting" : "moved"}
      </span>
      {profile && (
        <Popover>
          <PopoverTrigger render={<Button variant="link" size="xs" className="h-auto px-0 font-normal text-muted-foreground" />}>
            <span>About<span className="@max-[620px]/page:hidden"> this project</span></span>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-96 max-w-[calc(100vw-2rem)]">
            <p className="text-sm leading-relaxed whitespace-pre-line">{profileText(profile.text)}</p>
            <p className="text-xs text-muted-foreground">Profile written {ago(profile.updated)} by the local helper.</p>
          </PopoverContent>
        </Popover>
      )}
      <span className="flex-1" />
      {group && inGroup.length > 0 && !state.selected.size && tab === "review" && (
        <>
          <Hint label={locked(`Move all ${inGroup.length} to ${group}`)}>
            <Button size="sm" disabled={!!ctx.lock} focusableWhenDisabled onClick={() => act("move", listFor(group).map(proposalKey))}>
              Move all {listFor(group).length}
            </Button>
          </Hint>
          <Button size="sm" variant="outline" onClick={() => act("keep", listFor(group).map(proposalKey))}>Keep all</Button>
        </>
      )}
      {group && inGroup.length > 0 && !state.selected.size && tab === "moved" && (
        <Hint label={locked(`Move every source here back where it came from`)}>
          <Button size="sm" variant="outline" disabled={!!ctx.lock} focusableWhenDisabled onClick={() => {
            const keys = listFor(group).map(proposalKey);
            if (keys.length > 1) setConfirm(keys); else act("back", keys);
          }}>Move back all {listFor(group).length}</Button>
        </Hint>
      )}
    </div>
  );

  const intro = tab === "moved" && base.length > 0 && (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
      <p className="min-w-0 flex-[1_1_18rem] text-muted-foreground">
        {base.length.toLocaleString()} sources moved by themselves.
        <span className="@max-[620px]/page:hidden"> Each named only this project&apos;s folder, or only this project&apos;s name at least twice, in its opening 50,000 characters.</span>
      </p>
      <ToggleGroup aria-label="Show" size="sm" spacing={0} value={[view]} onValueChange={(next) => next[0] && onState({ ...RESET, view: next[0] })}
        className="rounded-lg border border-border p-0.5">
        <ToggleGroupItem value="new" className="h-6 rounded-md! px-2 text-xs font-normal aria-pressed:font-medium">
          <span>New<span className="@max-[620px]/page:hidden"> since you last looked</span></span>
          <span className="text-muted-foreground tabular-nums">{freshCount}</span>
        </ToggleGroupItem>
        <ToggleGroupItem value="all" className="h-6 rounded-md! px-2 text-xs font-normal aria-pressed:font-medium">
          All <span className="text-muted-foreground tabular-nums">{base.length}</span>
        </ToggleGroupItem>
      </ToggleGroup>
      {freshCount > 0 && <Button variant="ghost" size="sm" onClick={ctx.markSeen}>Mark all as seen</Button>}
    </div>
  );

  const label = tab === "review" ? (group ? `Moves to ${group}` : "All projects")
    : tab === "moved" ? (group ? `Moved to ${group}` : "All projects") : "Decided";
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      {intro}
      <div className={cn("grid min-h-0 flex-1 gap-4 lg:grid-rows-[minmax(0,1fr)]", rail && "@min-[760px]/page:grid-cols-[15rem_minmax(0,1fr)]")}>
        {rail && (
          <GroupRail label={tab === "review" ? "Projects with moves to review" : "Projects moved into"} groups={groups}
            total={viewBase.length} value={group} onChange={(next) => switchGroup(next, false)} />
        )}
        <section aria-label={label} className="flex min-h-0 min-w-0 flex-col gap-2">
          {header}
          <ReviewList nav={nav} state={state} onState={onState} size={ctx.size} onSize={ctx.onSize}
            label={tab === "review" ? "Moves to review" : tab === "moved" ? "Moved automatically" : "Decided"}
            rowLabel={(p) => p.source} searchLabel="Search sources and quotes" filters={filters} filtered={filtered}
            onClearFilters={() => onState({ ...RESET, query: "", filter: "all", from: "" })}
            noMatch={tab === "review" ? "No moves match these filters." : "Nothing matches these filters."}
            bulk={bulk} saving={ctx.saving} empty={empty}
            row={(p, context) => proposalRow(p, context, {
              tab, showTarget: tab !== "review" || !group, autoMove: ctx.autoMove, lock: ctx.lock,
              error: ctx.errors.get(proposalKey(p))?.error,
              about: ctx.profiles.get(p.target) ? firstSentence(ctx.profiles.get(p.target)!.text) : undefined,
              onAct: (kind, key) => act(kind, [key]), onRetry: ctx.retry, onRead: ctx.read,
            })} />
        </section>
      </div>
      <AlertDialog open={confirm !== null} onOpenChange={(open) => { if (!open) setConfirm(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Move back all {confirm?.length} sources?</AlertDialogTitle>
            <AlertDialogDescription>
              They return to where they came from and stay there. Their memories take new IDs, as with any move.
              Reversing this takes two steps per source: Reopen, then Move.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => { if (confirm) act("back", confirm); setConfirm(null); }}>Move back {confirm?.length}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
