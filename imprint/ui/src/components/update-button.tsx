"use client";

import { useEffect, useState } from "react";
import { ArrowUpCircle, CheckCircle2, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/modal";
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
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-foreground px-2.5 text-sm font-medium text-background shadow-xs outline-none transition-opacity hover:opacity-90 focus-visible:ring-3 focus-visible:ring-ring/50"
        style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
      >
        {busy ? <Spinner className="size-4" /> : <ArrowUpCircle className="size-4" aria-hidden />}
        <span className="md:hidden">{busy ? "Updating" : "Update"}</span>
        <span className="hidden md:inline">{busy ? `Updating to ${target}…` : `Update to ${target}`}</span>
      </button>
      {open && (
        <Modal label="Update Imprint" onClose={() => setOpen(false)} className="max-w-lg p-0">
          <div className="border-b border-border px-6 py-4">
            <h2 className="text-base font-semibold">Update Imprint</h2>
            <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
              <span>
                {info.current} → <span className="font-medium text-foreground">{target}</span>
              </span>
              {info.url && (
                <a href={info.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 underline-offset-2 hover:text-foreground hover:underline">
                  Release notes <ExternalLink className="size-3" aria-hidden />
                </a>
              )}
            </p>
          </div>
          <div className="space-y-4 px-6 py-5 text-sm">
            <Body phase={phase} info={info} target={target} log={log} />
          </div>
          <div className="flex justify-end gap-2 border-t border-border px-6 py-3">
            {phase.kind === "done" ? (
              <Button size="sm" onClick={() => window.location.reload()}>Reload dashboard</Button>
            ) : (
              <>
                <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
                  {busy ? "Hide" : "Close"}
                </Button>
                {!info.unsupported && (phase.kind === "idle" || phase.kind === "stopped" || phase.kind === "error") && (
                  <Button size="sm" onClick={begin}>
                    {phase.kind === "idle" ? "Update now" : "Try again"}
                  </Button>
                )}
              </>
            )}
          </div>
        </Modal>
      )}
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
          <Spinner className="size-4" /> Updating to {target}. The dashboard reconnects when the new version is running.
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
