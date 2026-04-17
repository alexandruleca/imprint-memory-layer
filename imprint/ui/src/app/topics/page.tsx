"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { getTopics, getTopicDetail } from "@/lib/api";
import { ListSkeleton } from "@/components/loaders";
import { Skeleton } from "@/components/ui/skeleton";
import { MigrateDialog } from "@/components/migrate-dialog";
import type { Topic, TopicOverviewData, TopicDetailData, MemoryNode } from "@/lib/types";

export default function TopicsPage() {
  const [data, setData] = useState<TopicOverviewData | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<TopicDetailData | null>(null);
  const [migrateOpen, setMigrateOpen] = useState(false);

  useEffect(() => {
    getTopics().then(setData).catch(console.error);
  }, []);

  async function selectTopic(name: string) {
    setSelected(name);
    const d = await getTopicDetail(name);
    setDetail(d);
  }

  if (!data) return (
    <div className="p-8 space-y-4">
      <Skeleton className="h-8 w-40" />
      <div className="grid grid-cols-3 gap-4" style={{ minHeight: 500 }}>
        <ListSkeleton count={12} />
        <div className="col-span-2 flex items-center justify-center">
          <Skeleton className="h-64 w-full rounded-xl" />
        </div>
      </div>
    </div>
  );

  if (!data.topics.length) {
    return (
      <div className="p-8 space-y-4">
        <h2 className="text-2xl font-bold">Topics</h2>
        <Card>
          <CardContent className="pt-6">
            <p className="text-muted-foreground">No topics found. Run <code>imprint retag</code> to generate topic tags for existing memories.</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-8 space-y-4">
      <h2 className="text-2xl font-bold">Topics ({data.topics.length})</h2>

      <div className="grid grid-cols-3 gap-4" style={{ minHeight: 500 }}>
        <ScrollArea className="h-[600px] col-span-1">
          <div className="space-y-1 pr-4">
            {data.topics.map((t) => (
              <div
                key={t.id}
                className={`p-2 rounded cursor-pointer text-sm flex justify-between items-center ${
                  selected === t.name ? "bg-primary/20 border border-primary" : "hover:bg-muted"
                }`}
                onClick={() => selectTopic(t.name)}
              >
                <span className="font-medium">{t.name}</span>
                <Badge variant="secondary">{t.count.toLocaleString()}</Badge>
              </div>
            ))}
          </div>
        </ScrollArea>

        <div className="col-span-2">
          {detail ? (
            <div className="space-y-4">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between">
                  <CardTitle>{detail.topic} ({detail.total} memories)</CardTitle>
                  <Button size="sm" variant="outline" onClick={() => setMigrateOpen(true)}>
                    Migrate…
                  </Button>
                </CardHeader>
                <CardContent>
                  <div className="flex gap-2 flex-wrap mb-4">
                    {detail.projects.map(([proj, count]) => (
                      <Badge key={proj} variant="outline">{proj} ({count})</Badge>
                    ))}
                  </div>
                </CardContent>
              </Card>

              <ScrollArea className="h-[450px]">
                <div className="space-y-2 pr-4">
                  {detail.nodes.map((node: MemoryNode) => (
                    <Card key={node.id}>
                      <CardContent className="p-3">
                        <div className="flex gap-1 mb-1 flex-wrap">
                          <Badge variant="outline" className="text-xs">{node.project}</Badge>
                          <Badge variant="secondary" className="text-xs">{node.type}</Badge>
                          {node.tags?.lang && <Badge className="text-xs">{node.tags.lang}</Badge>}
                        </div>
                        <p className="text-xs text-muted-foreground">{node.source}</p>
                        <p className="text-sm mt-1 line-clamp-3">{node.content}</p>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              </ScrollArea>
            </div>
          ) : (
            <div className="flex items-center justify-center h-full text-muted-foreground">
              Select a topic to view details
            </div>
          )}
        </div>
      </div>

      {detail && (
        <MigrateDialog
          open={migrateOpen}
          preset={{ mode: "topic", value: detail.topic }}
          onClose={() => setMigrateOpen(false)}
          onDone={() => setMigrateOpen(false)}
        />
      )}
    </div>
  );
}
