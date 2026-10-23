"use client";

import { useEffect, useState } from "react";
import { ArrowLeft, ArrowLeftRight, ArrowRight, Link2, MonitorSmartphone, Pause, Play, Trash2 } from "lucide-react";
import { ConfirmDialog } from "./confirm-dialog";
import { getPairedDevices, controlPairedDevice, type PairedDevice } from "@/lib/sync-api";

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
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [forget, setForget] = useState<PairedDevice | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const result = await getPairedDevices();
        if (!cancelled) { setPeers(result); setError(""); }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    }
    void refresh();
    const timer = setInterval(refresh, 5000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  async function change(peer: PairedDevice, action: "enabled" | "forget") {
    setBusy(true); setError("");
    try {
      await controlPairedDevice(peer.id, action, !peer.enabled);
      setPeers(await getPairedDevices());
      setForget(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally { setBusy(false); }
  }

  return (
    <section aria-labelledby="paired-devices-title" className="overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
      <div className="border-b border-border px-5 py-4 sm:px-6">
        <h3 id="paired-devices-title" className="font-semibold">Paired devices</h3>
        <p className="mt-0.5 max-w-2xl text-xs text-muted-foreground">
          Paired devices keep a workspace in step on their own: while Imprint runs on both, they reconnect through the
          same relay and merge what changed about once a minute. Pausing either side stops transfers. Forgetting a
          device keeps the memories it already shared.
        </p>
      </div>
      {error && <p role="alert" className="px-5 pt-4 text-sm text-destructive sm:px-6">{error}</p>}
      {!peers.length ? (
        <div className="flex items-start gap-3 px-5 py-5 sm:px-6">
          <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted">
            <Link2 className="size-4 text-muted-foreground" aria-hidden />
          </span>
          <div className="text-sm">
            <p className="font-medium">No paired devices yet</p>
            <p className="text-muted-foreground">
              To pair, turn on &ldquo;Enable automatic sync with this device&rdquo; on both devices during a live sync.
            </p>
          </div>
        </div>
      ) : (
        <ul className="divide-y divide-border">
          {peers.map((peer) => {
            const direction = DIRECTION[peer.direction] ?? DIRECTION.both;
            const Arrow = direction.icon;
            const pending = peer.state === "forget_pending";
            return (
              <li key={peer.id} className="flex flex-wrap items-start gap-x-4 gap-y-3 px-5 py-4 sm:px-6">
                <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted">
                  <MonitorSmartphone className="size-4" aria-hidden />
                </span>
                <div className="min-w-0 flex-1 space-y-1.5">
                  <p className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{peer.peer.hostname}</span>
                    <span className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">{peer.peer.fingerprint}</span>
                    <State peer={peer} />
                  </p>
                  <p className="flex flex-wrap items-center gap-1.5 text-sm">
                    <span className="font-mono">{peer.workspace}</span>
                    <Arrow className="size-3.5 text-muted-foreground" aria-label={direction.label} />
                    <span className="font-mono">{peer.remote_workspace}</span>
                    <span className="text-xs text-muted-foreground">· {direction.label} · {peer.relay.replace(/^wss?:\/\//, "")}</span>
                  </p>
                  <p className="text-xs text-muted-foreground" role="status">
                    {pending
                      ? "Revoked locally. Remote removal pending: keep Imprint running on both devices until they reconnect."
                      : peer.enabled ? "Automatic sync enabled" : "Automatic sync paused on this device"}
                    {peer.last_sync ? ` · last sync ${ago(peer.last_sync)}` : ""}
                    {peer.last_error ? ` · last connection: ${peer.last_error}` : ""}
                  </p>
                </div>
                {!pending && (
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void change(peer, "enabled")}
                      className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-input bg-background px-3 text-sm font-medium transition-colors hover:bg-muted disabled:opacity-50"
                    >
                      {peer.enabled ? <Pause className="size-3.5" aria-hidden /> : <Play className="size-3.5" aria-hidden />}
                      {peer.enabled ? "Pause" : "Resume"}
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => setForget(peer)}
                      className="inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-sm font-medium text-destructive transition-colors hover:bg-red-50 disabled:opacity-50"
                    >
                      <Trash2 className="size-3.5" aria-hidden /> Forget…
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <ConfirmDialog
        open={forget !== null}
        title="Forget this device on both sides?"
        description={`Remove automatic pairing with ${forget?.peer.hostname ?? "this device"} for ALL paired workspaces. Existing memories remain. The other device forgets this one when both reconnect to the relay; offline removal stays pending.`}
        confirmLabel="Forget device"
        busy={busy}
        destructive
        onCancel={() => setForget(null)}
        onConfirm={() => { if (forget) void change(forget, "forget"); }}
      />
    </section>
  );
}
