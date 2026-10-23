"use client";

import { useEffect, useState } from "react";
import { ConfirmDialog } from "./confirm-dialog";
import { getPairedDevices, controlPairedDevice, type PairedDevice } from "@/lib/sync-api";

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

  return <section className="rounded-lg border p-5 space-y-3" aria-labelledby="paired-devices-title">
    <h3 id="paired-devices-title" className="text-lg font-semibold">Paired devices</h3>
    <p className="text-sm text-muted-foreground">
      Both devices must enable automatic pairing during a live sync. While Imprint is running on both,
      it reconnects through the same relay and merges changed workspace data about once a minute.
      Pausing either side stops transfers. Existing memories stay when a device is forgotten.
    </p>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {!peers.length && <p className="text-sm text-muted-foreground">No paired devices yet. Enable automatic sync on both sides below.</p>}
    <ul className="space-y-3">{peers.map(peer => <li key={peer.id} className="rounded-md border p-3 space-y-2">
      <p className="font-medium">{peer.peer.hostname} <span className="text-xs font-mono text-muted-foreground">{peer.peer.fingerprint}</span></p>
      <p className="text-xs text-muted-foreground break-words">{peer.workspace} ↔ {peer.remote_workspace} · {peer.direction} · {peer.relay}</p>
      <p className="text-sm" role="status">{peer.state === "forget_pending"
        ? "Revoked locally. Remote removal pending: keep Imprint running on both devices until they reconnect."
        : peer.enabled ? "Automatic sync enabled" : "Automatic sync paused on this device"}</p>
      {peer.last_sync && <p className="text-xs text-muted-foreground">Last completed sync: {new Date(peer.last_sync * 1000).toLocaleString()}</p>}
      {peer.last_error && <p className="text-xs text-muted-foreground">Last connection: {peer.last_error}</p>}
      {peer.state === "active" && <div className="flex flex-wrap gap-3">
        <button disabled={busy} onClick={() => void change(peer, "enabled")} className="text-sm underline disabled:opacity-50">{peer.enabled ? "Pause automatic sync" : "Enable automatic sync"}</button>
        <button disabled={busy} onClick={() => setForget(peer)} className="text-sm text-destructive underline disabled:opacity-50">Forget device…</button>
      </div>}
    </li>)}</ul>
    <ConfirmDialog open={forget !== null} title="Forget this device on both sides?"
      description={`Remove automatic pairing with ${forget?.peer.hostname ?? "this device"} for ALL paired workspaces. Existing memories remain. The other device forgets this one when both reconnect to the relay; offline removal stays pending.`}
      confirmLabel="Forget device" busy={busy} destructive
      onCancel={() => setForget(null)} onConfirm={() => { if (forget) void change(forget, "forget"); }} />
  </section>;
}
