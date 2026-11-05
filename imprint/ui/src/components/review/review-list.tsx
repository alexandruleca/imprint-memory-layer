"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Search } from "lucide-react";
import { Hint } from "@/components/hint";
import { Spinner } from "@/components/loaders";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Kbd } from "@/components/ui/kbd";
import { CompactPager, Pager } from "@/components/review/pager";
import { cn } from "@/lib/utils";

// ── State ───────────────────────────────────────────────────────

/** One review tab's place: kept by the page while it is open, so switching
 * tabs and back returns to the same page, selection and filters. */
export interface ReviewTabState {
  page: number;
  /** Show the page holding `focus` instead of `page`: after a decision, an
   * undo or a keyboard move. Paging by hand turns it off. */
  reveal: boolean;
  /** The current row: the one tab stop of the list and what keys act on. */
  focus: string | null;
  /** Bumped to move keyboard focus to `focus`, or to the empty state. */
  focusToken: number;
  selected: ReadonlySet<string>;
  /** Where a shift-click range starts. */
  anchor: string | null;
  expanded: string | null;
  query: string;
  /** The facet chips' choice; "all" for none. */
  filter: string;
  /** The From filter; "" for anywhere. */
  from: string;
  /** The rail's project; "" for all, null before the page picked one. */
  group: string | null;
  sort: string;
  /** A tab's own view, such as Moved automatically's "new" or "all"; null: its default. */
  view: string | null;
}

export function reviewTab(patch: Partial<ReviewTabState> = {}): ReviewTabState {
  return { page: 0, reveal: false, focus: null, focusToken: 0, selected: new Set(), anchor: null, expanded: null,
           query: "", filter: "all", from: "", group: "", sort: "newest", view: null, ...patch };
}

/** What a change of filter, search or group resets: page 1 and the selection. */
export const RESET: Partial<ReviewTabState> = { page: 0, reveal: false, focus: null, selected: new Set(), anchor: null, expanded: null };

// ── Announcements ───────────────────────────────────────────────

const AnnounceContext = createContext<(message: string) => void>(() => {});
export const useAnnounce = () => useContext(AnnounceContext);

/** The page's one polite live region (not role="status"), for page changes,
 * filter results and keyboard decisions; toasts announce themselves. */
