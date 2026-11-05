"use client";

import { useState } from "react";
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
}

/** Target projects by count beside the list, from 760 px of content up. A
 * chosen project that emptied stays listed, with a check. */
export function GroupRail({ label, groups, total, value, onChange }: {
  label: string; groups: RailGroup[]; total: number; value: string; onChange: (group: string) => void;
}) {
  const [filter, setFilter] = useState("");
  const shown = groups.filter((group) => group.name.toLowerCase().includes(filter.trim().toLowerCase()));
  return (
    <nav aria-label={label} className="hidden min-h-0 flex-col gap-2 @min-[760px]/page:flex">
      {groups.length > 8 && <PaneFilter label="Filter projects" value={filter} onChange={setFilter} />}
      <ScrollArea className="max-h-[70vh] rounded-xl bg-card ring-1 ring-foreground/10 lg:max-h-none lg:min-h-0 lg:flex-1">
        <div className="space-y-0.5 p-1.5">
          <PaneItem selected={value === ""} onClick={() => onChange("")}>
            <span className="min-w-0 truncate">All projects</span><PaneCount value={total} />
          </PaneItem>
          {shown.map((group) => {
            const item = (
              <PaneItem key={group.name} selected={value === group.name} onClick={() => onChange(group.name)}>
                {group.fresh && <span aria-label="New" className="size-1.5 shrink-0 rounded-full bg-foreground" />}
                <span className="min-w-0 truncate">{group.name}</span>
                {group.count ? <PaneCount value={group.count} />
                  : <Check aria-label="Done" className="ml-auto size-3.5 shrink-0 text-muted-foreground" />}
              </PaneItem>
            );
            return group.hint ? <Hint key={group.name} label={group.hint} side="right">{item}</Hint> : item;
          })}
        </div>
      </ScrollArea>
    </nav>
  );
}

/** The rail as a select below 760 px of content, in the group header in
 * place of its title. */
export function GroupSelect({ groups, total, value, onChange, className }: {
  groups: RailGroup[]; total: number; value: string; onChange: (group: string) => void; className?: string;
}) {
  const items = [{ value: null as string | null, label: `All projects (${total.toLocaleString()})` },
                 ...groups.map((group) => ({ value: group.name, label: `${group.name} (${group.count.toLocaleString()})` }))];
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
