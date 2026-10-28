"use client";

import { BookOpenText } from "lucide-react";
import { Button } from "@/components/ui/button";
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
  onRead: (source: SourceEntry) => void;
};

/** A project's sources with chunk counts, selectable for a move and readable whole. */
export function ProjectSources({ sources, selected, onSelectedChange, onRead }: Props) {
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
  const movable = sources.filter((s) => !s.linked);
  const all = movable.length > 0 && movable.every((s) => selected.has(s.source));
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
              disabled={!movable.length} onCheckedChange={(checked) => onSelectedChange(checked ? new Set(movable.map((s) => s.source)) : new Set())} />
          </TableHead>
          <TableHead>Source</TableHead>
          <TableHead className="w-20 text-right">Chunks</TableHead>
          <TableHead className="w-10"><span className="sr-only">Read</span></TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sources.map((s) => (
          <TableRow key={s.source} data-state={selected.has(s.source) ? "selected" : undefined}>
            <TableCell>
              <Checkbox aria-label={`Select ${s.source}`} checked={selected.has(s.source)} disabled={s.linked}
                onCheckedChange={(checked) => toggle(s.source, checked)} />
            </TableCell>
            <TableCell className="max-w-0" title={s.title ? `${s.title}\n${s.source}` : s.source}>
              {s.linked && <span className="block text-xs text-muted-foreground">Linked from {s.project || "Unassigned"}</span>}
              {s.title ? (
                <>
                  <span className="block truncate text-sm">{s.title}</span>
                  <span className="block truncate font-mono text-[11px] text-muted-foreground">{s.source}</span>
                </>
              ) : <span className="block truncate font-mono text-xs">{s.source}</span>}
            </TableCell>
            <TableCell className="text-right tabular-nums">{s.chunks.toLocaleString()}</TableCell>
            <TableCell>
              <Button variant="ghost" size="icon-sm" aria-label={`Read ${s.title || s.source}`} onClick={() => onRead(s)}>
                <BookOpenText aria-hidden />
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
