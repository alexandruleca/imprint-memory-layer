"use client";

import { Check, Cpu } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { Spinner } from "@/components/ui/spinner";
import type { Setup } from "../_lib/turns";

const gb = (bytes: number) => `${(bytes / 1e9).toFixed(bytes >= 1e10 ? 0 : 1)} GB`;

function Step({ done, label, children }: { done: boolean; label: string; children?: React.ReactNode }) {
  return (
    <li className="flex gap-2.5">
      <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center">
        {done ? <Check className="size-4" aria-label="Done" /> : <Spinner className="size-3.5" aria-label="In progress" />}
      </span>
      <div className="min-w-0 flex-1 space-y-1.5">
        <p className={done ? "text-muted-foreground" : "font-medium"}>{label}</p>
        {!done && children}
      </div>
    </li>
  );
}

/** Progress of the one-time model download and of loading the model, which
 * every first message after a restart waits for. */
export function SetupProgress({ setup }: { setup: Setup }) {
  const { download, load } = setup;
  const finished = (!download || download.done) && (!load || load.done);

  if (!download) {
    if (!load || load.done) return null;
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground" role="status">
        <Spinner className="size-3.5" aria-hidden />
        <span>Loading the local model…</span>
      </div>
    );
  }
  if (finished) {
    return (
      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <Check className="size-3.5" aria-hidden /> Local chat is set up
      </p>
    );
  }
  const percent = download.total ? Math.round((download.downloaded / download.total) * 100) : null;
  return (
    <section aria-label="Setting up local chat" className="space-y-3 rounded-xl border border-border bg-card p-4 text-sm">
      <div className="space-y-0.5">
        <h3 className="flex items-center gap-2 font-medium"><Cpu className="size-4" aria-hidden /> Setting up local chat</h3>
        <p className="text-xs text-muted-foreground">This happens once. The model then runs on this device.</p>
      </div>
      <ol className="space-y-2.5">
        <Step done={download.done} label={`Download ${download.file}`}>
          <Progress value={percent} aria-label="Model download" />
          <p className="text-xs text-muted-foreground tabular-nums">
            {download.total ? `${gb(download.downloaded)} of ${gb(download.total)} · ${percent}%` : "Starting download…"}
          </p>
        </Step>
        {load && <Step done={load.done} label="Load the model" />}
      </ol>
    </section>
  );
}
