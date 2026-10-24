"use client";

import { BookOpen, ChevronRight, Database, FileText, Globe, Library, Search, Sparkles, Wrench } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Spinner } from "@/components/ui/spinner";
import type { ToolStep } from "../_lib/turns";

type Describe = (args: Record<string, unknown>) => [running: string, done: string];

const quoted = (value: unknown) => (typeof value === "string" && value ? ` “${value}”` : "");
const named = (value: unknown) => (typeof value === "string" && value ? ` ${value}` : "");

const TOOLS: Record<string, { icon: LucideIcon; describe: Describe }> = {
  search: { icon: Search, describe: (a) => [`Searching memory for${quoted(a.query)}`, `Searched memory for${quoted(a.query)}`] },
  kg_query: { icon: Database, describe: () => ["Querying the knowledge graph", "Queried the knowledge graph"] },
  status: { icon: Library, describe: () => ["Checking memory status", "Checked memory status"] },
  wake_up: { icon: Sparkles, describe: () => ["Loading context", "Loaded context"] },
  list_sources: { icon: Library, describe: () => ["Listing sources", "Listed sources"] },
  file_summary: { icon: FileText, describe: (a) => [`Summarizing${named(a.source)}`, `Summarized${named(a.source)}`] },
  file_chunks: { icon: BookOpen, describe: (a) => [`Reading${named(a.source)}`, `Read${named(a.source)}`] },
  store: { icon: Database, describe: () => ["Saving a memory", "Saved a memory"] },
  kg_add: { icon: Database, describe: () => ["Recording a fact", "Recorded a fact"] },
  kg_invalidate: { icon: Database, describe: () => ["Ending a fact", "Ended a fact"] },
  ingest_url: { icon: Globe, describe: (a) => [`Fetching${named(a.url)}`, `Fetched${named(a.url)}`] },
};

/** One tool call inside a reply: a spinner while it runs, then a collapsed
 * row that expands to the result the model received. */
export function ToolStepRow({ step }: { step: ToolStep }) {
  const tool = TOOLS[step.name] ?? { icon: Wrench, describe: () => [`Running ${step.name}`, `Ran ${step.name}`] };
  const [running, done] = tool.describe(step.args);
  const Icon = tool.icon;

  if (step.result === undefined) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground" role="status">
        <Spinner className="size-3.5" aria-hidden />
        <span className="animate-pulse">{running}…</span>
      </div>
    );
  }
  return (
    <Collapsible>
      <CollapsibleTrigger className="group/tool flex max-w-full items-center gap-2 rounded-md text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50">
        <Icon className="size-3.5 shrink-0" aria-hidden />
        <span className="truncate">{done}</span>
        <ChevronRight className="size-3.5 shrink-0 transition-transform group-data-[panel-open]/tool:rotate-90" aria-hidden />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <pre className="mt-2 max-h-64 overflow-auto rounded-lg border border-border bg-muted/40 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words">
          {step.result || "(no output)"}
        </pre>
      </CollapsibleContent>
    </Collapsible>
  );
}
