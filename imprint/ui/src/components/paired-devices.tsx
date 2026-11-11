"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, ArrowLeft, ArrowLeftRight, ArrowRight, Link2, MonitorSmartphone, MoreHorizontal, Pause, Play, Search, Trash2, Unlink } from "lucide-react";
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
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Progress } from "@/components/ui/progress";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Pager } from "@/components/review/pager";
import { Spinner } from "@/components/loaders";
import { getPairedDevices, controlPairedDevice, type PairedDevice, type PairedDeviceAction } from "@/lib/sync-api";
import { useCached } from "@/lib/use-cached";

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

/** What a running automatic sync is doing, in words. */
const STAGE = {
  connecting: (host: string) => `Waiting for ${host} to connect`,
  comparing: (host: string) => `Comparing workspaces with ${host}`,
  sending: (host: string) => `Sending to ${host}`,
  receiving: (host: string) => `Receiving from ${host}`,
  storing: () => "Storing what arrived",
} as const;

const count = new Intl.NumberFormat();

/** "120 memories, 3 facts": the nonzero counts of a sync's datasets. */
function moved(counts: Record<string, number> | undefined): string {
  return Object.entries(counts ?? {}).filter(([, n]) => n > 0).map(([dataset, n]) => `${count.format(n)} ${dataset}`).join(", ");
}

function elapsed(since: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - since));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)} min ${s % 60}s`;
}

/** The running attempt: its stage and, while records move, how far it is. */
function SyncProgress({ peer }: { peer: PairedDevice }) {
  const activity = peer.activity;
  if (!activity) return null;
  const host = peer.peer.hostname;
  const counted = typeof activity.done === "number" && typeof activity.total === "number" && activity.total > 0;
  const percent = counted ? Math.min(100, Math.round((activity.done! / activity.total!) * 100)) : null;
  const label = (STAGE[activity.stage] ?? STAGE.comparing)(host);
  return (
    <div className="mt-2 space-y-2 rounded-lg border border-border bg-muted/40 px-3 py-2.5" role="status" aria-live="polite"
      aria-label={`Sync with ${host}`}>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        <Spinner className="size-3" />
        <span className="font-medium">{label}</span>
        {counted && (
          <span className="text-muted-foreground">
            · {activity.dataset} {count.format(activity.done!)} of {count.format(activity.total!)}
          </span>
        )}
        <span className="ml-auto tabular-nums text-muted-foreground">{elapsed(activity.since)}</span>
      </p>
      {percent !== null && <Progress value={percent} aria-label={`${activity.dataset} ${percent}%`} />}
    </div>
  );
}

/** When the devices last synced or compared, and what the last sync moved. */
function syncSummary(peer: PairedDevice): string {
  const parts: string[] = [];
  if (peer.last_sync) {
    const received = moved(peer.last_result?.received);
    const sent = moved(peer.last_result?.sent);
    const deleted = moved(peer.last_result?.deleted);
    const refused = peer.last_result?.deletions_refused ?? 0;
    const moves = [received && `received ${received}`, sent && `sent ${sent}`,
      deleted && `deleted ${deleted} the other device deleted`,
      refused > 0 && `kept ${count.format(refused)} memories the other device deleted: too many at once`].filter(Boolean);
    parts.push(`last sync ${ago(peer.last_sync)}` + (moves.length ? `: ${moves.join("; ")}` : ""));
  }
  if (peer.last_checked && (!peer.last_sync || peer.last_checked > peer.last_sync + 1)) {
    parts.push(`up to date, checked ${ago(peer.last_checked)}`);
  }
  if (!parts.length && !peer.activity) parts.push("not synced yet");
  return parts.map((part) => ` · ${part}`).join("");
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

const WARN = "border-warning/30 bg-warning/10 text-warning";

type Show = "all" | "attention" | "active" | "paused";
const SHOWS: { value: Show; label: string }[] = [
  { value: "all", label: "All" }, { value: "attention", label: "Needs attention" },
  { value: "active", label: "Active" }, { value: "paused", label: "Paused" },
];
/** Pairings per page; a short list shows no pager or filters. */
const PER_PAGE = 8;

/** Removal pending, not seen for a week, or the last connection failed. */
function needsAttention(peer: PairedDevice) {
  return peer.state === "forget_pending" || unseenDays(peer) >= STALE_DAYS || !!peer.last_error;
}

function State({ peer }: { peer: PairedDevice }) {
  const [tone, text] =
    peer.state === "forget_pending"
      ? [WARN, "Removal pending"]
      : peer.enabled
        ? ["border-success/30 bg-success/10 text-success", "Active"]
        : ["border-border bg-muted text-foreground", "Paused"];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${tone}`}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {text}
    </span>
  );
}

