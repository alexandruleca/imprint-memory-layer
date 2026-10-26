import type { SyncDirection, SyncEvent } from "./types";

import { API_BASE } from "./api-base";

// ── Snapshot export / import ───────────────────────────────────

export async function downloadExport(): Promise<void> {
  const res = await fetch(`${API_BASE}/api/sync/export/download`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Export failed: ${res.status}`);
  }
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = match?.[1] || "imprint-export.zip";

  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export async function uploadImport(file: File): Promise<{ ok: boolean }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/api/sync/import/upload`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Import failed: ${res.status}`);
  }
  return res.json();
}

// ── Live sync SSE streams ──────────────────────────────────────

function readSSE(
  res: Response,
  onEvent: (ev: SyncEvent) => void,
): void {
  const reader = res.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  let buffer = "";

  (async () => {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            onEvent(JSON.parse(line.slice(6)));
          } catch {}
        }
      }
    }
  })();
}

/** Which workspace a live session uses and what this machine allows. */
export interface SyncSessionOptions {
  workspace: string;
  direction: SyncDirection;
  remember?: boolean;
  /** Serving only: relay host; empty uses the public relay. */
  relay?: string;
}

/** Report a rejected request with the server's reason when it gave one. */
async function failure(res: Response): Promise<SyncEvent> {
  const body = await res.json().catch(() => ({}));
  return { type: "error", message: body.error || `Server error: ${res.status}` };
}

export function streamSyncServe(
  options: SyncSessionOptions,
  onEvent: (ev: SyncEvent) => void,
): AbortController {
  const ctrl = new AbortController();
  fetch(`${API_BASE}/api/sync/serve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace: options.workspace, direction: options.direction, relay: options.relay || "", ...(options.remember ? { remember: true } : {}) }),
    signal: ctrl.signal,
  }).then(async (res) => {
    if (!res.ok) {
      onEvent(await failure(res));
      return;
    }
    readSSE(res, onEvent);
  }).catch((err) => {
    if (err.name !== "AbortError") {
      onEvent({ type: "error", message: err.message });
    }
  });
  return ctrl;
}

export function streamSyncReceive(
  code: string,
  options: SyncSessionOptions,
  onEvent: (ev: SyncEvent) => void,
): AbortController {
  const ctrl = new AbortController();
  fetch(`${API_BASE}/api/sync/receive`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, workspace: options.workspace, direction: options.direction, ...(options.remember ? { remember: true } : {}) }),
    signal: ctrl.signal,
  }).then(async (res) => {
    if (!res.ok) {
      onEvent(await failure(res));
      return;
    }
    readSSE(res, onEvent);
  }).catch((err) => {
    if (err.name !== "AbortError") {
      onEvent({ type: "error", message: err.message });
    }
  });
  return ctrl;
}

export async function cancelSync(sessionId: string): Promise<void> {
  await fetch(`${API_BASE}/api/sync/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export type SyncApprovalDecision = "accept" | "trust" | "reject";

export interface PairedDevice {
  id: string;
  peer: { hostname: string; fingerprint: string; os: string; user: string };
  workspace: string;
  remote_workspace: string;
  relay: string;
  direction: SyncDirection;
  enabled: boolean;
  state: "active" | "forget_pending";
  created_at: number;
  /** When the device last answered on its pairing; null until the first reconnect. */
  last_seen: number | null;
  last_sync: number | null;
  last_error: string;
}

export async function getPairedDevices(): Promise<PairedDevice[]> {
  const res = await fetch(`${API_BASE}/api/sync/peers`);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || "Could not load paired devices");
  return (await res.json()).peers;
}

/** Pause/resume; forget on both devices (the peer is told); remove from this device only, now. */
export type PairedDeviceAction = "enabled" | "forget" | "remove";

export async function controlPairedDevice(id: string, action: PairedDeviceAction, enabled?: boolean): Promise<void> {
  const res = await fetch(`${API_BASE}/api/sync/peers/${encodeURIComponent(id)}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(action === "enabled" ? { action, enabled } : { action, confirm: true }),
  });
  if (!res.ok) throw new Error((await res.json()).error || "Could not update paired device");
}

export async function approveSync(
  sessionId: string,
  decision: SyncApprovalDecision,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/sync/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, decision }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Approval failed: ${res.status}`);
  }
}
