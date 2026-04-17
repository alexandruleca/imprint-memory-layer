import type { SyncEvent } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8420";

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

export function streamSyncServe(
  onEvent: (ev: SyncEvent) => void,
): AbortController {
  const ctrl = new AbortController();
  fetch(`${API_BASE}/api/sync/serve`, {
    signal: ctrl.signal,
  }).then((res) => {
    if (!res.ok) {
      onEvent({ type: "error", message: `Server error: ${res.status}` });
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
  roomId: string,
  pin: string,
  onEvent: (ev: SyncEvent) => void,
): AbortController {
  const ctrl = new AbortController();
  fetch(`${API_BASE}/api/sync/receive`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ room_id: roomId, pin }),
    signal: ctrl.signal,
  }).then((res) => {
    if (!res.ok) {
      onEvent({ type: "error", message: `Server error: ${res.status}` });
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