export function Announcer({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState({ text: "", serial: 0 });
  const announce = useCallback((text: string) => setMessage((prev) => ({ text, serial: prev.serial + 1 })), []);
  return (
    <AnnounceContext.Provider value={announce}>
      {children}
      <div aria-live="polite" className="sr-only" data-testid="review-live"><span key={message.serial}>{message.text}</span></div>
    </AnnounceContext.Provider>
  );
}

// ── Navigation ──────────────────────────────────────────────────

/** Paging, the current row, selection and the rules for what follows a
 * decision, over the filtered list of one tab. */
export function useReviewNav<T>({ items, getKey, state, onState, size, noun = "rows" }: {
  items: T[];
  getKey: (item: T) => string;
  state: ReviewTabState;
  onState: (patch: Partial<ReviewTabState>) => void;
  size: number;
  noun?: string;
}) {
  const announce = useAnnounce();
  const keys = useMemo(() => items.map(getKey), [items, getKey]);
  const pages = Math.max(1, Math.ceil(keys.length / size));
  let page = Math.min(Math.max(state.page, 0), pages - 1);
  if (state.reveal && state.focus) {
    const at = keys.indexOf(state.focus);
    if (at >= 0) page = Math.floor(at / size);
  }
  const start = page * size;
  const shown = items.slice(start, start + size);
  const shownKeys = keys.slice(start, start + size);
  const current = state.focus && shownKeys.includes(state.focus) ? state.focus : shownKeys[0] ?? null;
  const selected = keys.filter((key) => state.selected.has(key));

  const goTo = (next: number, byKeyboard = false) => {
    const target = Math.min(Math.max(next, 0), pages - 1);
    if (target === page) return;
    const from = target * size;
    onState({ page: target, reveal: false, expanded: null,
              ...(byKeyboard ? { focus: keys[from] ?? null, focusToken: state.focusToken + 1 } : {}) });
    announce(`Page ${target + 1} of ${pages}, ${noun} ${from + 1} to ${Math.min(keys.length, from + size)}`);
  };

  const move = (delta: number) => {
    if (!keys.length) return;
    const at = state.focus && shownKeys.includes(state.focus) ? keys.indexOf(state.focus) : -1;
    const next = at < 0 ? (delta > 0 ? start : Math.min(keys.length, start + size) - 1)
      : Math.min(Math.max(at + delta, 0), keys.length - 1);
    onState({ focus: keys[next], reveal: true, focusToken: state.focusToken + 1, expanded: null });
  };

  /** What `a`, `r` and `u` act on: the selection, else the current row. */
  const targets = () => (selected.length ? selected : current ? [current] : []);

  /** The state after `decided` leave the list: the page refills from the
   * next one, and focus goes to the row now at the first decided row's
   * place, else the one before it, else the empty state. */
  const after = (decided: string[]): Partial<ReviewTabState> => {
    const gone = new Set(decided);
    const first = keys.findIndex((key) => gone.has(key));
    if (first < 0) return {};
    const rest = keys.filter((key) => !gone.has(key));
    const index = Math.min(first, rest.length - 1);
    const next = rest[index] ?? null;
    return { focus: next, reveal: next !== null, page: next === null ? 0 : Math.floor(index / size),
             selected: new Set([...state.selected].filter((key) => !gone.has(key))), anchor: null, expanded: null,
             focusToken: state.focusToken + 1 };
  };

  const toggle = (key: string, range: boolean) => {
    const next = new Set(state.selected);
    const from = state.anchor ? shownKeys.indexOf(state.anchor) : -1;
    const to = shownKeys.indexOf(key);
    if (range && from >= 0 && to >= 0) {
      shownKeys.slice(Math.min(from, to), Math.max(from, to) + 1).forEach((k) => next.add(k));
    } else if (next.has(key)) next.delete(key);
    else next.add(key);
    onState({ selected: next, anchor: key, focus: key, expanded: state.expanded === key ? key : null });
  };

  const pageSelected = shownKeys.length > 0 && shownKeys.every((key) => state.selected.has(key));
  const togglePage = () => {
    const next = new Set(state.selected);
    shownKeys.forEach((key) => (pageSelected ? next.delete(key) : next.add(key)));
    onState({ selected: next });
  };

  return {
    keys, pages, page, start, shown, shownKeys, current, selected, pageSelected, goTo, move, targets, after, toggle, togglePage,
    selectAll: () => onState({ selected: new Set(keys) }),
    clear: () => onState({ selected: new Set(), anchor: null }),
    expand: (key: string) => onState({ expanded: state.expanded === key ? null : key, focus: key }),
    /** The rows' DOM focus moved here (a click or Tab). */
    focusRow: (key: string) => {
      // One row is open at a time, and it closes when focus leaves it.
      if (state.focus !== key) onState({ focus: key, expanded: state.expanded === key ? key : null });
    },
  };
}

export type ReviewNav<T> = ReturnType<typeof useReviewNav<T>>;

// ── Pieces ──────────────────────────────────────────────────────

/** A row action: labelled from 620 px of content up, an icon with a hint
 * that names it and its key below that. A button disabled by the ingestion
 * lock stays focusable, so its hint can say why. */
export function ActionButton({ icon, label, shortcut, hint, variant = "outline", tabIndex, disabled, onClick, className }: {
  icon: ReactNode; label: string; shortcut?: string; hint?: string;
  variant?: "default" | "outline" | "ghost" | "secondary";
  tabIndex?: number; disabled?: boolean; onClick: () => void; className?: string;
}) {
  return (
    <Hint label={<>{hint ?? label}{shortcut && <Kbd>{shortcut}</Kbd>}</>}>
      <Button size="sm" variant={variant} tabIndex={tabIndex} disabled={disabled} focusableWhenDisabled
        onClick={onClick} className={cn("@max-[620px]/page:px-1.5", className)}>
        {icon}<span className="@max-[620px]/page:sr-only">{label}</span>
      </Button>
    </Hint>
  );
}

/** An empty list: a heading that takes focus when the list empties, and
 * what to do next. */
export function ReviewEmpty({ title, description, children, className }: {
  title: string; description?: ReactNode; children?: ReactNode; className?: string;
}) {
  return (
    <Empty className={cn("border border-dashed border-border bg-card md:p-8", className)} data-review-empty="">
      <EmptyHeader className="max-w-md">
        <EmptyTitle><h2 tabIndex={-1} data-empty-heading="" className="text-base font-semibold outline-none">{title}</h2></EmptyTitle>
        {description && <EmptyDescription>{description}</EmptyDescription>}
      </EmptyHeader>
      {children && <EmptyContent className="max-w-md">{children}</EmptyContent>}
    </Empty>
  );
}

/** What a list row shows; the list lays it out. */
export interface RowParts {
  /** One or two lines: what the row is. */
  body: ReactNode;
  /** Beside the actions; hidden below 620 px, or moved under the body. */
  meta?: ReactNode;
  actions: ReactNode;
  /** The expanded row's details. */
  details?: ReactNode;
  /** A failed write, with its Retry. */
  error?: ReactNode;
}

export interface RowContext {
  focused: boolean;
  selected: boolean;
  expanded: boolean;
  /** -1 except in the current row: Tab never walks every row's buttons. */
  tabIndex: number;
}

const INTERACTIVE = "button, a, input, select, textarea, label, [role='checkbox'], [role='combobox'], [data-row-details]";

/** The paged list of one review tab: toolbar (or selection bar), the rows of
 * the current page and the pager. From lg up the rows scroll in their own
 * pane between the toolbar and the pager. */
export function ReviewList<T>({ nav, state, onState, label, rowLabel, row, metaBelow, searchLabel, filters, filtered,
                                 onClearFilters, bulk, saving, empty, noMatch, size, onSize }: {
  nav: ReviewNav<T>;
  state: ReviewTabState;
  onState: (patch: Partial<ReviewTabState>) => void;
  label: string;
  rowLabel: (item: T) => string;
  row: (item: T, context: RowContext) => RowParts;
  /** Meta moves under the body below 620 px instead of hiding (core memories' kind and scope). */
  metaBelow?: boolean;
  searchLabel: string;
  filters?: ReactNode;
  /** A search or filter narrows the list. */
  filtered: boolean;
  onClearFilters: () => void;
  /** The selection bar's actions for the selected keys. */
  bulk: (keys: string[]) => ReactNode;
  saving: number;
  empty: ReactNode;
  noMatch: string;
  size: number;
  onSize: (size: number) => void;
}) {
  const rows = useRef(new Map<string, HTMLLIElement>());
  const list = useRef<HTMLDivElement>(null);
  const toolbar = useRef<HTMLDivElement>(null);
  const emptyRef = useRef<HTMLDivElement>(null);
  const shownPage = useRef(nav.page);
  const announce = useAnnounce();

  // Keyboard focus follows the current row after keys, decisions and undo.
  useEffect(() => {
    if (!state.focusToken) return;
    const target = state.focus ? rows.current.get(state.focus) : undefined;
    if (target) {
      target.focus({ preventScroll: true });
      target.scrollIntoView({ block: "nearest" });
      return;
    }
    const fallback = emptyRef.current?.querySelector<HTMLElement>("[data-autofocus]")
      ?? emptyRef.current?.querySelector<HTMLElement>("[data-empty-heading]");
    fallback?.focus();
  }, [state.focusToken]); // eslint-disable-line react-hooks/exhaustive-deps -- only when asked to move focus

  // A new page starts at its top.
  useEffect(() => {
    if (shownPage.current === nav.page) return;
    shownPage.current = nav.page;
    if (list.current) list.current.scrollTop = 0;
    const top = toolbar.current?.getBoundingClientRect().top ?? 0;
    if (top < 0) toolbar.current?.scrollIntoView({ block: "start" });
  }, [nav.page]);

  // Say how many match while a search or filter narrows the list.
  const total = nav.keys.length;
  useEffect(() => {
    if (filtered) announce(`${total.toLocaleString()} match`);
  }, [state.query, state.filter, state.from, filtered]); // eslint-disable-line react-hooks/exhaustive-deps -- on a filter change only

  const offPage = nav.selected.filter((key) => !nav.shownKeys.includes(key)).length;
  const pageBox = (
    <Checkbox aria-label="Select all on this page" checked={nav.pageSelected}
      indeterminate={!nav.pageSelected && nav.shownKeys.some((key) => state.selected.has(key))}
      disabled={!nav.shownKeys.length} onCheckedChange={() => nav.togglePage()} className="mx-1" />
  );
  const savingNote = saving > 0 && (
    <span className="flex items-center gap-1 text-xs text-muted-foreground" aria-hidden><Spinner className="size-3" /> Saving {saving}…</span>
  );
  const compact = <CompactPager page={nav.page} pages={nav.pages} size={size} shown={nav.shown.length} total={total} onPage={(p) => nav.goTo(p)} />;

  const search = (
    <InputGroup className="h-8 w-auto min-w-32 flex-1 basis-44 bg-background sm:max-w-72">
      <InputGroupAddon><Search aria-hidden /></InputGroupAddon>
      <InputGroupInput id="review-search" type="search" aria-label="Search" placeholder={searchLabel} value={state.query}
        onChange={(e) => onState({ ...RESET, query: e.target.value })}
        onKeyDown={(e) => {
          // Chromium clears a search field on Escape without telling React,
          // so the first Escape clears the search here, the second leaves.
          if (e.key === "Escape") {
            e.preventDefault();
            if (state.query) onState({ ...RESET, query: "" });
            else e.currentTarget.blur();
          } else if (e.key === "ArrowDown") {
            e.preventDefault();
            nav.move(1);
          }
        }} />
    </InputGroup>
  );

  const bar = nav.selected.length ? (
    <div ref={toolbar} role="toolbar" aria-label="Selected rows"
      className="flex scroll-mt-12 flex-wrap items-center gap-2 rounded-t-xl border border-b-0 border-border bg-muted px-2 py-1.5">
      {pageBox}
      <span className="text-sm font-medium tabular-nums">{nav.selected.length} selected</span>
      {offPage > 0 && <span className="text-xs text-muted-foreground">({offPage} on other pages)</span>}
      {bulk(nav.selected)}
      <Button variant="ghost" size="sm" onClick={nav.clear}>Clear <Kbd className="@max-[620px]/page:hidden">Esc</Kbd></Button>
      {savingNote}
      {compact}
    </div>
  ) : (
    <div ref={toolbar} className="flex scroll-mt-12 flex-wrap items-center gap-2 rounded-t-xl border border-b-0 border-border bg-card px-2 py-1.5">
      {pageBox}
      {search}
      {filters}
      {savingNote}
      {compact}
    </div>
  );

  const banner = total > 0 && nav.pageSelected && total > nav.shown.length && nav.selected.length < total && (
    <div className="border border-b-0 border-border bg-muted px-3 py-1.5 text-xs">
      {nav.shown.length} on this page selected ·{" "}
      <button type="button" className="font-medium underline underline-offset-2" onClick={nav.selectAll}>Select all {total.toLocaleString()}</button>
    </div>
  );

  // The toolbar keeps its place whether rows match or not, so typing a
  // search that matches nothing keeps focus in the field.
  return (
    <div ref={emptyRef} className="flex min-h-0 flex-col">
      {(total > 0 || filtered) && bar}
      {banner}
      {total > 0 ? (
        <>
          <div ref={list} className="rounded-b-xl border border-border bg-card lg:min-h-0 lg:shrink lg:overflow-y-auto">
            <ul aria-label={label} className="divide-y divide-border">
              {nav.shown.map((item, index) => {
                const key = nav.shownKeys[index];
                const context: RowContext = { focused: key === nav.current, selected: state.selected.has(key),
                                              expanded: state.expanded === key, tabIndex: key === nav.current ? 0 : -1 };
                const parts = row(item, context);
                const name = rowLabel(item);
                return (
                  <li key={key} data-review-row="" aria-label={name} tabIndex={context.tabIndex}
                    ref={(element) => { if (element) rows.current.set(key, element); else rows.current.delete(key); }}
                    onFocus={() => nav.focusRow(key)}
                    onClick={(e) => { if (!(e.target as HTMLElement).closest(INTERACTIVE)) nav.expand(key); }}
                    className={cn(
                      "relative grid grid-cols-[auto_minmax(0,1fr)_auto_auto] items-center gap-x-3 gap-y-1 px-3 py-2 outline-none motion-safe:transition-colors",
                      "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
                      context.selected && "bg-foreground/[0.035]",
                      context.focused && "bg-muted before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-foreground",
                    )}>
                    <Checkbox aria-label={`Select ${name}`} checked={context.selected} tabIndex={context.tabIndex}
                      onCheckedChange={(_, details) => nav.toggle(key, (details.event as MouseEvent | undefined)?.shiftKey ?? false)} />
                    <div className="min-w-0">{parts.body}</div>
                    <div className={cn("text-xs whitespace-nowrap text-muted-foreground tabular-nums",
                      metaBelow ? "@max-[620px]/page:col-span-2 @max-[620px]/page:col-start-2 @max-[620px]/page:row-start-2" : "@max-[620px]/page:hidden")}>
                      {parts.meta}
                    </div>
                    <div className="flex items-center gap-1 justify-self-end">{parts.actions}</div>
                    {parts.details && <div data-row-details="" className="col-span-full sm:col-start-2">{parts.details}</div>}
                    {parts.error && <div className="col-span-full sm:col-start-2">{parts.error}</div>}
                  </li>
                );
              })}
            </ul>
          </div>
          <Pager page={nav.page} pages={nav.pages} size={size} shown={nav.shown.length} total={total}
            onPage={(p) => nav.goTo(p)} onSize={onSize} />
        </>
      ) : filtered ? (
        <ReviewEmpty title={noMatch} className="rounded-t-none">
          <Button variant="outline" size="sm" data-autofocus="" onClick={onClearFilters}>Clear filters</Button>
        </ReviewEmpty>
      ) : empty}
    </div>
  );
}

/** A failed write's line under its row. */
export function RowError({ message, onRetry, tabIndex }: { message: string; onRetry: () => void; tabIndex: number }) {
  return (
    <p role="alert" className="flex flex-wrap items-center gap-2 text-xs text-destructive">
      {message}
      <Button variant="outline" size="xs" tabIndex={tabIndex} onClick={onRetry}>Retry</Button>
    </p>
  );
}

