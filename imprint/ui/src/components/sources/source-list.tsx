"use client";

import { useMemo, type ReactNode, type RefObject } from "react";
import { ArrowRightLeft, ArrowUpDown } from "lucide-react";
import { Hint } from "@/components/hint";
import { Facets, FromFilter } from "@/components/review/filters";
import { RESET, ReviewList, useAnnounce, useReviewNav, type ReviewTabState } from "@/components/review/review-list";
import { cannotMove, KINDS, kindOf, rowName, sourceRow, splitKey, type SourceRowOptions } from "@/components/sources/source-row";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { SourceRow } from "@/lib/api";
import { useReviewShortcuts } from "@/lib/use-review-shortcuts";

export const SOURCE_SORTS = [
  { value: "chunks", label: "Most chunks" },
  { value: "recent", label: "Recently updated" },
  { value: "name", label: "Name A–Z" },
] as const;

/** The state a source list starts with: largest first. */
export const SOURCE_TAB: Partial<ReviewTabState> = { sort: "chunks" };

const key = (row: SourceRow) => row.source;
/** The Kind filter's value for a row: "" (no snapshot) cannot be a chip's value. */
const kindValue = (row: SourceRow) => kindOf(row) || "none";

/** Rows with what search and sort need computed once per load. */
type Prepared = { row: SourceRow; hay: string; name: string };

function sorted(list: Prepared[], sort: string) {
  const out = [...list];
  if (sort === "recent") out.sort((a, b) => (b.row.updated ?? 0) - (a.row.updated ?? 0) || b.row.chunks - a.row.chunks);
  else if (sort === "name") out.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : a.row.source < b.row.source ? -1 : 1));
  else out.sort((a, b) => b.row.chunks - a.row.chunks || (a.row.source < b.row.source ? -1 : 1));
  return out.map((p) => p.row);
}

/** Every source, or one project's, as a paged list: search, a Kind filter,
 * a Project select when not scoped, sorting, and Move for the selection.
 * Sources and a project's Sources tab share it. */
