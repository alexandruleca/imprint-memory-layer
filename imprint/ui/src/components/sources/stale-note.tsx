"use client";

import { RefreshCw } from "lucide-react";
import { Spinner } from "@/components/loaders";
import { Button } from "@/components/ui/button";

/** A note that the data changed since the list was loaded; the list itself
 * stays put until asked, so rows never move under the cursor. */
export function StaleNote({ busy, onRefresh, what = "Sources" }: { busy: boolean; onRefresh: () => void; what?: string }) {
  return (
    <div role="status" className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/50 px-3 py-1.5 text-sm">
      <span className="min-w-0 flex-1">{what} changed since this list was loaded.</span>
      <Button size="sm" variant="outline" disabled={busy} onClick={onRefresh}>{busy ? <Spinner /> : <RefreshCw />} Refresh</Button>
    </div>
  );
}