const NO_PEERS: PairedDevice[] = [];

export function PairedDevices({ onPair }: { onPair?: () => void } = {}) {
  // Pairings are this installation's, in every workspace. The last list shows
  // at once while it is read again (ADR 0152).
  const list = useCached("paired-devices", getPairedDevices, { global: true, revalidateOnJobs: false });
  const peers = list.data ?? NO_PEERS;
  // Whether the list was ever read: a failed read says nothing about pairings.
  const loaded = list.data !== undefined;
  const [actionError, setError] = useState("");
  const error = actionError || (list.error ? list.error.message : "");
  // The change under way, so its button shows progress and the others wait.
  const [pending, setPending] = useState<{ id: string; action: PairedDeviceAction } | null>(null);
  // The question the confirmation dialog asks, kept while it animates closed.
  const [confirm, setConfirm] = useState<{ peer: PairedDevice; action: keyof typeof CONFIRM } | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const asked = CONFIRM[confirm?.action ?? "forget"];
  const [query, setQuery] = useState("");
  const [show, setShow] = useState<Show>("all");
  const [page, setPage] = useState(0);
  const busy = pending !== null;

  function ask(peer: PairedDevice, action: keyof typeof CONFIRM) {
    setConfirm({ peer, action }); setConfirmOpen(true);
  }

  // Faster while a sync runs, so its progress moves on screen.
  const syncing = peers.some((peer) => peer.activity);

  const { refresh, mutate } = list;
  useEffect(() => {
    const timer = setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, syncing ? 2000 : 5000);
    return () => clearInterval(timer);
  }, [syncing, refresh]);

  async function change(peer: PairedDevice, action: PairedDeviceAction) {
    setPending({ id: peer.id, action }); setError("");
    try {
      await controlPairedDevice(peer.id, action, !peer.enabled);
      mutate(await getPairedDevices());
      setConfirmOpen(false);
      const host = peer.peer.hostname;
      toast.success(action === "forget" ? `Forgot ${host} on this device`
        : action === "remove" ? `Removed ${host} from this device`
          : peer.enabled ? `Paused automatic sync with ${host}` : `Resumed automatic sync with ${host}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally { setPending(null); }
  }

  const hosts = new Set(peers.map((peer) => peer.peer.fingerprint || peer.peer.hostname)).size;
  const attention = peers.filter(needsAttention).length;
  const needle = query.trim().toLowerCase();
  const filtered = peers.filter((peer) => (
    (show === "all" || (show === "attention" ? needsAttention(peer) : show === "paused" ? !peer.enabled && peer.state !== "forget_pending" : peer.enabled && peer.state !== "forget_pending"))
    && (!needle || [peer.peer.hostname, peer.workspace, peer.remote_workspace, peer.relay, peer.peer.fingerprint]
      .some((field) => field?.toLowerCase().includes(needle)))
  ));
  const pages = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
  const current = Math.min(page, pages - 1);
  const shown = filtered.slice(current * PER_PAGE, (current + 1) * PER_PAGE);
  const controls = peers.length > PER_PAGE || needle || show !== "all";

  return (
    <section aria-labelledby="paired-devices-title" className="@container/page overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
      <div className="flex flex-wrap items-start gap-x-4 gap-y-2 border-b border-border px-4 py-4 sm:px-6">
        <div className="w-full min-w-0 sm:w-auto sm:flex-1">
          <h3 id="paired-devices-title" className="font-semibold">Paired devices</h3>
          <p className="mt-0.5 max-w-2xl text-xs text-muted-foreground">
            Paired devices keep a workspace in step on their own: while Imprint runs on both, they reconnect through the
            same relay and merge what changed about once a minute.
          </p>
          <Collapsible>
            <CollapsibleTrigger render={<Button variant="link" size="xs" className="h-auto px-0 text-xs font-normal text-muted-foreground" />}>
              Pausing, forgetting and removing
            </CollapsibleTrigger>
            <CollapsibleContent>
              <p className="mt-1 max-w-2xl text-xs text-muted-foreground">
                Pausing either side stops transfers. Forgetting a device keeps the memories it already shared. A device
                that is gone for good, uninstalled or reset, can be removed from this one alone.
              </p>
            </CollapsibleContent>
          </Collapsible>
        </div>
        {loaded && peers.length > 0 && (
          <p className="text-xs text-muted-foreground tabular-nums" aria-label="Pairings">
            {count.format(peers.length)} {peers.length === 1 ? "pairing" : "pairings"} · {count.format(hosts)} {hosts === 1 ? "device" : "devices"}
            {attention > 0 && <span className="ml-1.5 text-warning">· {count.format(attention)} need attention</span>}
          </p>
        )}
        {onPair && loaded && peers.length > 0 && (
          <Button variant="outline" size="sm" onClick={onPair}><Link2 /> Pair a device</Button>
        )}
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
          {onPair && (
            <EmptyContent>
              <Button variant="outline" size="sm" onClick={onPair}><Link2 /> Pair a device</Button>
            </EmptyContent>
          )}
        </Empty>
      ) : (
        <>
        {controls && (
          <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5 sm:px-6">
            <InputGroup className="w-full sm:w-64">
              <InputGroupAddon><Search aria-hidden /></InputGroupAddon>
              <InputGroupInput aria-label="Search paired devices" placeholder="Device, workspace or relay" value={query}
                onChange={(e) => { setQuery(e.target.value); setPage(0); }} />
            </InputGroup>
            <ToggleGroup aria-label="Show" size="sm" variant="outline" value={[show]} className="flex-wrap"
              onValueChange={(value) => { if (value[0]) { setShow(value[0] as Show); setPage(0); } }}>
              {SHOWS.map((option) => (
                <ToggleGroupItem key={option.value} value={option.value}
                  className="text-muted-foreground aria-pressed:border-foreground/30 aria-pressed:text-foreground">
                  {option.label}
                  {option.value === "attention" && attention > 0 && <span className="tabular-nums">{attention}</span>}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>
        )}
        {!filtered.length && (
          <p className="px-4 py-8 text-center text-sm text-muted-foreground sm:px-6">
            No pairing matches.{" "}
            <Button variant="link" size="sm" className="h-auto p-0" onClick={() => { setQuery(""); setShow("all"); setPage(0); }}>Show all</Button>
          </p>
        )}
        <ul className="divide-y divide-border">
          {shown.map((peer) => {
            const direction = DIRECTION[peer.direction] ?? DIRECTION.both;
            const Arrow = direction.icon;
            const removing = peer.state === "forget_pending";
            const unseen = unseenDays(peer);
            const stale = unseen >= STALE_DAYS;
            // Point to local removal when the peer may never answer again.
            const gone = removing || stale;
            return (
              <li key={peer.id} className="flex items-start gap-3 px-4 py-4 sm:gap-4 sm:px-6">
                <span className="hidden size-9 shrink-0 place-items-center rounded-lg bg-muted sm:grid">
                  <MonitorSmartphone className="size-4" aria-hidden />
                </span>
                <div className="min-w-0 flex-1 space-y-1.5">
                  <p className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{peer.peer.hostname}</span>
                    <span className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">{peer.peer.fingerprint}</span>
                    <State peer={peer} />
                    {stale && (
                      <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-medium ${WARN}`}>
                        <AlertTriangle className="size-3" aria-hidden /> Not seen for {unseen} days
                      </span>
                    )}
                  </p>
                  <p className="flex flex-wrap items-center gap-1.5 text-sm">
                    <span className="inline-flex min-w-0 items-center gap-1.5">
                      <span className="truncate font-mono">{peer.workspace}</span>
                      <Arrow className="size-3.5 shrink-0 text-muted-foreground" aria-label={direction.label} />
                      <span className="truncate font-mono">{peer.remote_workspace}</span>
                    </span>
                    <span className="text-xs text-muted-foreground">· {direction.label} · {peer.relay.replace(/^wss?:\/\//, "")}</span>
                  </p>
                  <p className="text-xs text-muted-foreground" role="status">
                    {removing
                      ? "Revoked locally. Remote removal pending: keep Imprint running on both devices until they reconnect."
                      : peer.enabled ? "Automatic sync enabled" : "Automatic sync paused on this device"}
                    {!removing && syncSummary(peer)}
                  </p>
                  <SyncProgress peer={peer} />
                  {peer.last_error && <p className="text-xs text-muted-foreground">Last connection: {peer.last_error}</p>}
                  {gone && (
                    <p className="flex items-start gap-1.5 text-xs text-warning">
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
        {pages > 1 && (
          <div className="border-t border-border px-4 pb-2 sm:px-6">
            <Pager page={current} pages={pages} size={PER_PAGE} shown={shown.length} total={filtered.length} onPage={setPage} />
          </div>
        )}
        </>
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
