"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";

export const PAGE_SIZES = [10, 20, 50] as const;

/** "21–40 of 91". */
export function rangeLabel(page: number, size: number, shown: number, total: number) {
  return total ? `${(page * size + 1).toLocaleString()}–${(page * size + shown).toLocaleString()} of ${total.toLocaleString()}` : "0 of 0";
}

/** The page numbers to show: every one up to seven pages, otherwise the
 * first, the last and the current one with its neighbours. */
export function pageNumbers(page: number, pages: number): (number | "gap")[] {
  const shown: (number | "gap")[] = [];
  for (let i = 0; i < pages; i++) {
    if (pages <= 7 || i === 0 || i === pages - 1 || Math.abs(i - page) <= 1) shown.push(i);
    else if (shown.at(-1) !== "gap") shown.push("gap");
  }
  return shown;
}

/** The pager under a review list: range, rows per page and numbered pages.
 * Named "Pagination": the sidebar is the page's "Pages" navigation.
 * Below 512 px of content only "‹ 2 / 5 ›" remains. */
export function Pager({ page, pages, size, shown, total, onPage, onSize }: {
  page: number; pages: number; size: number; shown: number; total: number;
  onPage: (page: number) => void; onSize: (size: number) => void;
}) {
  if (!total) return null;
  return (
    <nav aria-label="Pagination" className="flex flex-wrap items-center gap-1 pt-2 text-xs text-muted-foreground">
      <span className="tabular-nums">{rangeLabel(page, size, shown, total)}</span>
      <span className="flex-1" />
      <label className="flex items-center gap-1.5">
        Rows
        <Select items={PAGE_SIZES.map((n) => ({ value: String(n), label: String(n) }))} value={String(size)}
          onValueChange={(value) => value && onSize(Number(value))}>
          <SelectTrigger size="sm" aria-label="Rows per page" className="h-7 w-16 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            {PAGE_SIZES.map((n) => <SelectItem key={n} value={String(n)}>{n}</SelectItem>)}
          </SelectContent>
        </Select>
      </label>
      <Button variant="ghost" size="sm" disabled={page <= 0} onClick={() => onPage(page - 1)} aria-label="Previous page">
        <ChevronLeft aria-hidden /><span className="@max-[520px]/page:hidden">Previous</span>
      </Button>
      {pageNumbers(page, pages).map((n, i) => n === "gap"
        ? <span key={`gap${i}`} aria-hidden className="px-1 @max-[520px]/page:hidden">…</span>
        : (
          <Button key={n} variant={n === page ? "default" : "ghost"} size="sm" aria-current={n === page ? "page" : undefined}
            aria-label={`Page ${n + 1}`} className={cn("min-w-7 tabular-nums @max-[520px]/page:hidden")} onClick={() => onPage(n)}>
            {n + 1}
          </Button>
        ))}
      <span className="hidden tabular-nums @max-[520px]/page:inline">{page + 1} / {pages}</span>
      <Button variant="ghost" size="sm" disabled={page >= pages - 1} onClick={() => onPage(page + 1)} aria-label="Next page">
        <span className="@max-[520px]/page:hidden">Next</span><ChevronRight aria-hidden />
      </Button>
    </nav>
  );
}

/** The toolbar's compact pager: range and ‹ ›. */
export function CompactPager({ page, pages, size, shown, total, onPage }: {
  page: number; pages: number; size: number; shown: number; total: number; onPage: (page: number) => void;
}) {
  return (
    <span className="ml-auto flex items-center gap-1 text-xs text-muted-foreground">
      <span className="tabular-nums">{rangeLabel(page, size, shown, total)}</span>
      <span className="flex @max-[620px]/page:hidden">
        <Button variant="ghost" size="icon-xs" disabled={page <= 0} onClick={() => onPage(page - 1)} aria-label="Previous page (top)"><ChevronLeft /></Button>
        <Button variant="ghost" size="icon-xs" disabled={page >= pages - 1} onClick={() => onPage(page + 1)} aria-label="Next page (top)"><ChevronRight /></Button>
      </span>
    </span>
  );
}
