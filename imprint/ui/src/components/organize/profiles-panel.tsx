"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { PaneFilter, PaneItem } from "@/components/split-page";
import { ReviewEmpty, type ReviewTabState } from "@/components/review/review-list";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { profileText, type PlacementTab } from "@/components/organize/placements-panel";
import type { OrganizeProposal, OrganizeReview } from "@/lib/api";
import { ago } from "@/lib/queue";

/** What Organize thinks each project is: the profiles it wrote, which feed
 * the local model's placements, with links to that project's moves. */
export function ProfilesPanel({ profiles, proposals, query, project, onChange, onOpen }: {
  profiles: OrganizeReview["profiles"];
  proposals: OrganizeProposal[];
  query: string;
  project: string;
  onChange: (patch: { query?: string; project?: string }) => void;
  onOpen: (tab: PlacementTab, patch: Partial<ReviewTabState>) => void;
}) {
  if (!profiles.length) return (
    <ReviewEmpty title="Profiles need the local helper model."
      description="Organize writes a short profile of each project with it, and uses the profiles to place memories.">
      <Link href="/settings/#settings-organize" className="text-sm underline underline-offset-2">Settings ↗</Link>
    </ReviewEmpty>
  );
  const sorted = [...profiles].sort((a, b) => a.project.localeCompare(b.project));
  const shown = sorted.filter((p) => p.project.toLowerCase().includes(query.trim().toLowerCase()));
  const selected = sorted.find((p) => p.project === project) ?? shown[0] ?? sorted[0];
  const waiting = proposals.filter((p) => p.status === "pending" && p.target === selected.project).length;
  const moved = proposals.filter((p) => p.status === "auto" && p.target === selected.project).length;
  const items = sorted.map((p) => ({ value: p.project, label: p.project }));
  return (
    <div className="grid min-h-0 flex-1 gap-4 lg:grid-rows-[minmax(0,1fr)] @min-[760px]/page:grid-cols-[15rem_minmax(0,1fr)]">
      <nav aria-label="Projects" className="hidden min-h-0 flex-col gap-2 @min-[760px]/page:flex">
        <PaneFilter label="Filter projects" value={query} onChange={(next) => onChange({ query: next })} />
        <ScrollArea className="max-h-[70vh] rounded-xl bg-card ring-1 ring-foreground/10 lg:max-h-none lg:min-h-0 lg:flex-1">
          <div className="space-y-0.5 p-1.5">
            {shown.map((p) => (
              <PaneItem key={p.project} selected={p.project === selected.project} onClick={() => onChange({ project: p.project })}>
                <span className="min-w-0 truncate">{p.project}</span>
              </PaneItem>
            ))}
          </div>
        </ScrollArea>
      </nav>
      <div className="flex min-h-0 min-w-0 flex-col gap-3">
        <Select items={items} value={selected.project} onValueChange={(next) => next && onChange({ project: next })}>
          <SelectTrigger aria-label="Project" className="h-8 w-full max-w-xs @min-[760px]/page:hidden"><SelectValue /></SelectTrigger>
          <SelectContent>{items.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent>
        </Select>
        <article aria-label={`Profile of ${selected.project}`}
          className="min-h-0 space-y-3 overflow-y-auto rounded-xl border border-border bg-card p-4 lg:flex-initial">
          <header className="space-y-1">
            <h2 className="text-lg font-semibold">{selected.project}</h2>
            <p className="text-xs text-muted-foreground">
              Written {ago(selected.updated)} by the local helper from this project&apos;s memories: evidence about the project, not fact.
            </p>
          </header>
          <p className="text-sm leading-relaxed whitespace-pre-line">{profileText(selected.text)}</p>
          <div className="flex flex-wrap gap-2">
            {waiting > 0 && (
              <Button variant="outline" size="sm" onClick={() => onOpen("review", { group: selected.project })}>
                {waiting} waiting to move here <ArrowRight />
              </Button>
            )}
            {moved > 0 && (
              <Button variant="outline" size="sm" onClick={() => onOpen("moved", { group: selected.project, view: "all" })}>
                {moved} moved here automatically <ArrowRight />
              </Button>
            )}
            <Button variant="ghost" size="sm" nativeButton={false} render={<Link href={`/projects/?project=${encodeURIComponent(selected.project)}`} />}>Open in Projects ↗</Button>
          </div>
        </article>
      </div>
    </div>
  );
}
