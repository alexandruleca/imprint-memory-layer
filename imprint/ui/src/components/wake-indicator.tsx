"use client";

import { Pause, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useWakeState } from "@/lib/wake";

/** Top-bar notice while the desktop app listens for "Hey Echo" (ADR 0137): it
 * shows whenever the hidden listener exists, on every page, with Pause and Off. */
export function WakeIndicator() {
  const wake = useWakeState();
  if (!wake?.listening) return null;
  return (
    <div role="status" aria-label="Listening for Hey Echo" className="flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 py-0.5 pr-1 pl-3 text-xs">
      <span className="size-2 rounded-full bg-primary" aria-hidden />
      <span className="ml-1 font-medium">Listening for “Hey Echo”</span>
      <Button size="xs" variant="ghost" aria-label="Pause listening for 1 hour" onClick={() => window.electronAPI?.controlWake?.("pause")}>
        <Pause /> 1 hour
      </Button>
      <Button size="xs" variant="ghost" aria-label="Turn off Hey Echo" onClick={() => window.electronAPI?.controlWake?.("off")}>
        <X /> Off
      </Button>
    </div>
  );
}
