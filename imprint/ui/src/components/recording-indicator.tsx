"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { stopMeeting, useRecorder } from "@/lib/recorder";

export function clock(seconds: number) {
  const s = Math.max(0, Math.floor(seconds));
  const pad = (n: number) => String(n).padStart(2, "0");
  return s >= 3600 ? `${Math.floor(s / 3600)}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}` : `${pad(Math.floor(s / 60))}:${pad(s % 60)}`;
}

/** Top-bar notice while a meeting is recorded, on every page, with Stop. */
export function RecordingIndicator() {
  const recorder = useRecorder();
  const [now, setNow] = useState(0);
  const active = recorder.phase === "recording" || recorder.phase === "stopping";
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  if (!active) return null;
  return (
    <div role="status" aria-label="Recording" className="flex items-center gap-2 rounded-full border border-destructive/40 bg-destructive/10 py-0.5 pr-1 pl-3 text-xs">
      <span className="size-2 animate-pulse rounded-full bg-destructive" aria-hidden />
      <Link href="/recordings/" className="font-medium tabular-nums">
        {recorder.phase === "stopping" ? "Saving…" : `Recording ${clock(((now || recorder.startedAt) - recorder.startedAt) / 1000)}`}
      </Link>
      <Button size="xs" variant="ghost" aria-label="Stop recording" disabled={recorder.phase === "stopping"} onClick={() => void stopMeeting()}>
        <Square className="fill-current" /> Stop
      </Button>
    </div>
  );
}
