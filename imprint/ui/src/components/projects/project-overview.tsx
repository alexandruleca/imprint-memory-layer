"use client";

import { ArrowRight } from "lucide-react";
import { FacetBars } from "@/components/split-page";
import { KINDS, kindOf } from "@/components/sources/source-row";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { SourceRow } from "@/lib/api";
import { ago } from "@/lib/queue";

type Facet = { name: string; count: number }[];

/** What `GET /api/project/{name}` returns: the project's memories by facet. */
export interface ProjectDetail {
  project: string;
  count: number;
  color: string;
  types: Facet;
  domains: Facet;
  langs: Facet;
  topics: Facet;
}

/** A profile without its "Project profile: name" heading. */
const profileText = (text: string) => text.replace(/^Project profile: [^\n]*\n\n/, "");

/** A project at a glance: what Organize thinks it is, its sources by kind,
 * and its memories by topic, kind, domain and language. */
export function ProjectOverview({ detail, sources, profile, onTopic, onSources }: {
  detail: ProjectDetail | null;
  /** null while they load. */
  sources: SourceRow[] | null;
  profile?: { text: string; updated: number };
  onTopic: (topic: string) => void;
  /** Open the Sources tab, filtered to one kind ("all" for every one). */
  onSources: (kind: string) => void;
}) {
  const kinds = sources ? KINDS.map((kind) => ({ ...kind, count: sources.filter((row) => kindOf(row) === kind.value).length }))
    .filter((kind) => kind.count) : [];
  return (
    <div className="space-y-4">
      {profile && (
        <section aria-label="About this project" className="space-y-1.5 rounded-xl border border-border bg-card p-4">
          <h3 className="text-sm font-semibold">About</h3>
          <p className="text-sm leading-relaxed whitespace-pre-line">{profileText(profile.text)}</p>
          <p className="text-xs text-muted-foreground">
            Written {ago(profile.updated)} by the local helper from this project&apos;s memories: evidence about the project, not fact.
          </p>
        </section>
      )}
      <section aria-label="Sources by kind" className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-card px-4 py-3 text-sm">
        {!sources ? <Skeleton className="h-5 w-64" /> : !sources.length ? (
          <span className="text-muted-foreground">No sources: this project holds only memories saved without one.</span>
        ) : (
          <>
            <Button variant="ghost" size="sm" className="font-medium" onClick={() => onSources("all")}>
              {sources.length.toLocaleString()} {sources.length === 1 ? "source" : "sources"} <ArrowRight />
            </Button>
            {kinds.map((kind) => (
              <Button key={kind.value} variant="outline" size="sm" className="font-normal" onClick={() => onSources(kind.value || "none")}>
                {kind.label} <span className="text-muted-foreground tabular-nums">{kind.count.toLocaleString()}</span>
              </Button>
            ))}
          </>
        )}
      </section>
      <section aria-label="Memories by facet" className="rounded-xl border border-border bg-card p-4">
        {detail ? (
          detail.types.length || detail.topics.length || detail.domains.length || detail.langs.length ? (
            <div className="grid gap-x-8 gap-y-6 @min-[620px]/page:grid-cols-2">
              <FacetBars title="Topics" items={detail.topics} onSelect={onTopic} />
              <FacetBars title="Kinds of memory" items={detail.types} />
              <FacetBars title="Domains" items={detail.domains} />
              <FacetBars title="Languages" items={detail.langs} />
            </div>
          ) : <p className="text-sm text-muted-foreground">No tags yet: retag this project&apos;s memories from Commands to see its topics.</p>
        ) : <div className="grid gap-4 @min-[620px]/page:grid-cols-2" aria-busy="true">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-32 w-full" />)}</div>}
      </section>
    </div>
  );
}
