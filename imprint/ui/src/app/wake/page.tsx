"use client";

import { useEffect } from "react";
import { WakeListener } from "@/lib/wake-listener";

/** The desktop app's hidden "Hey Echo" listener (ADR 0137). Its window is never
 * shown; the orb, the tray and the dashboard's top bar show that it listens.
 * Outside the desktop app this page does nothing. */
export default function WakePage() {
  useEffect(() => {
    const report = window.electronAPI?.reportWakeListener;
    if (!report) return;
    const listener = new WakeListener(report);
    void listener.start();
    return () => listener.stop();
  }, []);
  return <p className="sr-only">The Imprint desktop app listens for “Hey Echo” here.</p>;
}
