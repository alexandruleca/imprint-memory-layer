"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { SearchIcon } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Kbd } from "@/components/ui/kbd";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { searchMemories, getMemory } from "@/lib/api";
import { CardGridSkeleton, Spinner } from "@/components/loaders";
import { ChunkDialog } from "@/components/chunk-dialog";
import type { MemoryNode } from "@/lib/types";

// "All types" is no type filter; Base UI keeps null for "nothing selected".
const TYPES = [
  { value: null, label: "All types" },
  ...["decision", "pattern", "finding", "bug", "architecture", "milestone"].map((value) => ({ value, label: value })),
];
const MODES = [
  { value: "hybrid", label: "Semantic + exact terms" },
  { value: "semantic", label: "Semantic only" },
];

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [project, setProject] = useState("");
  const [type, setType] = useState("");
  const [mode, setMode] = useState<"hybrid" | "semantic">("hybrid");
  const [results, setResults] = useState<MemoryNode[]>([]);
  const [submitted, setSubmitted] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<MemoryNode | null>(null);
  const [chunk, setChunk] = useState<MemoryNode | null>(null);
  const [opening, setOpening] = useState(false);
  const request = useRef<AbortController | null>(null);
  const detailRequest = useRef<AbortController | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key === "k") {
        event.preventDefault();
        input.current?.focus();
      }
    }
    window.addEventListener("keydown", shortcut);
    return () => { window.removeEventListener("keydown", shortcut); request.current?.abort(); detailRequest.current?.abort(); };
  }, []);

  async function doSearch(event: React.FormEvent) {
    event.preventDefault();
    const text = query.trim();
    if (!text) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    detailRequest.current?.abort();
    setLoading(true);
    setOpening(false);
    setError("");
    setSelected(null);
    setResults([]);
    setSubmitted(text);
    try {
      const data = await searchMemories(text, { project: project.trim(), type, mode, signal: controller.signal });
      if (controller.signal.aborted) return;
      setResults(data.nodes);
      setSelected(data.nodes[0] ?? null);
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Search failed. Please retry.");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  async function openFull() {
    if (!selected || opening) return;
    detailRequest.current?.abort();
    const controller = new AbortController();
    detailRequest.current = controller;
    setOpening(true);
    setError("");
    try {
      const memory = await getMemory(selected.id, controller.signal);
      if (!controller.signal.aborted) setChunk(memory);
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Could not open this memory.");
    } finally {
      if (!controller.signal.aborted) setOpening(false);
    }
  }

  return (
    <div className="p-4 sm:p-8 space-y-5 max-w-7xl mx-auto">
      <header>
        <h1 className="text-2xl font-bold">Search your memory</h1>
        <p className="text-sm text-muted-foreground mt-1">Find decisions, code and prior work. Use a phrase, symbol or filename. <Kbd>Ctrl / ⌘ K</Kbd></p>
      </header>
      <form onSubmit={doSearch} className="space-y-3" aria-label="Search memories">
        <div className="flex gap-2">
          <label className="sr-only" htmlFor="memory-query">Search query</label>
          <Input id="memory-query" ref={input} value={query} onChange={(event) => setQuery(event.target.value)} maxLength={4000}
            placeholder="e.g. why we chose Qdrant, or replace_source" autoFocus className="flex-1" />
          <Button type="submit" disabled={!query.trim()}>{loading && <Spinner className="w-4 h-4" />} {loading ? "Search again" : "Search"}</Button>
        </div>
        <div className="flex flex-wrap gap-3 text-xs">
          <label className="flex flex-col gap-1">Project
            <Input aria-label="Filter by project" value={project} onChange={(event) => setProject(event.target.value)} placeholder="All projects" className="h-8 w-44" />
          </label>
          <div className="flex flex-col gap-1">
            <span>Memory type</span>
            <Select items={TYPES} value={type || null} onValueChange={(value) => setType(value ?? "")}>
              <SelectTrigger aria-label="Memory type" className="w-44"><SelectValue /></SelectTrigger>
              <SelectContent>
                {TYPES.map((item) => <SelectItem key={item.label} value={item.value}>{item.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1">
            <span>Search method</span>
            <Select items={MODES} value={mode} onValueChange={(value) => setMode(value as typeof mode)}>
              <SelectTrigger aria-label="Search method" className="w-52"><SelectValue /></SelectTrigger>
              <SelectContent>
                {MODES.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        </div>
      </form>
      {error && <Alert variant="destructive" aria-label="Search error">{error} Submit the search to retry.</Alert>}
      <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
        {loading ? `Searching for “${submitted}”…` : submitted && !error ? `${results.length} ${results.length === 1 ? "result" : "results"} for “${submitted}”` : "Your memories stay in the selected workspace."}
      </p>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5" aria-busy={loading}>
        <section aria-label="Search results" className="space-y-2 lg:max-h-[65vh] overflow-auto">
          {loading && <CardGridSkeleton count={3} />}
          {!loading && !error && results.length === 0 && <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon"><SearchIcon aria-hidden /></EmptyMedia>
              <EmptyTitle>{submitted ? "No matching memories" : "What would you like to remember?"}</EmptyTitle>
              <EmptyDescription>{submitted ? "Try a shorter phrase, an exact identifier, or remove a filter. If this source is new, index it first." : "Search a decision, a feature, or a debugging problem. Add sources to make them searchable here."}</EmptyDescription>
            </EmptyHeader>
            <EmptyContent><Link className="text-sm text-primary underline" href="/commands/">Index a folder or document</Link></EmptyContent>
          </Empty>}
          {results.map((node, index) => <Button key={node.id} variant="outline" onClick={() => { detailRequest.current?.abort(); setOpening(false); setSelected(node); }}
            aria-pressed={selected?.id === node.id} className={`block h-auto w-full whitespace-normal p-4 text-left font-normal ${selected?.id === node.id ? "border-primary bg-primary/5 hover:bg-primary/5" : "hover:bg-muted/40"}`}>
            <div className="flex items-start justify-between gap-2"><h2 className="font-medium text-sm break-words">{index + 1}. {node.label || node.source || node.content.slice(0, 90)}</h2><Badge variant="outline">{node.match_type || "semantic"}</Badge></div>
            <p className="text-xs text-muted-foreground mt-2 break-all">{node.source || "Manual memory"}</p>
            <div className="flex flex-wrap gap-1 mt-2"><Badge variant="secondary">{node.project || "No project"}</Badge><Badge variant="outline">{node.type || "memory"}</Badge></div>
            <p className="text-sm text-muted-foreground mt-2 line-clamp-3">{node.content}</p>
          </Button>)}
        </section>
        <section aria-label="Memory details" className="min-w-0">
          {selected && <Card><CardContent className="p-5 space-y-4">
            <h2 className="font-semibold break-words">{selected.label || "Memory details"}</h2>
            <p className="text-xs text-muted-foreground break-all">{selected.source || "Manual memory"}</p>
            <dl className="text-xs text-muted-foreground space-y-1">
              {selected.source_mtime ? <div><dt className="inline">Source modified: </dt><dd className="inline">{new Date(selected.source_mtime * 1000).toLocaleString()}</dd></div> : null}
              {selected.source_hash && <div><dt className="inline">Content fingerprint: </dt><dd className="inline font-mono">{selected.source_hash.slice(0, 12)}</dd></div>}
              {selected.similarity !== undefined && selected.match_type !== "lexical" && <div><dt className="inline">Semantic similarity: </dt><dd className="inline">{selected.similarity.toFixed(3)} · ranking signal</dd></div>}
            </dl>
            <p className="text-xs text-muted-foreground">Indexed context may be outdated. Verify current source files before making changes.</p>
            <pre className="text-sm whitespace-pre-wrap break-words font-sans bg-muted/50 p-4 rounded-md max-h-80 overflow-auto">{selected.content}</pre>
            <Button variant="outline" size="sm" onClick={openFull} aria-busy={opening} aria-disabled={opening}>{opening && <Spinner />}{opening ? "Opening…" : "Open full memory"}</Button>
          </CardContent></Card>}
        </section>
      </div>
      <ChunkDialog chunk={chunk} onClose={() => setChunk(null)} />
    </div>
  );
}
