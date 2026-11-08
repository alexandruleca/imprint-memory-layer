"use client";

import { useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/loaders";

/** The end of a paged list: loads the next page by itself when it scrolls
 * into view, and keeps a button for keyboards, screen readers and the case
 * where scrolling never reaches it. After a failed load it waits for the
 * button, so a broken connection does not retry in a loop. */
export function LoadMore({ hasMore, loading, failed, onLoad, label, status }: {
  hasMore: boolean;
  loading: boolean;
  failed?: boolean;
  onLoad: () => void;
  label: string;
  /** What is loaded so far, read out politely as it changes. */
  status: string;
}) {
  const sentinel = useRef<HTMLDivElement>(null);
  const load = useRef(onLoad);
  useEffect(() => { load.current = onLoad; });
  const auto = hasMore && !loading && !failed;

  useEffect(() => {
    const el = sentinel.current;
    if (!el || !auto || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) load.current();
    }, { rootMargin: "0px 0px 400px 0px" });
    observer.observe(el);
    return () => observer.disconnect();
  }, [auto]);

  return (
    <div ref={sentinel} className="flex flex-col items-center gap-2 py-3 text-xs text-muted-foreground">
      <p aria-live="polite" className="tabular-nums">{status}</p>
      {hasMore && (
        <Button variant="outline" size="sm" onClick={onLoad} disabled={loading}>
          {loading && <Spinner />}{loading ? "Loading…" : label}
        </Button>
      )}
    </div>
  );
}
