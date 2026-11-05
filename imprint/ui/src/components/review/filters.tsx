"use client";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

export type Facet = { value: string; label: string; count?: number };

/** Facet chips with counts, one at a time; below 620 px of content they
 * fold into one Filter select. "all" is no filter. */
export function Facets({ label, facets, value, onChange }: {
  label: string; facets: Facet[]; value: string; onChange: (value: string) => void;
}) {
  const text = (facet: Facet) => (facet.count === undefined || facet.value === "all" ? facet.label : `${facet.label} ${facet.count}`);
  return (
    <>
      <ToggleGroup aria-label={label} size="sm" spacing={0} value={[value]}
        onValueChange={(next) => onChange(next[0] ?? "all")}
        className="rounded-lg border border-border p-0.5 @max-[620px]/page:hidden">
        {facets.map((facet) => (
          <ToggleGroupItem key={facet.value} value={facet.value} className="h-6 rounded-md! px-2 text-xs font-normal aria-pressed:font-medium">
            {facet.label}
            {facet.count !== undefined && facet.value !== "all" && <span className="text-muted-foreground tabular-nums">{facet.count}</span>}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      <Select items={facets.map((facet) => ({ value: facet.value, label: text(facet) }))} value={value}
        onValueChange={(next) => onChange(next ?? "all")}>
        <SelectTrigger size="sm" aria-label="Filter" className="hidden max-w-32 text-xs @max-[620px]/page:flex"><SelectValue /></SelectTrigger>
        <SelectContent>
          {facets.map((facet) => <SelectItem key={facet.value} value={facet.value}>{text(facet)}</SelectItem>)}
        </SelectContent>
      </Select>
    </>
  );
}

/** A select over where rows came from; "" for anywhere. Option values must not be empty. */
export function FromFilter({ label, anyLabel, options, value, onChange, className }: {
  label: string; anyLabel: string; options: { value: string; label: string }[]; value: string;
  onChange: (value: string) => void; className?: string;
}) {
  // Base UI's select keeps null for "nothing chosen", as on the Search page.
  const items = [{ value: null as string | null, label: anyLabel }, ...options];
  return (
    <Select items={items} value={value || null} onValueChange={(next) => onChange(next ?? "")}>
      <SelectTrigger size="sm" aria-label={label} className={cn("max-w-40 shrink-0 text-xs", className)}>
        <SelectValue className="truncate" />
      </SelectTrigger>
      <SelectContent>
        {items.map((item) => <SelectItem key={item.value ?? ""} value={item.value}>{item.label}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}
