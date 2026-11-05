"use client";

import { useEffect, useRef, useState } from "react";
import { Check } from "lucide-react";
import { Hint } from "@/components/hint";
import { PaneCount, PaneFilter, PaneItem } from "@/components/split-page";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";

/** One target project in the rail. */
export interface RailGroup {
  name: string;
  count: number;
  /** The evidence split, shown as a hint only, which keeps the rail quiet. */
  hint?: string;
  /** Something new arrived in it. */
  fresh?: boolean;
  /** The project's colour, as a dot before its name. */
  color?: string;
}

/** Target projects by count beside the list, from 760 px of content up. A
 * chosen project that emptied stays listed, with a check. `allLabel` null
 * leaves out the "All projects" row (the Projects page always has one open). */
export function GroupRail({ label, groups, total, value, onChange, allLabel = "All projects" }: {
  label: string; groups: RailGroup[]; total: number; value: string; onChange: (group: string) => void;
  allLabel?: string | null;
}) {
  const [filter, setFilter] = useState("");
  const pane = useRef<HTMLDivElement>(null);
  const shown = groups.filter((group) => group.name.toLowerCase().includes(filter.trim().toLowerCase()));

  // The chosen project stays in sight when it changes from elsewhere (a
  // link, the select, J and K), without scrolling the page around the rail.
  useEffect(() => {
    const viewport = pane.current?.closest<HTMLElement>("[data-slot='scroll-area-viewport']");
    const item = pane.current?.querySelector<HTMLElement>("[aria-pressed='true']");
    if (!viewport || !item) return;
    const top = item.offsetTop - viewport.scrollTop;
    if (top < 0) viewport.scrollTop = item.offsetTop - 6;
    else if (top + item.offsetHeight > viewport.clientHeight) viewport.scrollTop = item.offsetTop + item.offsetHeight - viewport.clientHeight + 6;
  }, [value]);

  return (
    <nav aria-label={label} className="hidden min-h-0 flex-col gap-2 @min-[760px]/page:flex">
      {groups.length > 8 && <PaneFilter label="Filter projects" value={filter} onChange={setFilter} />}
      <ScrollArea className="max-h-[70vh] rounded-xl bg-card ring-1 ring-foreground/10 lg:max-h-none lg:min-h-0 lg:flex-1">
        <div ref={pane} className="relative space-y-0.5 p-1.5">
          {allLabel !== null && (
            <PaneItem selected={value === ""} onClick={() => onChange("")}>
              <span className="min-w-0 truncate">{allLabel}</span><PaneCount value={total} />
            </PaneItem>
          )}
          {shown.map((group) => {
            const item = (
              <PaneItem key={group.name} selected={value === group.name} onClick={() => onChange(group.name)}>
                {group.fresh && <span aria-label="New" className="size-1.5 shrink-0 rounded-full bg-foreground" />}
                {group.color && <span aria-hidden className="size-2.5 shrink-0 rounded-full" style={{ backgroundColor: group.color }} />}
                <span className="min-w-0 truncate">{group.name}</span>
                {group.count ? <PaneCount value={group.count} />
                  : <Check aria-label="Done" className="ml-auto size-3.5 shrink-0 text-muted-foreground" />}
              </PaneItem>
            );
            return group.hint ? <Hint key={group.name} label={group.hint} side="right">{item}</Hint> : item;
          })}
          {!shown.length && filter && (
            <p className="px-2.5 py-6 text-center text-sm text-muted-foreground">No project matches &ldquo;{filter}&rdquo;.</p>
          )}
        </div>
      </ScrollArea>
    </nav>
  );
}

/** The rail as a select below 760 px of content, in the group header in
 * place of its title. */
export function GroupSelect({ groups, total, value, onChange, className, allLabel = "All projects" }: {
  groups: RailGroup[]; total: number; value: string; onChange: (group: string) => void; className?: string;
  allLabel?: string | null;
}) {
  const items = [...(allLabel === null ? [] : [{ value: null as string | null, label: `${allLabel} (${total.toLocaleString()})` }]),
                 ...groups.map((group) => ({ value: group.name as string | null, label: `${group.name} (${group.count.toLocaleString()})` }))];
  return (
    <Select items={items} value={value || null} onValueChange={(next) => onChange(next ?? "")}>
      <SelectTrigger aria-label="Project" className={cn("h-8 max-w-[62%] min-w-0 font-medium @min-[760px]/page:hidden", className)}>
        <SelectValue className="truncate" />
      </SelectTrigger>
      <SelectContent>
        {items.map((item) => <SelectItem key={item.value ?? ""} value={item.value}>{item.label}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}
