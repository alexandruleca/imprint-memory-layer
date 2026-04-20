"use client";

import { useCallback, useRef, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Spinner } from "@/components/loaders";
import { SyncProgressGroup } from "@/components/sync-progress";
import {
  downloadExport,
  uploadImport,
  streamSyncServe,
  streamSyncReceive,
  cancelSync,
  approveSync,
  type SyncApprovalDecision,
} from "@/lib/sync-api";
import type { SyncEvent, SyncStats, DatasetProgress } from "@/lib/types";
import {
  Download,
  Upload,
  Copy,
  Check,
  Radio,
  Plug,
  MonitorSmartphone,
  ShieldCheck,
  ShieldQuestionMark,
  X,
} from "lucide-react";

// ── Helpers ────────────────────────────────────────────────────

function formatStats(s: SyncStats | undefined): string {
  if (!s) return "";
  const parts: string[] = [];
  if (s.memories) {
    parts.push(`${s.memories.inserted} memories inserted, ${s.memories.skipped} skipped`);
  }
  if (s.facts) {
    parts.push(`${s.facts.inserted} facts inserted, ${s.facts.skipped} skipped`);
  }
  return parts.join(" · ");
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    waiting: "Waiting for peer...",
    connected: "Connected to relay",
    handshake_sent: "Authenticating...",
    handshake_ok: "Authenticated",
    sending: "Sending data...",
    send_complete: "Send complete",
    receiving: "Receiving data...",
    storing: "Storing records...",
    pulling: "Pulling remote data...",
    pushing: "Pushing local data...",
  };
  return labels[status] || status;
}

function statusColor(status: string): string {
  if (status === "waiting") return "border-amber-500/50 text-amber-400";
  if (status === "connected" || status === "handshake_ok") return "border-green-500/50 text-green-400";
  if (status.includes("ing")) return "border-blue-500/50 text-blue-400";
  return "border-muted-foreground/50 text-muted-foreground";
}

// ── Copy button ────────────────────────────────────────────────

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      className="p-1 rounded hover:bg-muted transition-colors"
      title="Copy"
    >
      {copied ? (
        <Check className="w-3.5 h-3.5 text-green-400" />
      ) : (
        <Copy className="w-3.5 h-3.5 text-muted-foreground" />
      )}
    </button>
  );
}

// ── Button helper ──────────────────────────────────────────────

