"use client";

import { Checkbox } from "@/components/ui/checkbox";
import { Empty, EmptyDescription } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { SourceEntry } from "@/lib/types";

type Props = {
  /** null while loading. */
  sources: SourceEntry[] | null;
  selected: Set<string>;
  onSelectedChange: (selected: Set<string>) => void;
};

/** A project's sources with chunk counts, selectable for a move. */
export function ProjectSources({ sources, selected, onSelectedChange }: Props) {
  if (!sources) {
    return (
      <div className="space-y-2" aria-busy="true">
        {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-7 w-full" />)}
      </div>
    );
  }
  if (!sources.length) {
    return (
      <Empty className="border p-4">
        <EmptyDescription>No sources. This project holds only memories saved without one.</EmptyDescription>
      </Empty>
    );
  }
  const all = sources.length > 0 && sources.every((s) => selected.has(s.source));
  const some = !all && sources.some((s) => selected.has(s.source));

  function toggle(source: string, checked: boolean) {
    const next = new Set(selected);
    if (checked) next.add(source); else next.delete(source);
    onSelectedChange(next);
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-8">
            <Checkbox aria-label="Select all sources" checked={all} indeterminate={some}
              onCheckedChange={(checked) => onSelectedChange(checked ? new Set(sources.map((s) => s.source)) : new Set())} />
          </TableHead>
          <TableHead>Source</TableHead>
          <TableHead className="w-20 text-right">Chunks</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sources.map((s) => (
          <TableRow key={s.source} data-state={selected.has(s.source) ? "selected" : undefined}>
            <TableCell>
              <Checkbox aria-label={`Select ${s.source}`} checked={selected.has(s.source)}
                onCheckedChange={(checked) => toggle(s.source, checked)} />
            </TableCell>
            <TableCell className="max-w-0 truncate font-mono text-xs" title={s.source}>{s.source}</TableCell>
            <TableCell className="text-right tabular-nums">{s.chunks.toLocaleString()}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
