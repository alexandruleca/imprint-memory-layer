"use client";

import { Updating } from "@/components/updating";
import type { ReactNode } from "react";
import { Info } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

/** A review page's one-line header: the title, its count, an ⓘ popover
 * with what the page is for, and the actions. */
export function ReviewHeader({ title, count, info, infoLabel, actions, updating }: {
  title: string; count?: number; info?: ReactNode; infoLabel?: string; actions?: ReactNode;
  /** A newer answer is on its way: shows "Updating…" beside the title. */
  updating?: boolean;
}) {
  return (
    <header className="flex flex-wrap items-center gap-x-2 gap-y-2 px-4 pt-4 pb-3 sm:px-8">
      <h1 className="flex items-baseline gap-2 text-2xl font-semibold tracking-tight">
        {title}
        {count !== undefined && <span className="text-base font-normal text-muted-foreground tabular-nums">{count.toLocaleString()}</span>}
      </h1>
      {info && (
        <Popover>
          <PopoverTrigger render={<Button variant="ghost" size="icon-sm" aria-label={infoLabel ?? `About ${title}`} className="text-muted-foreground" />}>
            <Info />
          </PopoverTrigger>
          <PopoverContent align="start" className="w-96 max-w-[calc(100vw-2rem)] text-sm">{info}</PopoverContent>
        </Popover>
      )}
      {updating !== undefined && <Updating show={updating} />}
      {actions && <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/** A tab with its count: a filled pill while it asks for action, "+N" for
 * what is new, and a short name below 620 px of content. The accessible
 * name is the full one with the count: "To review, 91". */
export function CountTab({ value, name, short, count, pill, fresh }: {
  value: string; name: string; short?: string; count: number; pill?: boolean; fresh?: number;
}) {
  return (
    <TabsTrigger value={value} aria-label={`${name}, ${count}`} className="flex-none px-2.5">
      <span className={cn(short && "@max-[620px]/page:hidden")}>{name}</span>
      {short && <span className="hidden @max-[620px]/page:inline">{short}</span>}
      <span className={cn("text-xs tabular-nums", pill && count > 0
        ? "rounded-full bg-primary px-1.5 font-medium text-primary-foreground"
        : "font-normal text-muted-foreground")}>
        {count.toLocaleString()}
      </span>
      {!!fresh && <span className="rounded-full border border-border px-1.5 text-[11px] font-normal text-foreground">+{fresh}</span>}
    </TabsTrigger>
  );
}
