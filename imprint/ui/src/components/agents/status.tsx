import { cn } from "@/lib/utils";

/** Running (a pulsing green dot) or finished (a grey one). */
export function StatusDot({ connected, decorative, className }: { connected: boolean; decorative?: boolean; className?: string }) {
  return (
    <span {...(decorative ? { "aria-hidden": true } : { role: "img", "aria-label": connected ? "Running" : "Finished" })} className={cn("relative inline-flex size-2 shrink-0", className)}>
      {connected && <span aria-hidden className="absolute inline-flex size-full animate-ping rounded-full bg-success opacity-60 motion-reduce:hidden" />}
      <span aria-hidden className={cn("relative inline-flex size-2 rounded-full", connected ? "bg-success" : "bg-muted-foreground/40")} />
    </span>
  );
}
