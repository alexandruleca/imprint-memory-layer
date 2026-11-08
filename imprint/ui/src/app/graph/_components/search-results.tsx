"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { ExternalLink, Search as SearchIcon } from "lucide-react";
import { Spinner } from "@/components/loaders";
import { buttonVariants } from "@/components/ui/button";
import type { GraphNode } from "@/lib/types";

/** A graph search's hits in rank order. A click focuses a hit's node; the
 * selected node's hit is marked and scrolled into view. */
export function SearchResults({
  query,
  hits,
  loading,
  selectedId,
  searchPageHref,
  onPick,
  onOpen,
}: {
  query: string;
  hits: GraphNode[];
  loading: boolean;
  selectedId: string | null;
  searchPageHref: string;
  onPick: (id: string) => void;
  onOpen: (node: GraphNode) => void;
}) {
  const list = useRef<HTMLOListElement>(null);
  useEffect(() => {
    if (!selectedId) return;
    const row = list.current?.querySelector<HTMLElement>(`[data-hit="${CSS.escape(selectedId)}"]`);
    row?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selectedId]);

  return (
    <section aria-label="Search results" className="flex max-h-full flex-col overflow-hidden rounded-xl border border-border bg-card/95 shadow-xl backdrop-blur">
      <header className="flex items-center gap-2 border-b border-border px-3 py-2.5">
        <SearchIcon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <p className="min-w-0 flex-1 truncate text-sm" role="status">
          {loading ? <span className="inline-flex items-center gap-1.5 text-muted-foreground"><Spinner className="size-3" /> Searching…</span>
            : <>{hits.length} {hits.length === 1 ? "result" : "results"} for <span className="font-medium">“{query}”</span></>}
        </p>
        <Link href={searchPageHref} aria-label="Open on the Search page" title="Open on the Search page" className={buttonVariants({ variant: "ghost", size: "icon-xs", className: "text-muted-foreground" })}>
          <ExternalLink aria-hidden />
        </Link>
      </header>
      {!loading && hits.length === 0 && (
        <p className="p-4 text-sm text-muted-foreground">No matching memories. Try a shorter phrase, an exact identifier, or another workspace.</p>
      )}
      <ol ref={list} className="min-h-0 flex-1 space-y-1 overflow-y-auto p-1.5">
        {hits.map((hit) => {
          const current = hit.id === selectedId;
          const file = hit.fullPath?.split("/").pop();
          return (
            <li key={hit.id} data-hit={hit.id}>
              <button
                type="button"
                aria-current={current ? "true" : undefined}
                onClick={() => onPick(hit.id)}
                onDoubleClick={() => onOpen(hit)}
                className={`group flex w-full gap-2.5 rounded-lg px-2 py-2 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring ${
                  current ? "bg-accent" : "hover:bg-muted/60"
                }`}
              >
                <span className={`mt-0.5 grid size-5 shrink-0 place-items-center rounded-full text-[10px] font-semibold tabular-nums ${
                  current ? "bg-foreground text-background" : "bg-muted text-muted-foreground"
                }`}>{hit.rank}</span>
                <span className="min-w-0 flex-1">
                  <span className="line-clamp-1 break-words text-sm font-medium">{hit.label}</span>
                  {hit.content && <span className="mt-0.5 line-clamp-2 break-words text-xs text-muted-foreground">{hit.content}</span>}
                  <span className="mt-1 block truncate text-[11px] text-muted-foreground/80">
                    {[hit.project || "No project", file, hit.chunk_index !== undefined ? `#${hit.chunk_index}` : ""].filter(Boolean).join(" · ")}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
      {hits.length > 0 && <p className="border-t border-border px-3 py-1.5 text-[11px] text-muted-foreground">Click focuses a hit; double-click reads it.</p>}
    </section>
  );
}
