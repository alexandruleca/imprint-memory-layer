"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { searchMemories, getSourceLineage } from "@/lib/api";
import { CardGridSkeleton, Spinner } from "@/components/loaders";
import type { MemoryNode, SourceLineage } from "@/lib/types";

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<MemoryNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<MemoryNode | null>(null);
  const [lineage, setLineage] = useState<SourceLineage | null>(null);

  async function doSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    try {
      const data = await searchMemories(query) as { nodes: MemoryNode[] };
      setResults(data.nodes || []);
      setSelected(null);
      setLineage(null);
    } finally {
      setLoading(false);
    }
  }

  async function selectNode(node: MemoryNode) {
    setSelected(node);
    if (node.source) {
      const lin = await getSourceLineage(node.source);
      setLineage(lin);
    }
  }

  return (
    <div className="p-8 space-y-4">
      <h2 className="text-2xl font-bold">Search</h2>
      <form onSubmit={doSearch} className="flex gap-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Semantic search across all memories..."
          className="flex-1"
        />
        <button
          type="submit"
          className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm flex items-center gap-2"
          disabled={loading}
        >
          {loading && <Spinner className="w-3.5 h-3.5" />}
          {loading ? "Searching..." : "Search"}
        </button>
      </form>

      <div className="grid grid-cols-2 gap-4" style={{ minHeight: 400 }}>
        <ScrollArea className="h-[600px]">
          <div className="space-y-2 pr-4">
            {results.map((node) => (
              <Card
                key={node.id}
                className={`cursor-pointer transition-colors ${selected?.id === node.id ? "border-primary" : "hover:border-muted-foreground/30"}`}
                onClick={() => selectNode(node)}
              >
                <CardContent className="p-3">
                  <p className="text-sm font-medium truncate">{node.label}</p>
                  <div className="flex gap-1 mt-1 flex-wrap">
                    <Badge variant="outline" className="text-xs">{node.project}</Badge>
                    <Badge variant="secondary" className="text-xs">{node.type}</Badge>
                    {node.tags?.topics?.map((t: string) => (
                      <Badge key={t} className="text-xs">{t}</Badge>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{node.content}</p>
                </CardContent>
              </Card>
            ))}
            {loading && <CardGridSkeleton count={4} />}
            {results.length === 0 && !loading && (
              <p className="text-sm text-muted-foreground">Enter a query to search.</p>
            )}
          </div>
        </ScrollArea>

        <div className="space-y-4">
          {selected && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">{selected.label}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <div className="flex gap-1 flex-wrap">
                  <Badge variant="outline">{selected.project}</Badge>
                  <Badge variant="secondary">{selected.type}</Badge>
                  {selected.tags?.lang && <Badge>{selected.tags.lang}</Badge>}
                </div>
                <p className="text-xs text-muted-foreground">Source: {selected.source}</p>
                <pre className="text-xs bg-muted p-3 rounded overflow-auto max-h-60 whitespace-pre-wrap">
                  {selected.content}
                </pre>
              </CardContent>
            </Card>
          )}

          {lineage && lineage.total > 1 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">
                  Source: {lineage.source} ({lineage.total} chunks)
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-1">
                  {lineage.chunks.map((chunk, i) => (
                    <div
                      key={chunk.id}
                      className={`text-xs p-2 rounded cursor-pointer ${chunk.id === selected?.id ? "bg-primary/20" : "bg-muted hover:bg-muted/80"}`}
                      onClick={() => setSelected(chunk)}
                    >
                      <span className="text-muted-foreground">Part {i + 1}/{lineage.total}</span>{" "}
                      <span className="truncate">{chunk.label}</span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
