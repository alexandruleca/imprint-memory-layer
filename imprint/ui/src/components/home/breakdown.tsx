"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/** One kind of grouping (projects, topics, types, …): its largest groups as
 * bars from one baseline, with the value at each bar's tip. A single series,
 * so no legend; the numbers are text, so the list is its own table. */
export function Breakdown({ title, items, total, count, noun, href, itemHref }: {
  title: string;
  items: [string, number][];
  /** Memories in the workspace, for each group's share. */
  total: number;
  /** How many groups exist; the list shows the largest. */
  count?: number;
  noun?: string;
  href?: string;
  itemHref?: (name: string) => string;
}) {
  const max = items[0]?.[1] ?? 0;
  return (
    <Card className="gap-3">
      <CardHeader>
        <CardTitle className="flex items-baseline gap-2 text-sm">
          <h3>{title}</h3>
          {count !== undefined && count > 0 && (
            <span className="text-xs font-normal text-muted-foreground tabular-nums">
              {count.toLocaleString()} {noun ?? title.toLowerCase()}
            </span>
          )}
        </CardTitle>
        {href && (
          <CardAction>
            <Link href={href} className="inline-flex items-center gap-1 rounded-sm text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
              All<span className="sr-only"> {title.toLowerCase()}</span>
              <ArrowRight className="size-3" aria-hidden />
            </Link>
          </CardAction>
        )}
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="py-2 text-xs text-muted-foreground">None yet</p>
        ) : (
          <ul className="-mx-1.5 space-y-0.5">
            {items.slice(0, 6).map(([label, value]) => {
              const share = total ? Math.round((value / total) * 100) : 0;
              const row = (
                <>
                  <span className="flex items-baseline gap-3">
                    <span className="min-w-0 flex-1 truncate text-muted-foreground group-hover/row:text-foreground" title={label}>{label}</span>
                    <span className="font-medium tabular-nums">{value.toLocaleString()}</span>
                    <span className="w-9 text-right text-muted-foreground tabular-nums">{share < 1 ? "<1" : share}%</span>
                  </span>
                  <span className="block h-1.5" aria-hidden>
                    <span
                      className="block h-full rounded-r-[4px] bg-foreground/60 transition-[width,background-color] group-hover/row:bg-foreground motion-safe:duration-500"
                      style={{ width: `${max ? Math.max(1, (value / max) * 100) : 0}%` }}
                    />
                  </span>
                </>
              );
              const rowClass = "group/row flex flex-col gap-1.5 rounded-md px-1.5 py-1.5 text-xs";
              return (
                <li key={label}>
                  {itemHref ? (
                    <Link
                      href={itemHref(label)}
                      aria-label={`${label}: ${value.toLocaleString()} memories, ${share < 1 ? "under 1" : share}%`}
                      className={`${rowClass} outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring`}
                    >
                      {row}
                    </Link>
                  ) : (
                    <div className={rowClass}>{row}</div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
