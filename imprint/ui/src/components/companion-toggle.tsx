"use client";

import { useEffect, useState } from "react";
import { Switch } from "@/components/ui/switch";

/** The main process owns this state; closing from the companion or tray must
 * update the dashboard as well. Showing never starts microphone capture. */
export function CompanionToggle() {
  const [available, setAvailable] = useState(false);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const api = window.electronAPI;
    if (!api?.getCompanionState || !api.onCompanionState) return;
    let alive = true, revision = 0;
    const unsubscribe = api.onCompanionState(state => { revision++; if (alive) setOpen(state.open); });
    const initialRevision = revision;
    void api.getCompanionState().then(state => {
      if (alive) { setAvailable(true); if (state && revision === initialRevision) setOpen(state.open); }
    }).catch(() => {});
    return () => { alive = false; unsubscribe(); };
  }, []);
  if (!available) return null;
  return <div className="app-no-drag flex items-center gap-2 text-xs text-muted-foreground">
    <label htmlFor="floating-echo-toggle">Floating Echo</label>
    <Switch id="floating-echo-toggle" aria-label="Floating Echo" checked={open} onCheckedChange={enabled => {
      setError("");
      if (enabled) window.dispatchEvent(new Event("imprint:stop-dashboard-voice"));
      void window.electronAPI?.setCompanionVisible?.(enabled).catch(() => setError("Couldn't change the floating companion. Try again."));
    }}/>
    {error && <span role="status">{error}</span>}
  </div>;
}
