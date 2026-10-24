import { useEffect, useLayoutEffect, useRef } from "react";

/** Keeps a log panel on its newest output while the reader is at its bottom.
 * Attach the returned ref to the panel's scroller (ScrollArea's viewportRef).
 * Only that scroller moves, never the page around it. Scrolling the log up
 * stops the following; scrolling back to the bottom resumes it. */
export function useFollowOutput(output: unknown) {
  const scroller = useRef<HTMLDivElement>(null);
  const following = useRef(true);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const track = () => { following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 8; };
    el.addEventListener("scroll", track, { passive: true });
    return () => el.removeEventListener("scroll", track);
  }, []);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    // Output that fits, such as a cleared log, has no position to keep.
    if (el.scrollHeight <= el.clientHeight) following.current = true;
    if (following.current) el.scrollTop = el.scrollHeight;
  }, [output]);

  return scroller;
}
