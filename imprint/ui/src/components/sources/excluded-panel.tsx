"use client";

import { useMemo, type RefObject } from "react";
import { RotateCcw, Trash2 } from "lucide-react";
import { Facets } from "@/components/review/filters";
import {
  ActionButton, RESET, ReviewEmpty, ReviewList, useAnnounce, useReviewNav, type ReviewTabState,
} from "@/components/review/review-list";
import { Button } from "@/components/ui/button";
import type { WorkspaceScope } from "@/lib/api";
import { useReviewShortcuts } from "@/lib/use-review-shortcuts";

export type Exclusion = WorkspaceScope["exclusions"][number];
export const exclusionKey = (item: Exclusion) => `${item.kind}\u0000${item.path}`;

const MATCH = [{ value: "all", label: "All" }, { value: "source", label: "Source keys" }, { value: "path", label: "File paths" }];

/** What purges keep out of ingestion, each with Allow re-ingestion. */
export function ExcludedPanel({ scope, state, onState, size, onSize, root, busy, onAllow, onPurge }: {
  scope: WorkspaceScope;
  state: ReviewTabState;
  onState: (patch: Partial<ReviewTabState>) => void;
  size: number;
  onSize: (size: number) => void;
  root: RefObject<HTMLElement | null>;
  busy: boolean;
  onAllow: (items: Exclusion[]) => void;
  onPurge: () => void;
}) {
  const announce = useAnnounce();
  const query = state.query.trim().toLowerCase();
  const list = useMemo(() => scope.exclusions
    .filter((item) => state.filter === "all" || item.kind === state.filter)
    .filter((item) => !query || item.path.toLowerCase().includes(query))
    .sort((a, b) => a.path.localeCompare(b.path)), [scope.exclusions, state.filter, query]);
  const nav = useReviewNav({ items: list, getKey: exclusionKey, state, onState, size, noun: "paths" });
  const byKey = new Map(list.map((item) => [exclusionKey(item), item]));
  const allow = (keys: string[]) => {
    if (!keys.length) { announce("Choose a row first: j and k move between rows"); return; }
    const items = keys.map((key) => byKey.get(key)).filter((item): item is Exclusion => !!item);
    onState(nav.after(keys));
    onAllow(items);
  };

  useReviewShortcuts(root, {
    j: () => nav.move(1), ArrowDown: () => nav.move(1), k: () => nav.move(-1), ArrowUp: () => nav.move(-1),
    x: () => { if (nav.current) nav.toggle(nav.current, false); },
    a: () => allow(nav.targets()),
    "[": () => nav.goTo(nav.page - 1, true),
    "]": () => nav.goTo(nav.page + 1, true),
    "/": () => document.getElementById("review-search")?.focus(),
    Escape: () => { if (state.selected.size) nav.clear(); },
  });

  const kinds = new Set(scope.exclusions.map((item) => item.kind));
  const filters = kinds.size > 1 ? (
    <Facets label="Match" value={state.filter} onChange={(filter) => onState({ ...RESET, filter })}
      facets={MATCH.map((facet) => ({ ...facet, count: scope.exclusions.filter((item) => facet.value === "all" || item.kind === facet.value).length }))} />
  ) : undefined;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <p className="min-w-0 flex-[1_1_18rem] text-sm text-muted-foreground">
          Purged from {scope.workspace} and kept out of its ingests.<span className="@max-[620px]/page:hidden"> Allowing one again lets the next ingest or refresh index it.</span>
        </p>
        <Button size="sm" variant="outline" onClick={onPurge}><Trash2 /> Purge a path…</Button>
      </div>
      <ReviewList nav={nav} state={state} onState={onState} size={size} onSize={onSize} label="Excluded paths"
        rowLabel={(item) => item.path} searchLabel="Search paths" filters={filters} filtered={!!query || state.filter !== "all"}
        onClearFilters={() => onState({ ...RESET, query: "", filter: "all" })} noMatch="No excluded path matches."
        saving={busy ? 1 : 0} empty={(
          <ReviewEmpty title="Nothing is excluded."
            description="Purging a source, a file or a directory removes its indexed copies and keeps it out of later ingests until you allow it here.">
            <Button variant="outline" data-autofocus="" onClick={onPurge}><Trash2 /> Purge a path…</Button>
          </ReviewEmpty>
        )}
        bulk={(keys) => (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => allow(keys)}><RotateCcw /> Allow {keys.length} again</Button>
        )}
        row={(item, ctx) => ({
          body: (
            <>
              <p className="truncate font-mono text-[12.5px] font-medium" title={item.path}>{item.path}{item.recursive ? "/**" : ""}</p>
              <p className="truncate text-xs text-muted-foreground">
                {item.kind === "source" ? "Source key" : "File path"}{item.recursive && " · with everything under it"}
              </p>
            </>
          ),
          actions: (
            <ActionButton icon={<RotateCcw />} label="Allow re-ingestion" hint="Allow re-ingestion (A)" tabIndex={ctx.tabIndex}
              disabled={busy} onClick={() => allow([exclusionKey(item)])} />
          ),
        })} />
    </div>
  );
}
