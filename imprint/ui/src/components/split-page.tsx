"use client";

import { useState } from "react";
import { Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

/** A page's title, with its count, a sentence on what it is for and its actions. */
export function PageHeader({ title, count, description, actions, className }: {
  title: string;
  count?: number;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <header className={cn("flex flex-wrap items-end justify-between gap-x-6 gap-y-3 px-4 pt-6 pb-4 sm:px-8", className)}>
      <div className="min-w-0 space-y-1">
        <h1 className="flex items-baseline gap-2 text-2xl font-semibold tracking-tight">
          {title}
          {count !== undefined && <span className="text-base font-normal text-muted-foreground tabular-nums">{count.toLocaleString()}</span>}
        </h1>
        {description && <p className="max-w-2xl text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/** A list beside the selected item's details (Topics, Projects, Sources,
 * Queue, Commands, Settings). From lg up the page fills the window: the
 * header stays put, and the list and the details each scroll on their own
 * between the same top and bottom edges, so neither drags the other out of
 * line. Narrower screens stack them and the page scrolls; the list is capped
 * there so the details stay reachable. */
export function SplitPage({ header, list, filter, children, listWidth = "18rem", fill = false }: {
  header: React.ReactNode;
  list: React.ReactNode;
  /** Above the list, outside its scroll pane. */
  filter?: React.ReactNode;
  children: React.ReactNode;
  listWidth?: string;
  /** The details lay themselves out in the pane's full height (a log that
   * takes the rest of it) instead of scrolling as one column. */
  fill?: boolean;
}) {
  return (
    <div className="flex min-h-full flex-col lg:h-full">
      {header}
      <div
        style={{ "--list-width": listWidth } as React.CSSProperties}
        className="grid flex-1 gap-4 px-4 pb-4 sm:px-8 sm:pb-6 lg:min-h-0 lg:grid-cols-[var(--list-width)_minmax(0,1fr)] lg:grid-rows-[minmax(0,1fr)] lg:gap-6"
      >
        <div className="flex min-w-0 flex-col gap-2 lg:min-h-0">
          {filter}
          <ScrollArea className="max-h-72 rounded-xl bg-card ring-1 ring-foreground/10 lg:max-h-none lg:min-h-0 lg:flex-1">
            <div className="p-1.5">{list}</div>
          </ScrollArea>
        </div>
        {fill ? (
          <div className="flex min-w-0 flex-col gap-4 lg:min-h-0">{children}</div>
        ) : (
          <ScrollArea className="min-w-0 lg:min-h-0">
            <div className="space-y-4 lg:pr-3">{children}</div>
          </ScrollArea>
        )}
      </div>
    </div>
  );
}

function paneItem(selected: boolean, className?: string) {
  return cn(
    "flex w-full min-w-0 items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm transition-colors outline-none",
    "hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50",
    selected ? "bg-muted font-medium text-foreground" : "text-foreground/85",
    className,
  );
}

/** One selectable row of a SplitPage list. */
export function PaneItem({ selected, className, ...props }: React.ComponentProps<"button"> & { selected: boolean }) {
  return <button type="button" aria-pressed={selected} className={paneItem(selected, className)} {...props} />;
}

/** A SplitPage list row that navigates, such as to a section in the URL's hash. */
export function PaneLink({ selected, className, ...props }: React.ComponentProps<"a"> & { selected: boolean }) {
  return <a aria-current={selected ? "page" : undefined} className={paneItem(selected, className)} {...props} />;
}

/** A heading between groups of a SplitPage list. */
export function PaneHeading({ children }: { children: React.ReactNode }) {
  return <p className="px-2.5 pt-3 pb-1 text-[11px] font-semibold tracking-wider text-muted-foreground uppercase first:pt-1">{children}</p>;
}

/** The count at the end of a list row. */
export function PaneCount({ value }: { value: number }) {
  return <span className="ml-auto shrink-0 text-xs font-normal text-muted-foreground tabular-nums">{value.toLocaleString()}</span>;
}

export function PaneFilter({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <InputGroup className="bg-card">
      <InputGroupAddon><Search aria-hidden /></InputGroupAddon>
      <InputGroupInput aria-label={label} placeholder={label} value={value} onChange={(e) => onChange(e.target.value)} />
    </InputGroup>
  );
}

/** Counts by name as bars sized against the largest, the first few shown. */
export function FacetBars({ title, items, shown = 6, onSelect }: {
  title: string;
  items: { name: string; count: number }[];
  shown?: number;
  onSelect?: (name: string) => void;
}) {
  const [all, setAll] = useState(false);
  if (!items.length) return null;
  const max = Math.max(...items.map((i) => i.count));
  const visible = all ? items : items.slice(0, shown);
  return (
    <section className="min-w-0">
      <h3 className="mb-2 text-xs font-medium text-muted-foreground">{title}</h3>
      <ul className="space-y-1">
        {visible.map((item) => {
          const bar = (
            <>
              <span
                aria-hidden
                className="absolute inset-y-0 left-0 rounded-md bg-muted"
                style={{ width: `${Math.max(4, (item.count / max) * 100)}%` }}
              />
              <span className="relative min-w-0 flex-1 truncate">{item.name}</span>
              <span className="relative shrink-0 text-xs text-muted-foreground tabular-nums">{item.count.toLocaleString()}</span>
            </>
          );
          const row = "relative flex w-full items-center gap-3 rounded-md px-2 py-1 text-left text-sm";
          return (
            <li key={item.name}>
              {onSelect ? (
                <button type="button" onClick={() => onSelect(item.name)}
                  className={cn(row, "outline-none hover:ring-1 hover:ring-foreground/15 focus-visible:ring-3 focus-visible:ring-ring/50")}>
                  {bar}
                </button>
              ) : <div className={row}>{bar}</div>}
            </li>
          );
        })}
      </ul>
      {items.length > shown && (
        <Button variant="ghost" size="xs" className="mt-1 font-normal text-muted-foreground" onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `Show all ${items.length}`}
        </Button>
      )}
    </section>
  );
}
