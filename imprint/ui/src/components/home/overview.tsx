"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { Card } from "@/components/ui/card";
import type { DashboardSummary } from "@/lib/api";
import { cn } from "@/lib/utils";

const monthName = (month: string, style: "short" | "long" = "short") => {
  const [year, m] = month.split("-").map(Number);
  return new Date(year, (m || 1) - 1, 1).toLocaleDateString(undefined, style === "long" ? { month: "long", year: "numeric" } : { month: "short" });
};

/** The headline: how many memories, how many arrived this week, and a column
 * per month for the last year. One series, so the title names it and no
 * legend is drawn; the readout above the columns doubles as the tooltip. */
export function MemoryHero({ summary }: { summary: DashboardSummary }) {
  return (
    <Card className="justify-center gap-6 px-5 py-5 sm:px-6">
      <div className="flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
        <div className="min-w-0 space-y-1">
          <h2 className="text-sm font-medium text-muted-foreground">Memories</h2>
          <p className="text-5xl font-semibold tracking-tight">{summary.total.toLocaleString()}</p>
          <p className="text-sm text-muted-foreground">
            {summary.added_7d > 0
              ? <><span className="font-medium text-foreground">+{summary.added_7d.toLocaleString()}</span> in the last 7 days</>
              : "None added in the last 7 days"}
          </p>
        </div>
        <MonthlyColumns timeline={summary.timeline} />
      </div>
    </Card>
  );
}

function MonthlyColumns({ timeline }: { timeline: DashboardSummary["timeline"] }) {
  const [active, setActive] = useState<number | null>(null);
  const tableId = useId();
  if (!timeline.length) return null;
  const max = Math.max(1, ...timeline.map((m) => m.count));
  const last = timeline.length - 1;
  const shown = active ?? last;
  const point = timeline[shown];
  return (
    <figure className="w-full md:max-w-sm" aria-describedby={tableId}>
      <figcaption className="mb-2 flex items-baseline justify-between gap-3 text-xs text-muted-foreground">
        <span>Added per month</span>
        <span className="tabular-nums" aria-hidden>
          <span className="font-medium text-foreground">{point.count.toLocaleString()}</span>
          {" "}in {monthName(point.month, "long")}{shown === last ? " so far" : ""}
        </span>
      </figcaption>
      <div className="flex h-24 items-end gap-0.5 border-b border-border" onMouseLeave={() => setActive(null)} aria-hidden>
        {timeline.map((m, i) => (
          <div key={m.month} className="flex h-full flex-1 items-end justify-center" onMouseEnter={() => setActive(i)}>
            <div
              className={cn(
                "w-full max-w-6 rounded-t-[4px] transition-colors motion-safe:duration-150",
                i === shown ? "bg-foreground" : "bg-foreground/25",
              )}
              style={{ height: m.count ? `max(2px, ${(m.count / max) * 100}%)` : 0 }}
            />
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-muted-foreground" aria-hidden>
        <span>{monthName(timeline[0].month)}</span>
        <span>{monthName(timeline[last].month)}</span>
      </div>
      <table id={tableId} className="sr-only">
        <caption>Memories added per month</caption>
        <thead><tr><th scope="col">Month</th><th scope="col">Memories</th></tr></thead>
        <tbody>
          {timeline.map((m) => <tr key={m.month}><th scope="row">{monthName(m.month, "long")}</th><td>{m.count.toLocaleString()}</td></tr>)}
        </tbody>
      </table>
    </figure>
  );
}

/** A count that opens the page listing it. */
export function StatTile({ label, value, detail, href, icon: Icon }: {
  label: string; value: number; detail?: string; href?: string; icon: React.ElementType;
}) {
  const body = (
    <>
      <span className="flex items-center justify-between gap-2 text-sm text-muted-foreground">
        <span className="flex items-center gap-1.5"><Icon className="size-3.5" aria-hidden />{label}</span>
        {href && <ArrowUpRight className="size-3.5 opacity-0 transition-opacity group-hover/tile:opacity-100 group-focus-visible/tile:opacity-100" aria-hidden />}
      </span>
      <span className="text-2xl font-semibold tracking-tight">{value.toLocaleString()}</span>
      {detail && <span className="truncate text-xs text-muted-foreground" title={detail}>{detail}</span>}
    </>
  );
  const className = "group/tile flex min-w-0 flex-col gap-1 rounded-xl bg-card px-4 py-3.5 ring-1 ring-foreground/10";
  if (!href) return <div className={className}>{body}</div>;
  return (
    <Link
      href={href}
      className={cn(className, "transition-[box-shadow,transform] outline-none hover:shadow-sm hover:ring-foreground/20 focus-visible:ring-2 focus-visible:ring-ring motion-safe:hover:-translate-y-px")}
    >
      {body}
    </Link>
  );
}
