"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { ApiError, getStats, getOverview } from "@/lib/api";
import { DashboardSkeleton } from "@/components/loaders";
import { IngestionProgress } from "@/components/ingestion-progress";
import { QuickIngest } from "@/components/quick-ingest";
import { PageHeader } from "@/components/split-page";
import { Database, FolderOpen, Code, Tags } from "lucide-react";
import type { StatsData, OverviewData } from "@/lib/types";

function AnimatedNumber({ value }: { value: number }) {
  const [display, setDisplay] = useState(0);

  useEffect(() => {
    if (value === 0) return;
    const duration = 800;
    const start = performance.now();
    let cancelled = false;

    function tick(now: number) {
      if (cancelled) return;
      const elapsed = now - start;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplay(Math.round(value * eased));
      if (progress < 1) requestAnimationFrame(tick);
    }

    requestAnimationFrame(tick);
    return () => { cancelled = true; };
  }, [value]);

  return <p className="text-3xl font-bold tabular-nums">{(value === 0 ? 0 : display).toLocaleString()}</p>;
}

function StatCard({
  label, value, icon: Icon,
}: {
  label: string; value: number; icon: React.ElementType;
}) {
  return (
    <Card>
      <CardHeader className="pb-2 pl-5">
        <CardTitle className="text-xs text-muted-foreground flex items-center gap-1.5">
          <Icon className="w-3.5 h-3.5" />
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent className="pl-5">
        <AnimatedNumber value={value} />
      </CardContent>
    </Card>
  );
}

function BarList({ items }: { items: [string, number][] }) {
  if (!items.length) return <p className="text-xs text-muted-foreground">None</p>;
  const max = items[0][1];
  return (
    <div className="space-y-1.5">
      {items.slice(0, 8).map(([label, count]) => (
        <div key={label} className="flex items-center gap-2 text-xs group/row hover:bg-muted/30 rounded px-1 py-0.5 transition-colors">
          <span className="w-24 truncate text-muted-foreground" title={label}>{label}</span>
          <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
            <div
              className="h-full rounded-full bg-foreground/75 transition-all duration-500"
              style={{ width: `${(count / max) * 100}%` }}
            />
          </div>
          <span className="w-14 text-right tabular-nums font-medium">{count.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

export default function DashboardPage() {
  const [stats, setStats] = useState<StatsData | null>(null);
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [error, setError] = useState<{ message: string; serverResponded: boolean; profileConflict: boolean } | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      try {
        const [s, o] = await Promise.all([getStats(), getOverview()]);
        if (cancelled) return;
        setStats(s);
        setOverview(o);
        setError(null);
      } catch (cause) {
        if (cancelled) return;
        const serverResponded = cause instanceof ApiError;
        const profileConflict = serverResponded && cause.body.error === "embedding_profile_conflict";
        setError({ message: cause instanceof Error ? cause.message : "Unknown error", serverResponded, profileConflict });
        // Starting the API after a failed load must recover the open dashboard.
        if (!serverResponded) timer = setTimeout(load, 3000);
      }
    }
    void load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [retry]);

  if (error) {
    return (
      <div className="p-8">
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p role="alert" className="text-destructive">
              {error.profileConflict ? "Memory index needs rebuilding" : error.serverResponded ? "Could not load dashboard" : "Failed to connect to API"}: {error.message}
            </p>
            <p className="text-sm text-muted-foreground mt-2">
              {error.profileConflict
                ? <>Use the workspace menu to create and switch to a new workspace, then reindex your sources. Your old workspace is preserved; its memories are not copied automatically.</>
                : error.serverResponded
                ? <>The API server responded with an error. Check its log with <code>imprint ui log</code>, then try again.</>
                : <>Reconnecting automatically. Check the server with <code>imprint ui status</code>. If it is stopped, run <code>imprint ui start</code>.</>}
            </p>
            <Button className="mt-4" variant="outline" onClick={() => { setError(null); setRetry(value => value + 1); }}>
              Try again
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!stats || !overview) {
    return <DashboardSkeleton />;
  }

  return (
    <div className="pb-8">
      <PageHeader title="Dashboard" description="What your AI tools remember, and where it came from." />
      <div className="space-y-6 px-4 sm:px-8">
        {stats.total === 0 && <Empty className="border bg-card">
          <EmptyHeader className="max-w-xl">
            <EmptyMedia variant="icon"><Database aria-hidden /></EmptyMedia>
            <EmptyTitle><h2 className="font-semibold">Build your first memory</h2></EmptyTitle>
            <EmptyDescription>1. Index a project folder or document below. 2. Check its progress in Queue. 3. Search what was indexed, or connect your AI client from Settings.</EmptyDescription>
            <EmptyDescription className="text-xs">Only selected sources are indexed. Secrets and files outside a selected folder are excluded.</EmptyDescription>
          </EmptyHeader>
        </Empty>}
        <QuickIngest />
        <IngestionProgress />

        <div className="grid grid-cols-2 xl:grid-cols-4 gap-5">
          <StatCard label="Total Memories" value={stats.total} icon={Database} />
          <StatCard label="Projects" value={overview.projects.length} icon={FolderOpen} />
          <StatCard label="Languages" value={stats.langs?.length || 0} icon={Code} />
          <StatCard label="Topics" value={stats.topics?.length || 0} icon={Tags} />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card>
            <CardHeader><CardTitle className="text-sm font-medium">Projects</CardTitle></CardHeader>
            <CardContent>
              <BarList items={stats.projects || []} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-sm font-medium">Languages</CardTitle></CardHeader>
            <CardContent>
              <BarList items={stats.langs || []} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-sm font-medium">Domains</CardTitle></CardHeader>
            <CardContent>
              <BarList items={stats.domains || []} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-sm font-medium">Types</CardTitle></CardHeader>
            <CardContent>
              <BarList items={stats.types || []} />
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader><CardTitle className="text-sm font-medium">Top Projects</CardTitle></CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {overview.projects.slice(0, 20).map((p) => (
              <Badge
                key={p.id}
                variant="secondary"
                style={{ borderColor: p.color, borderWidth: 1 }}
              >
                {p.name} ({p.count.toLocaleString()})
              </Badge>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
