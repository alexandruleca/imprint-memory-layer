"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { ApiError, getDashboard, type DashboardSummary } from "@/lib/api";
import { useCached } from "@/lib/use-cached";
import { useQueue } from "@/lib/queue";
import { DashboardSkeleton } from "@/components/loaders";
import { IngestionProgress } from "@/components/ingestion-progress";
import { QuickIngest } from "@/components/quick-ingest";
import { PageHeader } from "@/components/split-page";
import { MemoryHero, StatTile } from "@/components/home/overview";
import { Breakdown } from "@/components/home/breakdown";
import { Attention, Freshness, RecentSources } from "@/components/home/activity";
import { Code, Database, FileCode, FolderOpen, Tags } from "lucide-react";

const list = (value: unknown): [string, number][] =>
  Array.isArray(value) ? value.filter((row): row is [string, number] => Array.isArray(row) && typeof row[1] === "number") : [];
const num = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : 0);

/** The summary with every field present, whatever the server sent. */
function normalize(raw: Partial<DashboardSummary>): DashboardSummary {
  return {
    workspace: typeof raw.workspace === "string" ? raw.workspace : "",
    total: num(raw.total), project_count: num(raw.project_count), projects: list(raw.projects),
    types: list(raw.types), langs: list(raw.langs), lang_count: num(raw.lang_count),
    domains: list(raw.domains), domain_count: num(raw.domain_count), layers: list(raw.layers),
    topics: list(raw.topics), topic_count: num(raw.topic_count), source_count: num(raw.source_count),
    tag_pending: num(raw.tag_pending), added_7d: num(raw.added_7d),
    timeline: Array.isArray(raw.timeline) ? raw.timeline.filter((m) => m && typeof m.month === "string") : [],
    recent: Array.isArray(raw.recent) ? raw.recent.filter((r) => r && typeof r.source === "string") : [],
    built_at: num(raw.built_at), age: num(raw.age), stale: raw.stale === true, refreshing: raw.refreshing === true,
    refresh_error: raw.refresh_error,
  };
}

const loadSummary = async ({ fresh }: { fresh: boolean }) => normalize(await getDashboard({ fresh }));

export default function DashboardPage() {
  const { data: summary, error, loading, refreshing, refresh } = useCached("dashboard", loadSummary, {
    revalidateOnDataChange: true,
    refreshInterval: 60_000,
    isStale: (d) => d.stale,
    workspaceOf: (d) => d.workspace || null,
  });
  const queue = useQueue();

  const serverResponded = error instanceof ApiError;
  const profileConflict = serverResponded && error.body.error === "embedding_profile_conflict";
  const updating = refreshing || !!summary?.stale;
  const empty = summary?.total === 0;

  return (
    <div className="pb-12">
      <PageHeader
        title="Dashboard"
        description="What your AI tools remember, and where it came from."
        actions={summary && <Freshness builtAt={summary.built_at || null} updating={updating} failed={!!error} onRefresh={() => void refresh()} />}
      />
      <div className="space-y-6 px-4 sm:px-8">
        {error && !summary && (
          <Card className="border-destructive">
            <CardContent className="pt-6">
              <p role="alert" className="text-destructive">
                {profileConflict ? "Memory index needs rebuilding" : serverResponded ? "Could not load dashboard" : "Failed to connect to API"}: {error.message}
              </p>
              <p className="mt-2 text-sm text-muted-foreground">
                {profileConflict
                  ? <>Use the workspace menu to create and switch to a new workspace, then reindex your sources. Your old workspace is preserved; its memories are not copied automatically.</>
                  : serverResponded
                  ? <>The API server responded with an error. Check its log with <code>imprint ui log</code>, then try again.</>
                  : <>Reconnecting automatically. Check the server with <code>imprint ui status</code>. If it is stopped, run <code>imprint ui start</code>.</>}
              </p>
              <Button className="mt-4" variant="outline" onClick={() => void refresh()}>
                Try again
              </Button>
            </CardContent>
          </Card>
        )}

        <IngestionProgress />

        {empty && (
          <Empty className="border bg-card">
            <EmptyHeader className="max-w-xl">
              <EmptyMedia variant="icon"><Database aria-hidden /></EmptyMedia>
              <EmptyTitle><h2 className="font-semibold">Build your first memory</h2></EmptyTitle>
              <EmptyDescription>1. Index a project folder or document below. 2. Check its progress in Queue. 3. Search what was indexed, or connect your AI client from Settings.</EmptyDescription>
              <EmptyDescription className="text-xs">Only selected sources are indexed. Secrets and files outside a selected folder are excluded.</EmptyDescription>
            </EmptyHeader>
          </Empty>
        )}
        {empty && <QuickIngest />}

        {loading && !error && <DashboardSkeleton />}

        {summary && !empty && (
          <>
            <section aria-label="Overview" className="grid gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
              <MemoryHero summary={summary} />
              <div className="grid grid-cols-2 gap-4">
                <StatTile label="Projects" value={summary.project_count} icon={FolderOpen} href="/projects/"
                  detail={summary.projects[0] ? `Largest: ${summary.projects[0][0]}` : undefined} />
                <StatTile label="Sources" value={summary.source_count} icon={FileCode} href="/sources/" detail="Files and conversations" />
                <StatTile label="Topics" value={summary.topic_count} icon={Tags} href="/topics/"
                  detail={summary.topics[0] ? `Most common: ${summary.topics[0][0]}` : undefined} />
                <StatTile label="Languages" value={summary.lang_count} icon={Code}
                  detail={summary.langs.slice(0, 3).map(([name]) => name).join(", ") || undefined} />
              </div>
            </section>

            <div className="grid items-start gap-4 lg:grid-cols-2">
              <Attention summary={summary} queue={queue} />
              <RecentSources recent={summary.recent} />
            </div>
          </>
        )}

        {!empty && !profileConflict && <QuickIngest />}

        {summary && !empty && (
          <section aria-labelledby="breakdown-heading" className="space-y-3">
            <h2 id="breakdown-heading" className="text-sm font-medium text-muted-foreground">What is in memory</h2>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              <Breakdown title="Projects" items={summary.projects} total={summary.total} count={summary.project_count}
                href="/projects/" itemHref={(name) => `/projects/?project=${encodeURIComponent(name)}`} />
              <Breakdown title="Topics" items={summary.topics} total={summary.total} count={summary.topic_count} href="/topics/" />
              <Breakdown title="Types" items={summary.types} total={summary.total} />
              <Breakdown title="Languages" items={summary.langs} total={summary.total} count={summary.lang_count} />
              <Breakdown title="Domains" items={summary.domains} total={summary.total} count={summary.domain_count} />
              <Breakdown title="Layers" items={summary.layers} total={summary.total} />
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
