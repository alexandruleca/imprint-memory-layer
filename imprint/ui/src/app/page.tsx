"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { getStats, getOverview } from "@/lib/api";
import { DashboardSkeleton } from "@/components/loaders";
import { IngestionProgress } from "@/components/ingestion-progress";
import { QuickIngest } from "@/components/quick-ingest";
import { Database, FolderOpen, Code, Tags } from "lucide-react";
import type { StatsData, OverviewData } from "@/lib/types";

function AnimatedNumber({ value }: { value: number }) {
  const [display, setDisplay] = useState(0);

  useEffect(() => {
    if (value === 0) { setDisplay(0); return; }
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

  return <p className="text-3xl font-bold tabular-nums">{display.toLocaleString()}</p>;
}

function StatCard({
  label, value, icon: Icon, accentColor,
}: {
  label: string; value: number; icon: React.ElementType; accentColor: string;
}) {
  return (
    <Card className="relative overflow-hidden">
      <div className="absolute left-0 top-0 bottom-0 w-1" style={{ backgroundColor: accentColor }} />
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

function BarList({ items, color }: { items: [string, number][]; color?: string }) {
  if (!items.length) return <p className="text-xs text-muted-foreground">None</p>;
  const max = items[0][1];
  return (
    <div className="space-y-1.5">
      {items.slice(0, 8).map(([label, count]) => (
        <div key={label} className="flex items-center gap-2 text-xs group/row hover:bg-muted/30 rounded px-1 py-0.5 transition-colors">
          <span className="w-24 truncate text-muted-foreground" title={label}>{label}</span>
          <div className="flex-1 h-4 bg-muted rounded overflow-hidden">
            <div
              className="h-full rounded transition-all duration-500"
              style={{
                width: `${(count / max) * 100}%`,
                background: `linear-gradient(90deg, ${color || "hsl(var(--primary))"}, ${color || "hsl(var(--primary))"}cc)`,
              }}
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
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getStats(), getOverview()])
      .then(([s, o]) => { setStats(s); setOverview(o); })
      .catch((e) => setError(e.message));
  }, []);

  if (error) {
    return (
      <div className="p-8">
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-destructive">Failed to connect to API: {error}</p>
            <p className="text-sm text-muted-foreground mt-2">
              Make sure the API server is running: <code>python -m imprint.api</code>
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!stats || !overview) {
    return <DashboardSkeleton />;
  }

  return (
    <div className="p-8 space-y-8">
      <div>
        <h2 className="text-3xl font-bold tracking-tight">Dashboard</h2>
        <p className="text-sm text-muted-foreground mt-1">Overview of your AI memory store</p>
      </div>

      <QuickIngest />
      <IngestionProgress />

      <div className="grid grid-cols-4 gap-5">
        <StatCard label="Total Memories" value={stats.total} icon={Database} accentColor="#60a5fa" />
        <StatCard label="Projects" value={overview.projects.length} icon={FolderOpen} accentColor="#4ecdc4" />
        <StatCard label="Languages" value={stats.langs?.length || 0} icon={Code} accentColor="#a78bfa" />
        <StatCard label="Topics" value={stats.topics?.length || 0} icon={Tags} accentColor="#f472b6" />
      </div>

      <div className="grid grid-cols-2 gap-6">
        <Card>
          <CardHeader><CardTitle className="text-sm font-medium">Projects</CardTitle></CardHeader>
          <CardContent>
            <BarList items={stats.projects || []} color="#60a5fa" />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm font-medium">Languages</CardTitle></CardHeader>
          <CardContent>
            <BarList items={stats.langs || []} color="#4ecdc4" />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm font-medium">Domains</CardTitle></CardHeader>
          <CardContent>
            <BarList items={stats.domains || []} color="#a78bfa" />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm font-medium">Types</CardTitle></CardHeader>
          <CardContent>
            <BarList items={stats.types || []} color="#f472b6" />
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
  );
}