function Btn({
  onClick,
  disabled,
  variant = "primary",
  children,
  className = "",
}: {
  onClick: () => void;
  disabled?: boolean;
  variant?: "primary" | "outline" | "destructive";
  children: React.ReactNode;
  className?: string;
}) {
  const base = "px-4 py-2 rounded-md text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-2";
  const variants = {
    primary: "bg-primary text-primary-foreground hover:bg-primary/90",
    outline: "border border-input bg-background hover:bg-muted",
    destructive: "bg-destructive text-destructive-foreground hover:bg-destructive/90",
  };
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`${base} ${variants[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

// ── Export card ─────────────────────────────────────────────────

function ExportCard() {
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [message, setMessage] = useState("");

  async function doExport() {
    setState("loading");
    setMessage("");
    try {
      await downloadExport();
      setState("done");
      setMessage("Download started");
    } catch (e: unknown) {
      setState("error");
      setMessage(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm flex items-center gap-2">
          <Download className="w-4 h-4" /> Export Snapshot
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          Creates a Qdrant snapshot + knowledge graph backup and downloads it as a zip file.
        </p>
        <Btn onClick={doExport} disabled={state === "loading"}>
          {state === "loading" ? (
            <>
              <Spinner className="w-4 h-4" /> Exporting...
            </>
          ) : (
            <>
              <Download className="w-4 h-4" /> Download Snapshot
            </>
          )}
        </Btn>
        {message && (
          <p className={`text-xs ${state === "error" ? "text-destructive" : "text-green-400"}`}>
            {message}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ── Import card ────────────────────────────────────────────────

function ImportCard() {
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [message, setMessage] = useState("");
  const [filename, setFilename] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const fileObjRef = useRef<File | null>(null);

  function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (f) {
      fileObjRef.current = f;
      setFilename(f.name);
      setState("idle");
      setMessage("");
    }
  }

  async function doImport() {
    if (!fileObjRef.current) return;
    setState("loading");
    setMessage("");
    try {
      await uploadImport(fileObjRef.current);
      setState("done");
      setMessage("Import complete. Memories restored.");
      fileObjRef.current = null;
      setFilename("");
      if (fileRef.current) fileRef.current.value = "";
    } catch (e: unknown) {
      setState("error");
      setMessage(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm flex items-center gap-2">
          <Upload className="w-4 h-4" /> Import Snapshot
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          Restore a snapshot bundle from another device. Select a .zip export file.
        </p>
        <input
          ref={fileRef}
          type="file"
          accept=".zip"
          onChange={onFileChange}
          className="sr-only"
        />
        <div className="flex items-center gap-2">
          <Btn variant="outline" onClick={() => fileRef.current?.click()}>
            Choose File
          </Btn>
          {filename && (
            <span className="text-xs text-muted-foreground truncate max-w-[200px]">
              {filename}
            </span>
          )}
        </div>
        <Btn
          onClick={doImport}
          disabled={!filename || state === "loading"}
        >
          {state === "loading" ? (
            <>
              <Spinner className="w-4 h-4" /> Importing...
            </>
          ) : (
            <>
              <Upload className="w-4 h-4" /> Import
            </>
          )}
        </Btn>
        {message && (
          <p className={`text-xs ${state === "error" ? "text-destructive" : "text-green-400"}`}>
            {message}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ── Serve panel ────────────────────────────────────────────────

type Peer = { hostname: string; os: string; fingerprint: string; user: string };

type ServeState =
  | { phase: "idle" }
  | { phase: "waiting"; roomId: string; pin: string; sessionId: string }
  | { phase: "awaiting_approval"; roomId: string; pin: string; sessionId: string; peer: Peer; submitting: boolean }
  | { phase: "connected"; roomId: string; pin: string; sessionId: string; peer: Peer }
  | { phase: "syncing"; sessionId: string; status: string; datasets: Record<string, DatasetProgress> }
  | { phase: "done"; pullStats?: SyncStats; pushStats?: SyncStats }
  | { phase: "error"; message: string };

function ServePanel() {
  const [state, setState] = useState<ServeState>({ phase: "idle" });
  const ctrlRef = useRef<AbortController | null>(null);

  const start = useCallback(() => {
    const datasets: Record<string, DatasetProgress> = {};
    let sessionId = "";
    let roomId = "";
    let pin = "";
    let peer: Peer = { hostname: "", os: "", fingerprint: "", user: "" };
    let pullStats: SyncStats | undefined;
    let pushStats: SyncStats | undefined;

    const ctrl = streamSyncServe((ev: SyncEvent) => {
      switch (ev.type) {
        case "room":
          roomId = ev.room_id || "";
          pin = ev.pin || "";
          sessionId = ev.session_id || "";
          setState({ phase: "waiting", roomId, pin, sessionId });
          break;
        case "status":
          if (ev.status === "waiting") {
            setState({ phase: "waiting", roomId, pin, sessionId });
          } else {
            setState({ phase: "syncing", sessionId, status: ev.status || "", datasets: { ...datasets } });
          }
          break;
        case "approval_required":
          peer = {
            hostname: ev.hostname || "",
            os: ev.os || "",
            fingerprint: ev.fingerprint || "",
            user: ev.user || "",
          };
          setState({ phase: "awaiting_approval", roomId, pin, sessionId, peer, submitting: false });
          break;
        case "auto_accepted":
          peer = {
            hostname: ev.hostname || "",
            os: ev.os || "",
            fingerprint: ev.fingerprint || "",
            user: ev.user || "",
          };
          setState({ phase: "connected", roomId, pin, sessionId, peer });
          break;
        case "peer_connected":
          peer = {
            hostname: ev.hostname || "",
            os: ev.os || "",
            fingerprint: ev.fingerprint || "",
            user: ev.user || "",
          };
          setState({ phase: "connected", roomId, pin, sessionId, peer });
          break;
        case "warning":
          // non-fatal; surfaced at the console for now.
          // eslint-disable-next-line no-console
          console.warn("sync warning:", ev.message);
          break;
        case "progress":
          datasets[ev.dataset || ""] = {
            dataset: ev.dataset || "",
            done: ev.done || 0,
            total: ev.total || 0,
          };
          setState({ phase: "syncing", sessionId, status: ev.phase || "", datasets: { ...datasets } });
          break;
        case "push_complete":
          pushStats = ev.stats;
          break;
        case "pull_complete":
          pullStats = ev.stats;
          break;
        case "done":
          setState({ phase: "done", pullStats, pushStats });
          break;
        case "cancelled":
          setState({ phase: "idle" });
          break;
        case "error":
          setState({ phase: "error", message: ev.message || "Unknown error" });
          break;
      }
    });
    ctrlRef.current = ctrl;
  }, []);

  function stop() {
    ctrlRef.current?.abort();
    ctrlRef.current = null;
    if (
      state.phase === "waiting" ||
      state.phase === "awaiting_approval" ||
      state.phase === "connected" ||
      state.phase === "syncing"
    ) {
      cancelSync((state as { sessionId: string }).sessionId);
    }
    setState({ phase: "idle" });
  }

  function reset() {
    setState({ phase: "idle" });
  }

  async function decide(decision: SyncApprovalDecision) {
    if (state.phase !== "awaiting_approval" || state.submitting) return;
    const { sessionId } = state;
    setState({ ...state, submitting: true });
    try {
      await approveSync(sessionId, decision);
      // Server will emit peer_connected (or error on reject) via SSE.
    } catch (e) {
      setState({ phase: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }

  return (
    <Card>
      <CardContent className="py-5 space-y-4">
        {state.phase === "idle" && (
          <>
            <p className="text-xs text-muted-foreground">
              Expose this machine&apos;s knowledge base for another device to sync with.
              A room ID and PIN will be generated to share.
            </p>
            <Btn onClick={start}>
              <Radio className="w-4 h-4" /> Start Serving
            </Btn>
          </>
        )}

        {state.phase === "waiting" && (
          <>
            <div className="flex items-center gap-2 mb-2">
              <Spinner className="w-4 h-4 text-amber-500" />
              <Badge variant="outline" className={statusColor("waiting")}>
                Waiting for peer...
              </Badge>
            </div>
            <div className="rounded-lg border border-dashed border-muted-foreground/30 p-4 space-y-3">
              <p className="text-xs text-muted-foreground font-medium">
                Share these with the other device:
              </p>
              <div className="flex items-center gap-3">
                <span className="text-xs text-muted-foreground w-16">Room ID</span>
                <code className="font-mono text-sm bg-muted px-2 py-1 rounded">{state.roomId}</code>
                <CopyButton text={state.roomId} />
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs text-muted-foreground w-16">PIN</span>
                <code className="font-mono text-sm bg-muted px-2 py-1 rounded">{state.pin}</code>
                <CopyButton text={state.pin} />
              </div>
            </div>
            <Btn variant="outline" onClick={stop}>
              <X className="w-4 h-4" /> Stop
            </Btn>
          </>
        )}

        {state.phase === "awaiting_approval" && (
          <>
            <div className="flex items-center gap-2 mb-2">
              <ShieldQuestionMark className="w-4 h-4 text-amber-400" />
              <Badge variant="outline" className="border-amber-500/50 text-amber-400">
                Incoming Sync Request
              </Badge>
            </div>
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 space-y-2">
              <div className="flex items-center gap-2">
                <MonitorSmartphone className="w-4 h-4 text-muted-foreground" />
                <span className="text-sm font-medium">{state.peer.hostname || "Unknown device"}</span>
                {state.peer.user && (
                  <span className="text-xs text-muted-foreground">· {state.peer.user}</span>
                )}
                <span className="text-xs text-muted-foreground">· {state.peer.os}</span>
              </div>
              <p className="text-xs text-muted-foreground font-mono pl-6">
                fingerprint: {state.peer.fingerprint || "unknown"}
              </p>
              <p className="text-xs text-muted-foreground pl-6">
                PIN was verified. Approve to let this device read + write your memories.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Btn
                onClick={() => decide("accept")}
                disabled={state.submitting}
              >
                <Check className="w-4 h-4" /> Accept once
              </Btn>
              <Btn
                variant="outline"
                onClick={() => decide("trust")}
                disabled={state.submitting}
              >
                <ShieldCheck className="w-4 h-4" /> Trust & accept
              </Btn>
              <Btn
                variant="outline"
                onClick={() => decide("reject")}
                disabled={state.submitting}
                className="text-destructive"
              >
                <X className="w-4 h-4" /> Reject
              </Btn>
            </div>
            {state.submitting && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Spinner className="w-3 h-3" /> Sending decision…
              </div>
            )}
          </>
        )}

        {state.phase === "connected" && (
          <>
            <div className="flex items-center gap-2 mb-2">
              <Badge variant="outline" className="border-green-500/50 text-green-400">
                Peer Connected
              </Badge>
            </div>
            <div className="rounded-lg border border-muted-foreground/20 p-3 space-y-1">
              <div className="flex items-center gap-2">
                <MonitorSmartphone className="w-4 h-4 text-muted-foreground" />
                <span className="text-sm font-medium">{state.peer.hostname || "Unknown"}</span>
                <span className="text-xs text-muted-foreground">{state.peer.os}</span>
              </div>
              <p className="text-xs text-muted-foreground font-mono pl-6">{state.peer.fingerprint}</p>
            </div>
            <Spinner className="w-4 h-4 text-blue-400" />
          </>
        )}

        {state.phase === "syncing" && (
          <>
            <div className="flex items-center gap-2 mb-2">
              <Spinner className="w-4 h-4 text-blue-400" />
              <Badge variant="outline" className={statusColor(state.status)}>
                {statusLabel(state.status)}
              </Badge>
            </div>
            <SyncProgressGroup datasets={state.datasets} />
            <Btn variant="outline" onClick={stop}>
              <X className="w-4 h-4" /> Cancel
            </Btn>
          </>
        )}

        {state.phase === "done" && (
          <>
            <div className="flex items-center gap-2 mb-2">
              <Check className="w-4 h-4 text-green-400" />
              <Badge variant="outline" className="border-green-500/50 text-green-400">
                Sync Complete
              </Badge>
            </div>
            {state.pullStats && (
              <p className="text-xs text-muted-foreground">
                <span className="font-medium">Received:</span> {formatStats(state.pullStats)}
              </p>
            )}
            {state.pushStats && (
              <p className="text-xs text-muted-foreground">
                <span className="font-medium">Sent:</span> {formatStats(state.pushStats)}
              </p>
            )}
            <Btn variant="outline" onClick={reset}>
              New Session
            </Btn>
          </>
        )}

        {state.phase === "error" && (
          <>
            <div className="flex items-center gap-2 mb-2">
              <X className="w-4 h-4 text-destructive" />
              <Badge variant="outline" className="border-destructive/50 text-destructive">
                Error
              </Badge>
            </div>
            <p className="text-xs text-destructive">{state.message}</p>
            <Btn variant="outline" onClick={reset}>
              Retry
            </Btn>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ── Receive panel ──────────────────────────────────────────────

type ReceiveState =
  | { phase: "idle" }
  | { phase: "pin_prompt"; roomId: string }
  | { phase: "connecting" }
  | { phase: "handshake" }
  | { phase: "syncing"; sessionId: string; status: string; datasets: Record<string, DatasetProgress> }
  | { phase: "done"; pullStats?: SyncStats; pushStats?: SyncStats }
  | { phase: "error"; message: string };

function ReceivePanel() {
  const [state, setState] = useState<ReceiveState>({ phase: "idle" });
  const [roomIdInput, setRoomIdInput] = useState("");
  const [pinInput, setPinInput] = useState("");
  const ctrlRef = useRef<AbortController | null>(null);

  function promptPin() {
    const rid = roomIdInput.trim();
    if (!rid) return;
    setState({ phase: "pin_prompt", roomId: rid });
    setPinInput("");
  }

  function connect() {
    const rid = (state as { roomId: string }).roomId;
    const pin = pinInput.trim();
    if (!rid || !pin) return;

    setState({ phase: "connecting" });

    const datasets: Record<string, DatasetProgress> = {};
    let sessionId = "";
    let pullStats: SyncStats | undefined;
    let pushStats: SyncStats | undefined;

    const ctrl = streamSyncReceive(rid, pin, (ev: SyncEvent) => {
      switch (ev.type) {
        case "session":
          sessionId = ev.session_id || "";
          break;
        case "status":
          if (ev.status === "connected" || ev.status === "handshake_sent") {
            setState({ phase: "handshake" });
          } else if (ev.status === "handshake_ok") {
            setState({ phase: "syncing", sessionId, status: "handshake_ok", datasets: {} });
          } else {
            setState({ phase: "syncing", sessionId, status: ev.status || "", datasets: { ...datasets } });
          }
          break;
        case "progress":
          datasets[ev.dataset || ""] = {
            dataset: ev.dataset || "",
            done: ev.done || 0,
            total: ev.total || 0,
          };
          setState({ phase: "syncing", sessionId, status: ev.phase || "", datasets: { ...datasets } });
          break;
        case "pull_complete":
          pullStats = ev.stats;
          break;
        case "push_complete":
          pushStats = ev.stats;
          break;
        case "done":
          setState({
            phase: "done",
            pullStats: ev.pull_stats || pullStats,
            pushStats: ev.push_stats || pushStats,
          });
          break;
        case "cancelled":
          setState({ phase: "idle" });
          break;
        case "error":
          setState({ phase: "error", message: ev.message || "Unknown error" });
          break;
      }
    });
    ctrlRef.current = ctrl;
  }

  function stop() {
    ctrlRef.current?.abort();
    ctrlRef.current = null;
    if (state.phase === "syncing") {
      cancelSync(state.sessionId);
    }
    setState({ phase: "idle" });
  }

  function reset() {
    setRoomIdInput("");
    setPinInput("");
    setState({ phase: "idle" });
  }

  return (
    <Card>
      <CardContent className="py-5 space-y-4">
        {state.phase === "idle" && (
          <>
            <p className="text-xs text-muted-foreground">
              Connect to another device that is serving its knowledge base.
              Enter the room ID shown on the other machine.
            </p>
            <div className="flex items-center gap-2">
              <Input
                value={roomIdInput}
                onChange={(e) => setRoomIdInput(e.target.value)}
                placeholder="Room ID (e.g. a1b2c3d4)"
                className="text-sm font-mono max-w-[220px]"
                onKeyDown={(e) => e.key === "Enter" && promptPin()}
              />
              <Btn onClick={promptPin} disabled={!roomIdInput.trim()}>
                <Plug className="w-4 h-4" /> Connect
              </Btn>
            </div>
          </>
        )}

        {state.phase === "pin_prompt" && (
          <>
            <p className="text-xs text-muted-foreground">
              Enter the PIN shown on the serving device.
            </p>
            <div className="rounded-lg border border-dashed border-muted-foreground/30 p-4 space-y-3">
              <div className="flex items-center gap-3">
                <span className="text-xs text-muted-foreground w-16">Room</span>
                <code className="font-mono text-sm bg-muted px-2 py-1 rounded">{state.roomId}</code>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground w-16">PIN</span>
                <Input
                  value={pinInput}
                  onChange={(e) => setPinInput(e.target.value)}
                  placeholder="Enter PIN"
                  className="text-sm font-mono max-w-[180px]"
                  autoFocus
                  onKeyDown={(e) => e.key === "Enter" && connect()}
                />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Btn onClick={connect} disabled={!pinInput.trim()}>
                <Plug className="w-4 h-4" /> Authenticate
              </Btn>
              <Btn variant="outline" onClick={reset}>
                Cancel
              </Btn>
            </div>
          </>
        )}

        {state.phase === "connecting" && (
          <div className="flex items-center gap-2">
            <Spinner className="w-4 h-4 text-blue-400" />
            <span className="text-sm text-muted-foreground">Connecting to relay...</span>
          </div>
        )}

        {state.phase === "handshake" && (
          <div className="flex items-center gap-2">
            <Spinner className="w-4 h-4 text-blue-400" />
            <span className="text-sm text-muted-foreground">Authenticating...</span>
          </div>
        )}

        {state.phase === "syncing" && (
          <>
            <div className="flex items-center gap-2 mb-2">
              <Spinner className="w-4 h-4 text-blue-400" />
              <Badge variant="outline" className={statusColor(state.status)}>
                {statusLabel(state.status)}
              </Badge>
            </div>
            <SyncProgressGroup datasets={state.datasets} />
            <Btn variant="outline" onClick={stop}>
              <X className="w-4 h-4" /> Cancel
            </Btn>
          </>
        )}

        {state.phase === "done" && (
          <>
            <div className="flex items-center gap-2 mb-2">
              <Check className="w-4 h-4 text-green-400" />
              <Badge variant="outline" className="border-green-500/50 text-green-400">
                Sync Complete
              </Badge>
            </div>
            {state.pullStats && (
              <p className="text-xs text-muted-foreground">
                <span className="font-medium">Pulled:</span> {formatStats(state.pullStats)}
              </p>
            )}
            {state.pushStats && (
              <p className="text-xs text-muted-foreground">
                <span className="font-medium">Pushed:</span> {formatStats(state.pushStats)}
              </p>
            )}
            <Btn variant="outline" onClick={reset}>
              New Session
            </Btn>
          </>
        )}

        {state.phase === "error" && (
          <>
            <div className="flex items-center gap-2 mb-2">
              <X className="w-4 h-4 text-destructive" />
              <Badge variant="outline" className="border-destructive/50 text-destructive">
                Error
              </Badge>
            </div>
            <p className="text-xs text-destructive">{state.message}</p>
            <Btn variant="outline" onClick={reset}>
              Retry
            </Btn>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ── Page ───────────────────────────────────────────────────────

export default function SyncPage() {
  return (
    <div className="p-8 space-y-6 max-w-4xl">
      <div>
        <h2 className="text-2xl font-bold">Sync</h2>
        <p className="text-muted-foreground text-sm mt-1">
          Export and import snapshots, or sync live with another device via relay.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-6">
        <ExportCard />
        <ImportCard />
      </div>

      <Separator />

      <div>
        <h3 className="text-lg font-semibold mb-3">Live Sync</h3>
        <Tabs defaultValue="serve">
          <TabsList>
            <TabsTrigger value="serve">
              <Radio className="w-3.5 h-3.5 mr-1" /> Serve
            </TabsTrigger>
            <TabsTrigger value="receive">
              <Plug className="w-3.5 h-3.5 mr-1" /> Receive
            </TabsTrigger>
          </TabsList>

          <TabsContent value="serve">
            <ServePanel />
          </TabsContent>

          <TabsContent value="receive">
            <ReceivePanel />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
