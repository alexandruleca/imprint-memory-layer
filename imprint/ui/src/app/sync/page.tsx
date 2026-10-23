"use client";

import { useCallback, useId, useRef, useState } from "react";
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
import {
  getDesktopHistory,
  scanDesktopExports,
  type DesktopExportEntry,
  type DesktopScanResult,
} from "@/lib/desktop-learn";
import { getWorkspaces } from "@/lib/api";
import type { SyncDirection, SyncEvent, SyncStats, DatasetProgress } from "@/lib/types";
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
  RefreshCw,
  Inbox,
  FileArchive,
  X,
} from "lucide-react";
import { useEffect } from "react";

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
    // progress phases
    send: "Sending data...",
    push: "Sending data...",
    receive: "Receiving data...",
    pull: "Receiving data...",
    store: "Storing records...",
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

// ── Live-sync options ──────────────────────────────────────────

type Workspaces = { active: string; workspaces: string[] };

const DIRECTIONS: { value: SyncDirection; label: string; serve: string; receive: string }[] = [
  {
    value: "both",
    label: "Two-way",
    serve: "The other device can pull this workspace and push its memories into it.",
    receive: "Import the other device's memories, then send this workspace back. You see who receives it and confirm first.",
  },
  {
    value: "send",
    label: "Send only",
    serve: "The other device can pull this workspace. Anything it pushes is refused.",
    receive: "Send this workspace to the other device after you confirm who receives it. Nothing is imported here.",
  },
  {
    value: "receive",
    label: "Receive only",
    serve: "The other device can push into this workspace. Nothing here is sent.",
    receive: "Import the other device's memories. Nothing here is sent.",
  },
];


// Mirrors the CLI's "Mode:" line for the provider (cmd/sync.go mode()).
function modeText(pull: boolean, push: boolean): string {
  if (pull && push) return "Two-way — peers can pull and push";
  if (pull) return "Send only — peers can pull, pushes are refused";
  return "Receive only — peers can push, pulls are refused";
}

// Mirrors the CLI's "Plan:" line for the joining side (syncPlan.String()).
function planText(pull: boolean, push: boolean, skipped: string[] = []): string {
  const phases = pull && push ? "pull, then push" : pull ? "pull only" : "push only";
  return ["Plan: " + phases, ...skipped].join(" — ");
}

const WORKSPACE_NAME = /^[a-z0-9][a-z0-9-]*$/;

// Same rules as config.validate_workspace_name; the server re-checks.
function workspaceProblem(name: string, known: boolean, direction: SyncDirection): string {
  if (!name) return "Enter a workspace name.";
  if (name.length > 40) return "Use at most 40 characters.";
  if (!WORKSPACE_NAME.test(name)) return "Use lowercase letters, digits and hyphens, starting with a letter or digit.";
  if (!known && direction === "send") return "Send only needs an existing workspace to send from.";
  return "";
}

