"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, ArrowLeft, ArrowLeftRight, ArrowRight, Link2, MonitorSmartphone, MoreHorizontal, Pause, Play, Trash2, Unlink } from "lucide-react";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Spinner } from "@/components/loaders";
import { getPairedDevices, controlPairedDevice, type PairedDevice, type PairedDeviceAction } from "@/lib/sync-api";

/** How this device's workspace relates to the peer's, from this side. */
const DIRECTION = {
  send: { icon: ArrowRight, label: "Sends" },
  receive: { icon: ArrowLeft, label: "Receives" },
  both: { icon: ArrowLeftRight, label: "Two-way" },
} as const;

function ago(ts: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

/** A paired device not reached for this long may be gone for good. */
const STALE_DAYS = 7;

/** Whole days since the paired device last answered (or, never, since pairing). */
function unseenDays(peer: PairedDevice): number {
  return Math.floor((Date.now() / 1000 - (peer.last_seen ?? peer.created_at)) / 86400);
}

/** What each confirmation says; forgetting tells the peer, removing does not. */
const CONFIRM = {
  forget: {
    title: "Forget this device on both sides?",
    button: "Forget device",
    body: (host: string) => `Remove automatic pairing with ${host} for ALL paired workspaces. Existing memories remain. `
      + "The other device forgets this one when both reconnect to the relay; offline removal stays pending.",
  },
  remove: {
    title: "Remove this pairing from this device only?",
    button: "Remove from this device",
    body: (host: string) => `This device deletes its automatic pairing with ${host} for ALL paired workspaces now, `
      + `with the keys and trust it holds for it. ${host} is not notified: if it comes back, its automatic sync reports `
      + "that this device hasn't answered until its pairing is removed there the same way. Existing memories remain. "
      + "Pair again with a new code to sync with it.",
  },
} as const;

function State({ peer }: { peer: PairedDevice }) {
  const [tone, text] =
    peer.state === "forget_pending"
      ? ["border-amber-600/25 bg-amber-50 text-amber-800", "Removal pending"]
      : peer.enabled
        ? ["border-emerald-600/25 bg-emerald-50 text-emerald-800", "Active"]
        : ["border-border bg-muted text-foreground", "Paused"];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${tone}`}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {text}
    </span>
  );
}

export function PairedDevices() {
  const [peers, setPeers] = useState<PairedDevice[]>([]);
  // Whether the list was ever read: a failed read says nothing about pairings.
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  // The change under way, so its button shows progress and the others wait.
  const [pending, setPending] = useState<{ id: string; action: PairedDeviceAction } | null>(null);
  // The question the confirmation dialog asks, kept while it animates closed.
  const [confirm, setConfirm] = useState<{ peer: PairedDevice; action: keyof typeof CONFIRM } | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const asked = CONFIRM[confirm?.action ?? "forget"];
  const busy = pending !== null;

  function ask(peer: PairedDevice, action: keyof typeof CONFIRM) {
    setConfirm({ peer, action }); setConfirmOpen(true);
  }

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const result = await getPairedDevices();
        if (!cancelled) { setPeers(result); setLoaded(true); setError(""); }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    }
    void refresh();
    const timer = setInterval(refresh, 5000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  async function change(peer: PairedDevice, action: PairedDeviceAction) {
    setPending({ id: peer.id, action }); setError("");
    try {
      await controlPairedDevice(peer.id, action, !peer.enabled);
      setPeers(await getPairedDevices());
      setConfirmOpen(false);
      const host = peer.peer.hostname;
      toast.success(action === "forget" ? `Forgot ${host} on this device`
        : action === "remove" ? `Removed ${host} from this device`
          : peer.enabled ? `Paused automatic sync with ${host}` : `Resumed automatic sync with ${host}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally { setPending(null); }
  }

  return (
    <section aria-labelledby="paired-devices-title" className="overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
      <div className="border-b border-border px-5 py-4 sm:px-6">
        <h3 id="paired-devices-title" className="font-semibold">Paired devices</h3>
        <p className="mt-0.5 max-w-2xl text-xs text-muted-foreground">
          Paired devices keep a workspace in step on their own: while Imprint runs on both, they reconnect through the
          same relay and merge what changed about once a minute. Pausing either side stops transfers. Forgetting a
          device keeps the memories it already shared. A device that is gone for good, uninstalled or reset, can be
          removed from this one alone.
        </p>
      </div>
      {error && <p role="alert" className="px-5 pt-4 text-sm text-destructive sm:px-6">{error}</p>}
      {!loaded ? (
        !error && (
          <p className="flex items-center gap-2 px-5 py-8 text-sm text-muted-foreground sm:px-6" role="status">
            <Spinner /> Loading paired devices…
          </p>
        )
      ) : !peers.length ? (
        <Empty className="px-5 py-8 sm:px-6">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Link2 className="text-muted-foreground" aria-hidden />
            </EmptyMedia>
            <EmptyTitle>No paired devices yet</EmptyTitle>
            <EmptyDescription>
              To pair, turn on &ldquo;Enable automatic sync with this device&rdquo; on both devices during a live sync.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <ul className="divide-y divide-border">
          {peers.map((peer) => {
            const direction = DIRECTION[peer.direction] ?? DIRECTION.both;
            const Arrow = direction.icon;
            const removing = peer.state === "forget_pending";
            const unseen = unseenDays(peer);
            const stale = unseen >= STALE_DAYS;
            // Point to local removal when the peer may never answer again.
            const gone = removing || stale;
            return (
              <li key={peer.id} className="flex items-start gap-4 px-5 py-4 sm:px-6">
                <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted">
                  <MonitorSmartphone className="size-4" aria-hidden />
                </span>
                <div className="min-w-0 flex-1 space-y-1.5">
                  <p className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{peer.peer.hostname}</span>
                    <span className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">{peer.peer.fingerprint}</span>
                    <State peer={peer} />
                    {stale && (
                      <span className="inline-flex items-center gap-1 whitespace-nowrap rounded-full border border-amber-600/25 bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-800">
                        <AlertTriangle className="size-3" aria-hidden /> Not seen for {unseen} days
                      </span>
                    )}
                  </p>
                  <p className="flex flex-wrap items-center gap-1.5 text-sm">
                    <span className="font-mono">{peer.workspace}</span>
                    <Arrow className="size-3.5 text-muted-foreground" aria-label={direction.label} />
                    <span className="font-mono">{peer.remote_workspace}</span>
                    <span className="text-xs text-muted-foreground">· {direction.label} · {peer.relay.replace(/^wss?:\/\//, "")}</span>
                  </p>
                  <p className="text-xs text-muted-foreground" role="status">
                    {removing
                      ? "Revoked locally. Remote removal pending: keep Imprint running on both devices until they reconnect."
                      : peer.enabled ? "Automatic sync enabled" : "Automatic sync paused on this device"}
                    {peer.last_sync ? ` · last sync ${ago(peer.last_sync)}` : ""}
                  </p>
                  {peer.last_error && <p className="text-xs text-muted-foreground">Last connection: {peer.last_error}</p>}
                  {gone && (
                    <p className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
                      <AlertTriangle className="mt-px size-3.5 shrink-0" aria-hidden />
                      If {peer.peer.hostname} was uninstalled or reset, choose Remove from this device in its actions menu.
                    </p>
                  )}
                </div>
                <DropdownMenu>
                  <DropdownMenuTrigger
                    render={<Button variant="ghost" size="icon" disabled={busy} aria-label={`Actions for ${peer.peer.hostname}`} />}
                  >
                    {pending?.id === peer.id ? <Spinner className="size-3.5" /> : <MoreHorizontal />}
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-60">
                    {!removing && (<>
                      <DropdownMenuItem onClick={() => void change(peer, "enabled")}>
                        {peer.enabled ? <Pause /> : <Play />} {peer.enabled ? "Pause automatic sync" : "Resume automatic sync"}
                      </DropdownMenuItem>
                      <DropdownMenuItem variant="destructive" onClick={() => ask(peer, "forget")}>
                        <Trash2 /> Forget on both devices…
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                    </>)}
                    {/* Emphasized when the other device may never answer again. */}
                    <DropdownMenuItem variant="destructive" onClick={() => ask(peer, "remove")}
                      className={gone ? "bg-destructive/10 font-medium" : undefined}>
                      <Unlink /> Remove from this device…
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </li>
            );
          })}
        </ul>
      )}
      <AlertDialog open={confirmOpen} onOpenChange={(open) => { if (!open && !busy) setConfirmOpen(false); }}>
        <AlertDialogContent aria-busy={busy}>
          <AlertDialogHeader>
            <AlertDialogTitle>{asked.title}</AlertDialogTitle>
            <AlertDialogDescription>{asked.body(confirm?.peer.peer.hostname ?? "this device")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <Button variant="destructive" disabled={busy} onClick={() => { if (confirm) void change(confirm.peer, confirm.action); }}>
              {busy && <Spinner />} {asked.button}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