export function SourceList({ sources, scope, state, onState, size, onSize, root, label, empty, actions, onMove }: {
  sources: SourceRow[];
  /** The project the list belongs to (the Projects page); undefined lists every project. */
  scope?: string;
  state: ReviewTabState;
  onState: (patch: Partial<ReviewTabState>) => void;
  size: number;
  onSize: (size: number) => void;
  root: RefObject<HTMLElement | null>;
  label: string;
  empty: ReactNode;
  actions: Omit<SourceRowOptions, "scope" | "onDetails">;
  /** Move these rows, which share one project, to another project. */
  onMove: (rows: SourceRow[]) => void;
}) {
  const announce = useAnnounce();
  const prepared = useMemo<Prepared[]>(() => sources.map((row) => ({
    row,
    hay: `${row.title ?? ""}\n${row.source}\n${row.project ?? ""}`.toLowerCase(),
    // What the row shows first: the question, else the file's name.
    name: (row.title || splitKey(row.source).base).toLowerCase(),
  })), [sources]);

  const project = scope ? "" : state.from;
  const inProject = useMemo(() => (project ? prepared.filter((p) => p.row.project === project) : prepared), [prepared, project]);
  const query = state.query.trim().toLowerCase();
  const list = useMemo(() => {
    let out = inProject;
    if (state.filter !== "all") out = out.filter((p) => kindValue(p.row) === state.filter);
    if (query) out = out.filter((p) => p.hay.includes(query));
    return sorted(out, state.sort);
  }, [inProject, state.filter, query, state.sort]);
  const nav = useReviewNav({ items: list, getKey: key, state, onState, size, noun: "sources" });
  const byKey = useMemo(() => new Map(sources.map((row) => [row.source, row])), [sources]);
  const filtered = !!query || state.filter !== "all" || !!project;

  const kindCounts = useMemo(() => {
    const counts = new Map<string, number>();
    inProject.forEach((p) => counts.set(kindValue(p.row), (counts.get(kindValue(p.row)) ?? 0) + 1));
    return counts;
  }, [inProject]);
  const projects = useMemo(() => {
    if (scope) return [];
    const counts = new Map<string, number>();
    sources.forEach((row) => { if (row.project) counts.set(row.project, (counts.get(row.project) ?? 0) + 1); });
    return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [sources, scope]);

  /** What Move acts on: the movable rows, if they share one project. */
  const movable = (keys: string[]) => {
    const rows = keys.map((k) => byKey.get(k)).filter((row): row is SourceRow => !!row);
    const ok = rows.filter((row) => !cannotMove(row, scope));
    const owners = new Set(ok.map((row) => scope ?? row.project));
    const why = !ok.length ? (rows.length === 1 ? cannotMove(rows[0], scope) ?? "" : "None of these can move from here")
      : owners.size > 1 ? "Choose sources of one project to move them together" : "";
    return { rows: ok, skipped: rows.length - ok.length, why };
  };
  const move = (keys: string[]) => {
    if (!keys.length) { announce("Choose a row first: j and k move between rows"); return; }
    const { rows, why } = movable(keys);
    if (why) { announce(why); return; }
    onMove(rows);
  };

  const options: SourceRowOptions = {
    ...actions, scope,
    onDetails: (row) => nav.expand(row.source),
  };

  useReviewShortcuts(root, {
    j: () => nav.move(1), ArrowDown: () => nav.move(1), k: () => nav.move(-1), ArrowUp: () => nav.move(-1),
    x: () => { if (nav.current) nav.toggle(nav.current, false); },
    Enter: () => { if (nav.current) nav.expand(nav.current); },
    o: () => { const row = nav.current ? byKey.get(nav.current) : undefined; if (row) actions.onRead(row); },
    m: () => move(nav.targets()),
    "[": () => nav.goTo(nav.page - 1, true),
    "]": () => nav.goTo(nav.page + 1, true),
    "/": () => document.getElementById("review-search")?.focus(),
    Escape: () => {
      if (state.selected.size) nav.clear();
      else if (state.expanded) onState({ expanded: null });
    },
  });

  const kinds = KINDS.map((kind) => ({ ...kind, value: kind.value || "none" })).filter((kind) => kindCounts.get(kind.value));
  const filters = (
    <>
      {kinds.length > 1 && (
        <Facets label="Kind" value={state.filter} onChange={(filter) => onState({ ...RESET, filter })}
          facets={[{ value: "all", label: "All" }, ...kinds.map((kind) => ({ value: kind.value, label: kind.label, count: kindCounts.get(kind.value) }))]} />
      )}
      {projects.length > 1 && (
        <FromFilter label="Project" anyLabel="Any project" value={state.from} onChange={(from) => onState({ ...RESET, from })}
          options={projects.map(([name, count]) => ({ value: name, label: `${name} (${count.toLocaleString()})` }))} />
      )}
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="Sort" className="text-muted-foreground" />}>
          <ArrowUpDown />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-48">
          <DropdownMenuGroup>
            <DropdownMenuLabel>Sort</DropdownMenuLabel>
            <DropdownMenuRadioGroup value={state.sort} onValueChange={(sort) => onState({ ...RESET, sort })}>
              {SOURCE_SORTS.map((sort) => <DropdownMenuRadioItem key={sort.value} value={sort.value} closeOnClick>{sort.label}</DropdownMenuRadioItem>)}
            </DropdownMenuRadioGroup>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );

  const bulk = (keys: string[]) => {
    const { rows, skipped, why } = movable(keys);
    const hint = why || (skipped ? `${skipped} linked or unrecorded ${skipped === 1 ? "stays" : "stay"} where ${skipped === 1 ? "it is" : "they are"}` : `Move ${rows.length} to another project (M)`);
    return (
      <Hint label={hint}>
        <Button size="sm" disabled={!!why} focusableWhenDisabled onClick={() => move(keys)}>
          <ArrowRightLeft /> Move {rows.length || keys.length} to…
        </Button>
      </Hint>
    );
  };

  return (
    <ReviewList nav={nav} state={state} onState={onState} size={size} onSize={onSize} label={label}
      rowLabel={rowName} searchLabel={scope ? "Search sources" : "Search titles and paths"}
      filters={filters} filtered={filtered}
      onClearFilters={() => onState({ ...RESET, query: "", filter: "all", from: "" })}
      noMatch="No sources match these filters." bulk={bulk} saving={0} empty={empty}
      row={(row, context) => sourceRow(row, context, options)} />
  );
}
