import { cn } from "@/lib/utils";

/** The dashboard's subtle "Updating…" note: shown while a page that already
 * shows an answer asks for a newer one (useCached's `refreshing`). The live
 * region stays mounted so screen readers hear the change. */
export function Updating({ show, className, label = "Updating…" }: { show: boolean; className?: string; label?: string }) {
  return (
    <span aria-live="polite" className={cn("inline-flex items-center gap-1.5 text-xs font-normal text-muted-foreground", className)}>
      {show && (
        <>
          <span className="size-1.5 rounded-full bg-foreground/60 motion-safe:animate-pulse" aria-hidden />
          {label}
        </>
      )}
    </span>
  );
}
