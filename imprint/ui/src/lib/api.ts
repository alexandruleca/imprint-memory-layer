const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8420";

async function fetchAPI<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

// ── Data endpoints ─────────────────────────────────────────────

import type {
  OverviewData,
  TopicOverviewData,
  TopicDetailData,
  SourceLineage,
  SourcesData,
  SourceSummary,
  StatsData,
  ConfigSetting,
  ChatSession,
  MemoryNode,
} from "./types";

export async function getOverview(filters?: Record<string, string[]>): Promise<OverviewData> {
  const params = new URLSearchParams();
  if (filters) {
    for (const [key, vals] of Object.entries(filters)) {
      vals.forEach((v) => params.append(key, v));
    }
  }
  const qs = params.toString();
  return fetchAPI(`/api/overview${qs ? `?${qs}` : ""}`);
}

export async function getProjectDetail(name: string) {
  return fetchAPI(`/api/project/${encodeURIComponent(name)}`);
}

export async function getNodes(params: {
  project?: string;
  type?: string;
  domain?: string;
  lang?: string;
  limit?: number;
  offset?: string;
}) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== "") qs.set(k, String(v));
  });
  return fetchAPI(`/api/nodes?${qs}`);
}

export async function searchMemories(q: string, limit = 30) {
  return fetchAPI(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`);
}

export async function getNeighbors(id: string, k = 10) {
  return fetchAPI(`/api/neighbors?id=${encodeURIComponent(id)}&k=${k}`);
}

export async function getStats(): Promise<StatsData> {
  return fetchAPI("/api/stats");
}

export async function getMemory(id: string) {
  return fetchAPI(`/api/memory/${encodeURIComponent(id)}`);
}

export async function getTimeline(project = "", limit = 500) {
  const params = new URLSearchParams();
  if (project) params.set("project", project);
  params.set("limit", String(limit));
  return fetchAPI(`/api/timeline?${params}`);
}

// ── Topics ─────────────────────────────────────────────────────

export async function getTopics(filters?: Record<string, string[]>): Promise<TopicOverviewData> {
  const params = new URLSearchParams();
  if (filters) {
    for (const [key, vals] of Object.entries(filters)) {
      vals.forEach((v) => params.append(key, v));
    }
  }
  const qs = params.toString();
  return fetchAPI(`/api/topics${qs ? `?${qs}` : ""}`);
}

export async function getTopicDetail(name: string): Promise<TopicDetailData> {
  return fetchAPI(`/api/topic/${encodeURIComponent(name)}`);
}

// ── Sources ────────────────────────────────────────────────────

export async function listSources(params?: {
  project?: string;
  lang?: string;
  layer?: string;
  limit?: number;
}): Promise<SourcesData> {
  const qs = new URLSearchParams();
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== "") qs.set(k, String(v));
    });
  }
  const q = qs.toString();
  return fetchAPI(`/api/sources${q ? `?${q}` : ""}`);
}

export async function getSourceSummary(sourceKey: string, project = ""): Promise<SourceSummary> {
  const params = new URLSearchParams();
  if (project) params.set("project", project);
  const q = params.toString();
  return fetchAPI(`/api/sources/summary/${encodeURIComponent(sourceKey)}${q ? `?${q}` : ""}`);
}

export async function getSourceLineage(sourceKey: string): Promise<SourceLineage> {
  return fetchAPI(`/api/source/${encodeURIComponent(sourceKey)}`);
}

// ── Knowledge graph ─────────────────────────────────────────────

export async function getKG(subject = "", limit = 200) {
  const params = new URLSearchParams();
  if (subject) params.set("subject", subject);
  params.set("limit", String(limit));
  return fetchAPI(`/api/kg?${params}`);
}

// ── Workspaces ──────────────────────────────────────────────────

export async function getWorkspaces() {
  return fetchAPI<{ active: string; workspaces: string[] }>("/api/workspaces");
}

export async function switchWorkspace(name: string) {
  return fetchAPI("/api/workspace/switch", {
    method: "POST",
    body: JSON.stringify({ workspace: name }),
  });
}

// ── Chat ────────────────────────────────────────────────────────

export async function getChatStatus() {
  return fetchAPI<{ enabled: boolean; installed: boolean }>("/api/chat/status");
}

export async function getChatSessions() {
  return fetchAPI<{ sessions: ChatSession[] }>("/api/chat/sessions");
}

export async function createChatSession() {
  return fetchAPI<ChatSession>("/api/chat/sessions", { method: "POST" });
}

export async function getChatSession(id: string) {
  return fetchAPI(`/api/chat/sessions/${encodeURIComponent(id)}`);
}

export async function deleteChatSession(id: string) {
  return fetchAPI(`/api/chat/sessions/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function renameChatSession(id: string, title: string) {
  return fetchAPI(`/api/chat/sessions/${encodeURIComponent(id)}/rename`, {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

export function streamChat(
  sessionId: string,
  message: string,
  onEvent: (ev: Record<string, unknown>) => void,
): AbortController {
  const ctrl = new AbortController();
  fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message }),
    signal: ctrl.signal,
  }).then(async (res) => {
    const reader = res.body?.getReader();
    if (!reader) return;
    const decoder = new TextDecoder();
    let buffer = "";
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
  });
  return ctrl;
}

// ── Config ──────────────────────────────────────────────────────

export async function getConfig() {
  return fetchAPI<{ settings: ConfigSetting[] }>("/api/config");
}

export async function setConfigValue(key: string, value: unknown) {
  return fetchAPI(`/api/config/${encodeURIComponent(key)}`, {
    method: "PUT",
    body: JSON.stringify({ value }),
  });
}

// ── Commands ────────────────────────────────────────────────────

export function streamCommand(
  command: string,
  body: Record<string, unknown>,
  onEvent: (ev: Record<string, unknown>) => void,
): AbortController {
  const ctrl = new AbortController();
  fetch(`${API_BASE}/api/commands/${command}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: ctrl.signal,
  }).then(async (res) => {
    const reader = res.body?.getReader();
    if (!reader) return;
    const decoder = new TextDecoder();
    let buffer = "";
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
  });
  return ctrl;
}

// ── Sync ────────────────────────────────────────────────────────

export async function syncExport(workspace?: string, output?: string) {
  return fetchAPI("/api/sync/export", {
    method: "POST",
    body: JSON.stringify({ workspace, output }),
  });
}

export async function syncImport(path: string, workspace?: string) {
  return fetchAPI("/api/sync/import", {
    method: "POST",
    body: JSON.stringify({ path, workspace }),
  });
}

// ── SSE stream ──────────────────────────────────────────────────

export function connectSSE(onUpdate: (version: number) => void): EventSource {
  const es = new EventSource(`${API_BASE}/api/stream`);
  es.addEventListener("update", (e) => {
    try {
      const data = JSON.parse((e as MessageEvent).data);
      onUpdate(data.version);
    } catch {}
  });
  return es;
}
