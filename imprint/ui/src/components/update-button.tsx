"use client";

import { useEffect, useState } from "react";
import { ArrowUpCircle, CheckCircle2, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Spinner } from "@/components/loaders";
import { getUpdate, getVersion, startUpdate, type UpdateInfo, type UpdateMethod } from "@/lib/api";

const STEPS: Record<UpdateMethod, string> = {
  archive:
    "Imprint downloads the release, checks it against the published checksums, installs it and restarts the dashboard.",
  "windows-setup":
    "Imprint downloads the installer, checks it against the published checksums and closes. The installer updates Imprint and opens it again.",
  "macos-pkg":
    "Imprint downloads the installer, checks it against the published checksums and opens it. Follow its steps; the dashboard restarts when it finishes.",
};

type Phase =
  | { kind: "idle" }
  | { kind: "starting" }
  | { kind: "updating" }
  // The updater exited and the new version isn't serving (failed, or needs a restart).
  | { kind: "stopped" }
  | { kind: "done" }
  | { kind: "error"; message: string };

const POLL_MS = 2000;

/** Top-bar notice for a newer release, and the dialog that installs it. */
export function UpdateButton() {
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [log, setLog] = useState<string[]>([]);

  useEffect(() => {
    getUpdate().then(setInfo).catch(() => {});
  }, []);

  const target = info?.latest;
  const watching = phase.kind === "updating" || phase.kind === "stopped";
  useEffect(() => {
    if (!watching || !target) return;
    const timer = setInterval(async () => {
      try {
        const [{ version }, status] = await Promise.all([getVersion(), getUpdate()]);
        if (version === target) return setPhase({ kind: "done" });
        setLog(status.log);
        if (!status.running) setPhase({ kind: "stopped" });
      } catch {
        // The server is restarting, or the installer has closed Imprint.
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [watching, target]);

  async function begin() {
    setPhase({ kind: "starting" });
    setLog([]);
    try {
      await startUpdate();
      setPhase({ kind: "updating" });
    } catch (e) {
      setPhase({ kind: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }

  if (!info?.update_available || !target) return null;
  const busy = phase.kind === "starting" || phase.kind === "updating";

  return (
    <>
      <Button onClick={() => setOpen(true)}>
        {busy ? <Spinner /> : <ArrowUpCircle aria-hidden />}
        <span className="md:hidden">{busy ? "Updating" : "Update"}</span>
        <span className="hidden md:inline">{busy ? `Updating to ${target}…` : `Update to ${target}`}</span>
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent showCloseButton={false} className="gap-0 p-0 sm:max-w-lg">
          <DialogHeader className="gap-1 border-b border-border px-6 py-4">
            <DialogTitle>Update Imprint</DialogTitle>
            <DialogDescription className="flex flex-wrap items-center gap-x-2">
              <span>
                {info.current} → <span className="font-medium text-foreground">{target}</span>
              </span>
              {info.url && (
                <a href={info.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1">
                  Release notes <ExternalLink className="size-3" aria-hidden />
                </a>
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 px-6 py-5 text-sm">
            <Body phase={phase} info={info} target={target} log={log} />
          </div>
          <DialogFooter className="mx-0 mb-0 px-6 py-3">
            {phase.kind === "done" ? (
              <Button onClick={() => window.location.reload()}>Reload dashboard</Button>
            ) : (
              <>
                <Button variant="ghost" onClick={() => setOpen(false)}>
                  {busy ? "Hide" : "Close"}
                </Button>
                {!info.unsupported && (phase.kind === "idle" || phase.kind === "stopped" || phase.kind === "error") && (
                  <Button onClick={begin}>
                    {phase.kind === "idle" ? "Update now" : "Try again"}
                  </Button>
                )}
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function Body({ phase, info, target, log }: { phase: Phase; info: UpdateInfo; target: string; log: string[] }) {
  if (phase.kind === "done") {
    return (
      <p role="status" className="flex items-center gap-2">
        <CheckCircle2 className="size-4 text-emerald-700" aria-hidden /> Imprint {target} is running. Reload to use the new dashboard.
      </p>
    );
  }
  if (info.unsupported) {
    return <p>This installation can&apos;t update itself: {info.unsupported}.</p>;
  }
  const steps = info.method ? STEPS[info.method] : "";
  return (
    <>
      {phase.kind === "idle" && (
        <>
          <p>{steps}</p>
          <p className="text-muted-foreground">Your memories, workspaces and settings are kept. Running jobs must finish first.</p>
        </>
      )}
      {(phase.kind === "starting" || phase.kind === "updating") && (
        <p role="status" className="flex items-center gap-2">
          <Spinner /> Updating to {target}. The dashboard reconnects when the new version is running.
        </p>
      )}
      {phase.kind === "stopped" && (
        <p role="alert">The updater stopped before {target} was running. If its log says to restart Imprint, close and reopen it.</p>
      )}
      {phase.kind === "error" && <p role="alert" className="text-destructive">{phase.message}</p>}
      {log.length > 0 && (
        <pre aria-label="Updater log" className="max-h-48 overflow-auto rounded-lg bg-muted p-3 font-mono text-xs whitespace-pre-wrap">
          {log.join("\n")}
        </pre>
      )}
    </>
  );
}
