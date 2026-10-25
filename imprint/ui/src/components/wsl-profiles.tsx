"use client";

import { useEffect, useRef, useState } from "react";
import { RotateCw, Search } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Spinner } from "@/components/loaders";
import { getWslProfiles, streamCommand, type WslListing } from "@/lib/api";
import { useFollowOutput } from "@/lib/use-follow-output";

type Profile = { distribution: string; target: string; label: string };
type Job = { title: string; output: string; state: "running" | "done" | "failed" };

/** Enrolls the AI clients inside WSL distributions with this Windows
 * installation (`imprint wsl setup` / `remove`, queued). The clients it
 * offers are the CLI's WSL targets, so a client the CLI adds appears here. */
export function WslProfiles() {
  const [listing, setListing] = useState<WslListing | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [removing, setRemoving] = useState<Profile | null>(null);
  const controller = useRef<AbortController | null>(null);
  const log = useFollowOutput(job?.output ?? "");
  const busy = job?.state === "running";

  useEffect(() => () => controller.current?.abort(), []);

  // Probing runs every distribution, so it happens only when asked.
  function check() {
    setChecking(true);
    setError("");
    getWslProfiles()
      .then(setListing)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setChecking(false));
  }

  function run(action: "setup" | "remove", profile: Profile) {
    const title = `${action === "setup" ? "Enrolling" : "Removing"} ${profile.label} in ${profile.distribution}`;
    setJob({ title, output: "", state: "running" });
    controller.current = streamCommand("wsl", { action, distribution: profile.distribution, target: profile.target }, (ev) => {
      if (ev.type === "output") {
        setJob((j) => j && { ...j, output: j.output + (ev.text as string) });
      } else if (ev.type === "done") {
        setJob((j) => j && { ...j, state: ev.exit_code === 0 ? "done" : "failed" });
        check();
      } else if (ev.type === "error") {
        setJob((j) => j && { ...j, output: `${j.output}ERROR: ${ev.error}\n`, state: "failed" });
      }
    });
  }

  if (!listing) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">
          Enroll AI clients that live inside your WSL distributions, so they use this installation&apos;s memories.
          Checking runs each distribution, which starts any that are stopped.
        </p>
        {error && <p role="alert" className="text-sm text-destructive">WSL profiles could not be listed: {error}</p>}
        <Button variant="outline" onClick={check} disabled={checking}>
          {checking ? <Spinner /> : <Search />}
          {checking ? "Checking distributions…" : "Check distributions"}
        </Button>
      </div>
    );
  }

  if (!listing.supported) {
    return (
      <p className="text-sm text-muted-foreground">
        WSL profiles are enrolled from a Windows installation of Imprint; this one runs on {listing.platform || "another system"}.
        Clients on this system connect directly from the Commands page.
      </p>
    );
  }

  const labels = Object.fromEntries(listing.targets.map((t) => [t.name, t.label]));
  return (
    <div className="space-y-4" aria-busy={checking}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">Each distribution&apos;s default user. Checking again starts stopped distributions.</p>
        <Button variant="outline" size="sm" onClick={check} disabled={checking || busy}>
          {checking ? <Spinner /> : <RotateCw />}
          Check again
        </Button>
      </div>

      {(listing.error || error) && (
        <Alert variant="destructive">
          <AlertDescription>{listing.error || `WSL profiles could not be listed: ${error}`}</AlertDescription>
        </Alert>
      )}
      {!listing.error && listing.distributions.length === 0 && (
        <p className="text-sm text-muted-foreground">No WSL distributions found.</p>
      )}

      {listing.distributions.map((d) => (
        <section key={d.distribution} aria-label={`WSL ${d.distribution}`} className="rounded-lg border border-border">
          <header className="flex flex-wrap items-baseline gap-x-2 border-b border-border px-3 py-2">
            <h3 className="text-sm font-medium">{d.distribution}</h3>
            {d.home && <code className="font-mono text-xs text-muted-foreground">{d.home}</code>}
          </header>
          {d.error ? (
            <p className="px-3 py-2 text-sm text-destructive">Could not read its profile: {d.error}</p>
          ) : (
            <ul className="divide-y divide-border">
              {d.clients.map((c) => {
                const profile = { distribution: d.distribution, target: c.name, label: labels[c.name] ?? c.name };
                return (
                  <li key={c.name} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
                    <span className="min-w-28 text-sm font-medium">{profile.label}</span>
                    {c.enrolled ? (
                      <Badge variant="secondary" className="bg-emerald-500/10 text-emerald-700 dark:text-emerald-400">Enrolled</Badge>
                    ) : (
                      <span className="text-xs text-muted-foreground">{c.profile ? "Not enrolled" : "No profile"}</span>
                    )}
                    {c.profile && <code className="min-w-0 truncate font-mono text-xs text-muted-foreground">{c.profile}</code>}
                    <span className="ml-auto">
                      {c.enrolled ? (
                        <Button variant="outline" size="sm" className="text-destructive" disabled={busy}
                          aria-label={`Remove ${profile.label} in ${d.distribution}`} onClick={() => setRemoving(profile)}>
                          Remove
                        </Button>
                      ) : c.profile ? (
                        <Button variant="outline" size="sm" disabled={busy}
                          aria-label={`Enroll ${profile.label} in ${d.distribution}`} onClick={() => run("setup", profile)}>
                          Enroll
                        </Button>
                      ) : null}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      ))}

      {job && (
        <div className="rounded-lg border border-border">
          <div className="flex items-center gap-2 border-b border-border px-3 py-2">
            <span className="text-sm font-medium">{job.title}</span>
            {job.state === "running" && <Badge variant="secondary" className="gap-1"><Spinner className="size-3" />Running</Badge>}
            {job.state === "done" && <Badge variant="secondary" className="bg-emerald-500/10 text-emerald-700 dark:text-emerald-400">Succeeded</Badge>}
            {job.state === "failed" && <Badge variant="destructive">Failed</Badge>}
          </div>
          <ScrollArea className="h-40 bg-muted/30" viewportRef={log}>
            <pre aria-live="polite" className="p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words">
              {job.output || <span className="text-muted-foreground">Waiting for output…</span>}
            </pre>
          </ScrollArea>
        </div>
      )}

      <AlertDialog open={removing !== null} onOpenChange={(open) => { if (!open) setRemoving(null); }}>
        {removing && (
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Remove {removing.label} in {removing.distribution}?</AlertDialogTitle>
              <AlertDialogDescription>
                Imprint stops using this profile: what enrolling wrote into it is removed, and learn no longer reads its
                conversations. Memories and other profiles stay.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <Button variant="destructive" onClick={() => { run("remove", removing); setRemoving(null); }}>Remove</Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        )}
      </AlertDialog>
    </div>
  );
}
