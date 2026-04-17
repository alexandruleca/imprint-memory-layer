"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { getOverview, getProjectDetail } from "@/lib/api";
import { ListSkeleton } from "@/components/loaders";
import { Skeleton } from "@/components/ui/skeleton";
import { MigrateDialog } from "@/components/migrate-dialog";
import type { OverviewData, Project } from "@/lib/types";

interface ProjectDetail {
  project: string;
  count: number;
  color: string;
  types: { name: string; count: number }[];
  domains: { name: string; count: number }[];
  langs: { name: string; count: number }[];
  sampleNodes: {
    id: string;
    label: string;
    type: string;
    source: string;
    tags: Record<string, unknown>;
    content: string;
  }[];
}

export default function ProjectsPage() {
  const [data, setData] = useState<OverviewData | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [migrateOpen, setMigrateOpen] = useState(false);

  useEffect(() => {
    getOverview().then(setData).catch(console.error);
  }, []);

  async function selectProject(name: string) {
    setSelected(name);
    const d = await getProjectDetail(name) as ProjectDetail;
    setDetail(d);
  }

  if (!data) return (
    <div className="p-8 space-y-4">
      <Skeleton className="h-8 w-48" />
      <div className="grid grid-cols-3 gap-4" style={{ minHeight: 500 }}>
        <ListSkeleton count={12} />
        <div className="col-span-2 flex items-center justify-center text-muted-foreground">
          <Skeleton className="h-64 w-full rounded-xl" />
        </div>
      </div>
    </div>
  );

  return (
    <div className="p-8 space-y-4">
      <h2 className="text-2xl font-bold">Projects ({data.projects.length})</h2>

      <div className="grid grid-cols-3 gap-4" style={{ minHeight: 500 }}>
        <ScrollArea className="h-[600px] col-span-1">
          <div className="space-y-1 pr-4">
            {data.projects.map((p: Project) => (
              <div
                key={p.id}
                className={`p-2 rounded cursor-pointer text-sm flex justify-between items-center ${
                  selected === p.name ? "bg-primary/20 border border-primary" : "hover:bg-muted"
                }`}
                onClick={() => selectProject(p.name)}
              >
                <div className="flex items-center gap-2">
                  <div className="w-3 h-3 rounded-full" style={{ backgroundColor: p.color }} />
                  <span className="font-medium">{p.name}</span>
                </div>
                <Badge variant="secondary">{p.count.toLocaleString()}</Badge>
              </div>
            ))}
          </div>
        </ScrollArea>

        <div className="col-span-2">
          {detail ? (
            <div className="space-y-4">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between">
                  <CardTitle>{detail.project} ({detail.count.toLocaleString()})</CardTitle>
                  <Button size="sm" variant="outline" onClick={() => setMigrateOpen(true)}>
                    Migrate…
                  </Button>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-3 gap-4">
                    <div>
                      <p className="text-xs text-muted-foreground mb-1">Types</p>
                      <div className="flex gap-1 flex-wrap">
                        {detail.types.map((t) => (
                          <Badge key={t.name} variant="outline">{t.name} ({t.count})</Badge>
                        ))}
                      </div>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground mb-1">Domains</p>
                      <div className="flex gap-1 flex-wrap">
                        {detail.domains.map((d) => (
                          <Badge key={d.name}>{d.name} ({d.count})</Badge>
                        ))}
                      </div>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground mb-1">Languages</p>
                      <div className="flex gap-1 flex-wrap">
                        {detail.langs.map((l) => (
                          <Badge key={l.name} variant="secondary">{l.name} ({l.count})</Badge>
                        ))}
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>

              <ScrollArea className="h-[400px]">
                <div className="space-y-2 pr-4">
                  {detail.sampleNodes.map((node) => (
                    <Card key={node.id}>
                      <CardContent className="p-3">
                        <p className="text-sm font-medium truncate">{node.label}</p>
                        <div className="flex gap-1 mt-1 flex-wrap">
                          <Badge variant="secondary" className="text-xs">{node.type}</Badge>
                          <Badge variant="outline" className="text-xs">{node.source}</Badge>
                        </div>
                        <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{node.content.slice(0, 200)}</p>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              </ScrollArea>
            </div>
          ) : (
            <div className="flex items-center justify-center h-full text-muted-foreground">
              Select a project to view details
            </div>
          )}
        </div>
      </div>

      {detail && (
        <MigrateDialog
          open={migrateOpen}
          preset={{ mode: "project", value: detail.project }}
          onClose={() => setMigrateOpen(false)}
          onDone={() => setMigrateOpen(false)}
        />
      )}
    </div>
  );
}
