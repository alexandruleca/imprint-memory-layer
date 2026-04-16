"use client";

import { useEffect, useRef, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { getOverview, getTopics } from "@/lib/api";
import { GraphSkeleton } from "@/components/loaders";
import type { OverviewData, TopicOverviewData, Project, Topic } from "@/lib/types";

export default function GraphPage() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<"projects" | "topics">("projects");
  const [projectData, setProjectData] = useState<OverviewData | null>(null);
  const [topicData, setTopicData] = useState<TopicOverviewData | null>(null);
  const [selected, setSelected] = useState<Project | Topic | null>(null);
  const graphRef = useRef<unknown>(null);

  useEffect(() => {
    getOverview().then(setProjectData);
    getTopics().then(setTopicData);
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;
    const data = mode === "projects" ? projectData : topicData;
    if (!data) return;

    let cancelled = false;

    import("@antv/g6").then(({ Graph }) => {
      if (cancelled) return;

      // Destroy previous graph
      if (graphRef.current && typeof (graphRef.current as { destroy?: () => void }).destroy === "function") {
        (graphRef.current as { destroy: () => void }).destroy();
      }

      const items = mode === "projects"
        ? (data as OverviewData).projects
        : (data as TopicOverviewData).topics;

      if (!items.length) return;

      const maxCount = Math.max(...items.map((i) => i.count));
      const clamp = (v: number, min: number, max: number) => Math.max(min, Math.min(max, v));

      const cx = containerRef.current!.offsetWidth / 2;
      const cy = containerRef.current!.offsetHeight / 2;
      const spreadRadius = Math.min(cx, cy) * 0.6;

      const nodes = items.map((item, i) => {
        const angle = (2 * Math.PI * i) / items.length;
        return {
          id: item.id,
          data: { ...item },
          style: {
            x: cx + spreadRadius * Math.cos(angle),
            y: cy + spreadRadius * Math.sin(angle),
            size: clamp(Math.sqrt(item.count / maxCount) * 80, 20, 100),
            fill: item.color + "44",
            stroke: item.color,
            lineWidth: 2,
            labelText: `${item.name}\n(${item.count.toLocaleString()})`,
            labelFontSize: 10,
            labelFill: "#e2e8f0",
            labelPlacement: "center" as const,
            labelWordWrap: true,
            labelMaxLines: 2,
            cursor: "pointer" as const,
          },
        };
      });

      const graph = new Graph({
        container: containerRef.current!,
        width: containerRef.current!.offsetWidth,
        height: containerRef.current!.offsetHeight,
        data: { nodes, edges: [] },
        layout: {
          type: "force",
          preventOverlap: true,
          nodeSpacing: 80,
          linkDistance: 350,
          nodeStrength: -3000,
          animated: false,
          maxIteration: 500,
        },
        node: { style: {} },
        behaviors: ["drag-canvas", "zoom-canvas", "drag-element"],
      });

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      graph.on("node:click", (evt: any) => {
        const id = evt?.target?.id;
        if (!id) return;
        const item = items.find((i) => i.id === id);
        if (item) setSelected(item);
      });

      graph.render();
      graphRef.current = graph;
    });

    return () => { cancelled = true; };
  }, [mode, projectData, topicData]);

  return (
    <div className="flex flex-col h-screen">
      <div className="p-4 border-b border-border flex items-center gap-4">
        <h2 className="text-lg font-bold">Graph</h2>
        <div className="flex gap-1">
          <button
            className={`px-3 py-1 rounded text-sm ${mode === "projects" ? "bg-primary text-primary-foreground" : "bg-muted"}`}
            onClick={() => { setMode("projects"); setSelected(null); }}
          >
            Projects
          </button>
          <button
            className={`px-3 py-1 rounded text-sm ${mode === "topics" ? "bg-primary text-primary-foreground" : "bg-muted"}`}
            onClick={() => { setMode("topics"); setSelected(null); }}
          >
            Topics
          </button>
        </div>
      </div>

      <div className="flex-1 relative">
        {!projectData && !topicData ? (
          <GraphSkeleton />
        ) : (
          <div ref={containerRef} className="absolute inset-0 bg-[#0a0a1a]" />
        )}

        {selected && (
          <div className="absolute top-4 right-4 w-72 bg-background/95 backdrop-blur border border-border rounded-lg p-4 space-y-3 shadow-lg z-10">
            <div className="flex items-center justify-between">
              <h3 className="font-medium">{selected.name}</h3>
              <button
                onClick={() => setSelected(null)}
                className="text-muted-foreground hover:text-foreground text-lg leading-none px-1"
              >
                &times;
              </button>
            </div>
            <p className="text-sm text-muted-foreground">{selected.count.toLocaleString()} memories</p>
            <div className="flex gap-1 flex-wrap">
              {"topDomains" in selected && (selected as Project).topDomains?.map((d) => (
                <Badge key={d} variant="secondary" className="text-xs">{d}</Badge>
              ))}
              {"topProjects" in selected && (selected as Topic).topProjects?.map((p) => (
                <Badge key={p} variant="outline" className="text-xs">{p}</Badge>
              ))}
            </div>
            {"topLangs" in selected && (
              <div className="flex gap-1 flex-wrap">
                {selected.topLangs?.map((l) => (
                  <Badge key={l} className="text-xs">{l}</Badge>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