function DirectionField({
  side,
  value,
  onChange,
}: {
  side: "serve" | "receive";
  value: SyncDirection;
  onChange: (direction: SyncDirection) => void;
}) {
  const name = useId();
  return (
    <fieldset>
      <legend className="text-xs font-medium mb-2">Direction</legend>
      <div className="grid gap-2 sm:grid-cols-3">
        {DIRECTIONS.map((option) => (
          <label
            key={option.value}
            className={`flex items-start gap-2 rounded-md border p-2.5 text-xs cursor-pointer has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-primary ${
              value === option.value ? "border-primary bg-primary/5" : "border-border hover:bg-muted/40"
            }`}
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
              className="mt-0.5 accent-primary"
            />
            <span>
              <span className="block font-medium">{option.label}</span>
              <span className="text-muted-foreground">{option[side]}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

// Live region for the negotiated plan, refusals and warnings.
function SessionNotes({ notes }: { notes: string[] }) {
  return (
    <div role="status" className="space-y-1 text-xs text-muted-foreground">
      {notes.map((note, i) => (
        <p key={i}>{note}</p>
      ))}
    </div>
  );
}

// ── Serve panel ────────────────────────────────────────────────

type Peer = { hostname: string; os: string; fingerprint: string; user: string };

type ServedSession = { workspace: string; pull: boolean; push: boolean };

type ServeState =
  | { phase: "idle" }
  | { phase: "waiting"; code: string; sessionId: string }
  | { phase: "awaiting_approval"; code: string; sessionId: string; peer: Peer; submitting: boolean }
  | { phase: "connected"; code: string; sessionId: string; peer: Peer }
  | { phase: "syncing"; sessionId: string; status: string; datasets: Record<string, DatasetProgress> }
  | { phase: "done"; receivedStats?: SyncStats }
  | { phase: "error"; message: string };

function approvalText(served: ServedSession | null): string {
  if (!served) return "Approve to let this device sync with you.";
  const ws = `workspace “${served.workspace}”`;
  if (served.pull && served.push) return `Approve to let this device read and write ${ws}.`;
  if (served.pull) return `Approve to let this device read ${ws}. Anything it pushes is refused.`;
  return `Approve to let this device write into ${ws}. Nothing is sent to it.`;
}

function refusalNote(request: SyncEvent["request"]): string {
  return request === "pull"
    ? "Refused the other device's pull: this session is receive-only, so nothing was sent."
    : "Refused the other device's push: this session is send-only, so nothing was imported.";
}

function ServePanel({ workspaces }: { workspaces: Workspaces }) {
  const [state, setState] = useState<ServeState>({ phase: "idle" });
  const [chosen, setChosen] = useState<string | null>(null);
  const [direction, setDirection] = useState<SyncDirection>("both");
  const [relay, setRelay] = useState("");
  const [served, setServed] = useState<ServedSession | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const ctrlRef = useRef<AbortController | null>(null);
  const workspaceId = useId();
  const relayId = useId();
  const workspace = chosen ?? workspaces.active;

  const start = useCallback(() => {
    const datasets: Record<string, DatasetProgress> = {};
    let sessionId = "";
    let code = "";
    let peer: Peer = { hostname: "", os: "", fingerprint: "", user: "" };
    let receivedStats: SyncStats | undefined;
    setServed(null);
    setNotes([]);
    const note = (text: string) => setNotes((current) => [...current, text]);

    const ctrl = streamSyncServe({ workspace, direction, relay: relay.trim() }, (ev: SyncEvent) => {
      switch (ev.type) {
        case "room":
          code = ev.target || "";
          sessionId = ev.session_id || "";
          setServed({ workspace: ev.workspace || workspace, pull: Boolean(ev.pull), push: Boolean(ev.push) });
          setState({ phase: "waiting", code, sessionId });
          break;
        case "status":
          if (ev.status === "waiting") {
            setState({ phase: "waiting", code, sessionId });
          } else {
            if (ev.status === "send_complete") note("Sent this workspace to the other device.");
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
          setState({ phase: "awaiting_approval", code, sessionId, peer, submitting: false });
          break;
        case "auto_accepted":
          peer = {
            hostname: ev.hostname || "",
            os: ev.os || "",
            fingerprint: ev.fingerprint || "",
            user: ev.user || "",
          };
          setState({ phase: "connected", code, sessionId, peer });
          break;
        case "peer_connected":
          peer = {
            hostname: ev.hostname || "",
            os: ev.os || "",
            fingerprint: ev.fingerprint || "",
            user: ev.user || "",
          };
          setState({ phase: "connected", code, sessionId, peer });
          break;
        case "refused":
          note(refusalNote(ev.request));
          break;
        case "warning":
          note(ev.message || "Warning");
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
          // The provider's push_complete reports what it stored from the peer.
          receivedStats = ev.stats;
          break;
        case "done":
          setState({ phase: "done", receivedStats });
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
  }, [workspace, direction, relay]);

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
              Expose one workspace for another device to sync with.
              A one-time code will be generated to share.
            </p>
            <div className="space-y-1">
              <label htmlFor={workspaceId} className="block text-xs font-medium">
                Workspace to serve
              </label>
              <select
                id={workspaceId}
                value={workspace}
                onChange={(e) => setChosen(e.target.value)}
                className="block h-8 w-full max-w-xs rounded-md border border-input bg-background px-2 text-sm"
              >
                {workspaces.workspaces.map((name) => (
                  <option key={name} value={name}>
                    {name}
                    {name === workspaces.active ? " (active)" : ""}
                  </option>
                ))}
              </select>
            </div>
            <DirectionField side="serve" value={direction} onChange={setDirection} />
            <div className="space-y-1">
              <label htmlFor={relayId} className="block text-xs font-medium">
                Relay <span className="font-normal text-muted-foreground">(optional)</span>
              </label>
              <Input
                id={relayId}
                value={relay}
                onChange={(e) => setRelay(e.target.value)}
                placeholder="public relay"
                className="text-sm font-mono max-w-xs"
              />
              <p className="text-xs text-muted-foreground">
                A self-hosted relay host, e.g. sync.example.com. Sessions are end-to-end encrypted either way.
              </p>
            </div>
            <Btn onClick={start} disabled={!workspace}>
              <Radio className="w-4 h-4" /> Start Serving
            </Btn>
          </>
        )}

        {state.phase !== "idle" && served && (
          <p className="text-xs text-muted-foreground">
            Serving workspace <span className="font-mono text-foreground">{served.workspace}</span>
            {" · "}
            {modeText(served.pull, served.push)}
          </p>
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
                Share this code with the other device:
              </p>
              <div className="flex items-center gap-3">
                <span className="text-xs text-muted-foreground w-16">Code</span>
                <code className="font-mono text-sm bg-muted px-2 py-1 rounded">{state.code}</code>
                <CopyButton text={state.code} />
              </div>
              <p className="text-xs text-muted-foreground">
                On the other machine: <code className="font-mono">imprint sync {state.code}</code>, or paste it
                in Sync → Receive.
              </p>
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
                The code was verified. {approvalText(served)}
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

        {state.phase !== "idle" && <SessionNotes notes={notes} />}

        {state.phase === "done" && (
          <>
            <div className="flex items-center gap-2 mb-2">
              <Check className="w-4 h-4 text-green-400" />
              <Badge variant="outline" className="border-green-500/50 text-green-400">
                Sync Complete
              </Badge>
            </div>
            {state.receivedStats && (
              <p className="text-xs text-muted-foreground">
                <span className="font-medium">Received:</span> {formatStats(state.receivedStats)}
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
  | { phase: "confirm_push"; sessionId: string; device: Peer; workspace: string; submitting: boolean }
  | { phase: "connecting" }
  | { phase: "handshake" }
  | { phase: "syncing"; sessionId: string; status: string; datasets: Record<string, DatasetProgress> }
  | { phase: "done"; pullStats?: SyncStats; pushStats?: SyncStats }
  | { phase: "error"; message: string };

function ReceivePanel({ workspaces }: { workspaces: Workspaces }) {
  const [state, setState] = useState<ReceiveState>({ phase: "idle" });
  const [codeInput, setCodeInput] = useState("");
  const [typed, setTyped] = useState<string | null>(null);
  // Joining only receives unless the user opts in to sending.
  const [direction, setDirection] = useState<SyncDirection>("receive");
  const [notes, setNotes] = useState<string[]>([]);
  const ctrlRef = useRef<AbortController | null>(null);
  const codeId = useId();
  const workspaceId = useId();

  const loading = typed === null && !workspaces.active;
  const workspace = (typed ?? workspaces.active).trim();
  const known = workspaces.workspaces.includes(workspace);
  const problem = loading ? "" : workspaceProblem(workspace, known, direction);

  function connect() {
    const code = codeInput.trim();
    if (!code || loading || problem) return;

    setState({ phase: "connecting" });
    setNotes([]);
    const note = (text: string) => setNotes((current) => [...current, text]);

    const datasets: Record<string, DatasetProgress> = {};
    let sessionId = "";
    let pullStats: SyncStats | undefined;
    let pushStats: SyncStats | undefined;

    const ctrl = streamSyncReceive(code, { workspace, direction }, (ev: SyncEvent) => {
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
        case "peer_connected":
          note(`Connected to ${ev.hostname || "the other device"} (id ${ev.fingerprint || "unknown"}).`);
          break;
        case "confirm_push":
          setState({
            phase: "confirm_push",
            sessionId,
            workspace: ev.workspace || workspace,
            device: { hostname: ev.hostname || "", os: ev.os || "", fingerprint: ev.fingerprint || "", user: ev.user || "" },
            submitting: false,
          });
          break;
        case "plan":
          note(planText(Boolean(ev.pull), Boolean(ev.push), ev.skipped));
          break;
        case "workspace_created":
          note(`Created workspace “${ev.workspace}”.`);
          break;
        case "warning":
          note(ev.message || "Warning");
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

  async function decidePush(decision: SyncApprovalDecision) {
    if (state.phase !== "confirm_push" || state.submitting) return;
    setState({ ...state, submitting: true });
    try {
      await approveSync(state.sessionId, decision);
    } catch (e) {
      setState({ phase: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }

  function stop() {
    ctrlRef.current?.abort();
    ctrlRef.current = null;
    if (state.phase === "syncing" || state.phase === "confirm_push") {
      cancelSync(state.sessionId);
    }
    setState({ phase: "idle" });
  }

  function reset() {
    setCodeInput("");
    setNotes([]);
    setState({ phase: "idle" });
  }

  return (
    <Card>
      <CardContent className="py-5 space-y-4">
        {state.phase === "idle" && (
          <>
            <p className="text-xs text-muted-foreground">
              Connect to another device that is serving a workspace. Enter the
              code shown on the other machine.
            </p>
            <div className="space-y-1">
              <label htmlFor={codeId} className="block text-xs font-medium">
                Code
              </label>
              <Input
                id={codeId}
                value={codeInput}
                onChange={(e) => setCodeInput(e.target.value)}
                placeholder="7f3a9c21-Ab3xY9Kq or wss://host/7f3a9c21-Ab3xY9Kq"
                autoComplete="off"
                className="text-sm font-mono max-w-sm"
                onKeyDown={(e) => e.key === "Enter" && connect()}
              />
            </div>
            <div className="space-y-1">
              <label htmlFor={workspaceId} className="block text-xs font-medium">
                Workspace on this device
              </label>
              <Input
                id={workspaceId}
                list={`${workspaceId}-known`}
                value={typed ?? workspaces.active}
                onChange={(e) => setTyped(e.target.value)}
                placeholder="workspace-name"
                aria-describedby={`${workspaceId}-hint`}
                aria-invalid={problem ? true : undefined}
                className="text-sm font-mono max-w-xs"
                onKeyDown={(e) => e.key === "Enter" && connect()}
              />
              <datalist id={`${workspaceId}-known`}>
                {workspaces.workspaces.map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
              <p
                id={`${workspaceId}-hint`}
                className={`text-xs ${problem ? "text-destructive" : "text-muted-foreground"}`}
              >
                {problem ||
                  (loading
                    ? "Loading workspaces…"
                    : known
                      ? `Syncs “${workspace}”${workspace === workspaces.active ? " (active)" : ""}. The active workspace is not changed.`
                      : `New workspace — created when the other device's memories arrive.`)}
              </p>
            </div>
            <DirectionField side="receive" value={direction} onChange={setDirection} />
            <Btn onClick={connect} disabled={!codeInput.trim() || loading || Boolean(problem)}>
              <Plug className="w-4 h-4" /> Connect
            </Btn>
          </>
        )}

        {state.phase === "confirm_push" && (
          <>
            <div className="flex items-center gap-2 mb-2">
              <ShieldQuestionMark className="w-4 h-4 text-amber-400" />
              <Badge variant="outline" className="border-amber-500/50 text-amber-400">
                Send this workspace?
              </Badge>
            </div>
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 space-y-2">
              <p className="text-sm">
                Send <span className="font-mono">{state.workspace}</span> to this device?
              </p>
              <div className="flex items-center gap-2">
                <MonitorSmartphone className="w-4 h-4 text-muted-foreground" />
                <span className="text-sm font-medium">{state.device.hostname || "Unknown device"}</span>
                {state.device.user && (
                  <span className="text-xs text-muted-foreground">· {state.device.user}</span>
                )}
                <span className="text-xs text-muted-foreground">· {state.device.os}</span>
              </div>
              <p className="text-xs text-muted-foreground font-mono pl-6">
                fingerprint: {state.device.fingerprint || "unknown"}
              </p>
              <p className="text-xs text-muted-foreground pl-6">
                This is the device that served the code. If you do not recognise it, do not send:
                the pull still runs.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Btn onClick={() => decidePush("accept")} disabled={state.submitting}>
                <Check className="w-4 h-4" /> Send
              </Btn>
              <Btn variant="outline" onClick={() => decidePush("reject")} disabled={state.submitting}>
                <X className="w-4 h-4" /> Don&apos;t send
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

        {state.phase !== "idle" && <SessionNotes notes={notes} />}

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

// ── Desktop exports panel ──────────────────────────────────────

function relativeTime(ts: number): string {
  const diff = Math.floor(Date.now() / 1000) - ts;
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function originBadge(origin: string) {
  const map: Record<string, string> = {
    claude: "border-indigo-500/50 text-indigo-400",
    chatgpt: "border-emerald-500/50 text-emerald-400",
    unknown: "border-muted-foreground/40 text-muted-foreground",
  };
  return map[origin] || map.unknown;
}

function DesktopExportsCard() {
  const [history, setHistory] = useState<DesktopExportEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [lastScan, setLastScan] = useState<DesktopScanResult | null>(null);
  const [error, setError] = useState("");

  const reloadHistory = useCallback(async () => {
    try {
      const h = await getDesktopHistory();
      const entries = Object.values(h.seen).sort(
        (a, b) => b.indexed_at - a.indexed_at,
      );
      setHistory(entries);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    getDesktopHistory().then((result) => {
      if (!cancelled) setHistory(Object.values(result.seen).sort((a, b) => b.indexed_at - a.indexed_at));
    }).catch((error) => { if (!cancelled) setError(String(error)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  async function scan() {
    setScanning(true);
    setError("");
    try {
      const res = await scanDesktopExports();
      setLastScan(res);
      await reloadHistory();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setScanning(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm flex items-center gap-2">
          <Inbox className="w-4 h-4" /> Desktop App Exports
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-xs text-muted-foreground">
          Auto-ingest Claude Desktop / ChatGPT Desktop conversation exports from
          your Downloads folder. Request an export in the vendor&apos;s app
          (Settings → Export data), drop the zip in Downloads, then scan —
          already-indexed zips are skipped by SHA.
        </p>

        <div className="flex items-center gap-2">
          <Btn onClick={scan} disabled={scanning}>
            {scanning ? (
              <>
                <Spinner className="w-4 h-4" /> Scanning…
              </>
            ) : (
              <>
                <RefreshCw className="w-4 h-4" /> Scan Downloads now
              </>
            )}
          </Btn>
          <Btn variant="outline" onClick={reloadHistory} disabled={loading}>
            <RefreshCw className="w-3.5 h-3.5" /> Refresh history
          </Btn>
        </div>

        {error && (
          <p className="text-xs text-destructive">{error}</p>
        )}

        {lastScan && (
          <div className="rounded-lg border border-muted-foreground/20 p-3 text-xs space-y-1 font-mono">
            <div className="flex gap-3">
              <span className="text-muted-foreground w-36">Roots scanned</span>
              <span>{lastScan.roots.length}</span>
            </div>
            <div className="flex gap-3">
              <span className="text-muted-foreground w-36">Candidates seen</span>
              <span>{lastScan.scanned}</span>
            </div>
            <div className="flex gap-3">
              <span className="text-muted-foreground w-36">Skipped (SHA match)</span>
              <span>{lastScan.skipped_seen}</span>
            </div>
            <div className="flex gap-3">
              <span className="text-muted-foreground w-36">Newly indexed</span>
              <span className={lastScan.indexed_zips > 0 ? "text-green-400" : ""}>
                {lastScan.indexed_zips}
              </span>
            </div>
            <div className="flex gap-3">
              <span className="text-muted-foreground w-36">Chunks stored</span>
              <span>{lastScan.inserted_chunks}</span>
            </div>
          </div>
        )}

        <div>
          <div className="text-xs font-medium mb-2 flex items-center gap-2">
            <FileArchive className="w-3.5 h-3.5" />
            Indexed exports
            <span className="text-muted-foreground">· {history.length}</span>
          </div>
          {loading && history.length === 0 ? (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Spinner className="w-3 h-3" /> Loading…
            </div>
          ) : history.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              None yet. Export from claude.ai or chat.openai.com, then click
              <span className="font-mono"> Scan Downloads now</span>.
            </p>
          ) : (
            <ul className="space-y-1.5 max-h-64 overflow-y-auto">
              {history.map((e) => {
                const fileName = e.path.split("/").pop() || e.path;
                return (
                  <li
                    key={e.path + e.indexed_at}
                    className="flex items-center gap-2 text-xs rounded border border-muted-foreground/15 px-2 py-1.5"
                  >
                    <Badge variant="outline" className={originBadge(e.origin)}>
                      {e.origin}
                    </Badge>
                    <span className="font-mono truncate flex-1" title={e.path}>
                      {fileName}
                    </span>
                    <span className="text-muted-foreground tabular-nums">
                      {e.chunks} chunks
                    </span>
                    <span className="text-muted-foreground tabular-nums min-w-[52px] text-right">
                      {relativeTime(e.indexed_at)}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Page ───────────────────────────────────────────────────────

export default function SyncPage() {
  const [workspaces, setWorkspaces] = useState<Workspaces>({ active: "", workspaces: [] });

  useEffect(() => {
    let cancelled = false;
    getWorkspaces().then((result) => {
      if (!cancelled) setWorkspaces(result);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="p-8 space-y-6 max-w-4xl">
      <div>
        <h2 className="text-2xl font-bold">Sync</h2>
        <p className="text-muted-foreground text-sm mt-1">
          Export and import snapshots, sync live with another device via relay,
          or auto-ingest conversation exports from Claude Desktop / ChatGPT
          Desktop.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-6">
        <ExportCard />
        <ImportCard />
      </div>

      <Separator />

      <div>
        <h3 className="text-lg font-semibold">Live Sync</h3>
        <p className="text-xs text-muted-foreground mt-1 mb-3">
          Sync one workspace with another device through a relay. Sessions are
          end-to-end encrypted with the one-time code: the relay never sees its
          secret, the devices or your data. To share a
          curated workspace with a colleague one way, serve it with Send only:
          they can pull it but cannot write to your memory. Build one by
          copying projects or topics into a new workspace from Settings →
          Workspaces.
        </p>
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
            <ServePanel workspaces={workspaces} />
          </TabsContent>

          <TabsContent value="receive">
            <ReceivePanel workspaces={workspaces} />
          </TabsContent>
        </Tabs>
      </div>

      <Separator />

      <DesktopExportsCard />
    </div>
  );
}
