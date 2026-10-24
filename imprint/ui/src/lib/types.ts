export interface Project {
  id: string;
  name: string;
  count: number;
  types: Record<string, number>;
  color: string;
  topDomains: string[];
  topLangs: string[];
}

export interface Topic {
  id: string;
  name: string;
  count: number;
  topProjects: string[];
  topLangs: string[];
  color: string;
}

export interface DeviceIdentity { id: string; name: string; os: string }
export interface PathContext {
  current_device: DeviceIdentity; working_directory: string;
  recorded_device: DeviceIdentity; source_device: DeviceIdentity;
  original_source_path: string; local_source_path: string; path_status: string;
}

export interface MemoryNode {
  recorded_device?: DeviceIdentity;
  path_context?: PathContext;
  similarity?: number;
  retrieval_score?: number;
  match_type?: "semantic" | "lexical" | "hybrid";
  source_hash?: string;
  source_mtime?: number;
  timestamp?: number;
  id: string;
  label: string;
  project: string;
  type: string;
  source: string;
  chunk_index: number;
  tags: Tags;
  content: string;
}

export interface Tags {
  lang: string;
  layer: string;
  kind: string;
  domain: string[];
  topics: string[];
}

export interface Facets {
  types: [string, number][];
  langs: [string, number][];
  domains: [string, number][];
  layers?: [string, number][];
}

export interface OverviewData {
  projects: Project[];
  total: number;
  version: number;
  facets: Facets;
}

export interface TopicOverviewData {
  topics: Topic[];
  total: number;
  facets: Facets;
}

export interface TopicDetailData {
  topic: string;
  total: number;
  nodes: MemoryNode[];
  projects: [string, number][];
}

export interface SourceLineage {
  source: string;
  total: number;
  chunks: MemoryNode[];
}

export interface StatsData {
  total: number;
  projects: [string, number][];
  types: [string, number][];
  langs: [string, number][];
  domains: [string, number][];
  layers: [string, number][];
  topics: [string, number][];
}

export interface SourceEntry {
  source: string;
  chunks: number;
}

export interface SourcesData {
  sources: SourceEntry[];
  total: number;
}

export interface SourceSummary {
  recorded_device?: DeviceIdentity;
  path_context?: PathContext;
  source: string;
  project: string;
  type: string;
  source_type: string;
  source_mtime: number;
  chunk_count: number;
  tags: Tags;
  first_chunk_preview: string;
}

export interface ConfigSetting {
  key: string;
  value: unknown;
  source: string;
  default: unknown;
  type: string;
  env: string;
  desc: string;
  /** Credential: `value` is masked ("****" + last 4 characters, or empty when unset). */
  secret?: boolean;
}

export interface ChatSession {
  id: string;
  title: string;
  created: number;
  updated: number;
}

export interface ChatMessage {
  role: "user" | "assistant" | "tool";
  content: string;
  tool_name?: string;
  tool_args?: Record<string, unknown>;
}

export interface IngestionJob {
  pid: number;
  command: string;
  phase?: "embedding" | "llm_tagging";
  processed: number;
  total: number;
  stored: number;
  skipped: number;
  started_at: number;
  updated_at: number;
  projects: string[];
  elapsed: number;
  percent: number;
  eta_seconds: number | null;
}

export type QueueJobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface QueueJob {
  id: string;
  command: string;
  body: Record<string, unknown>;
  status: QueueJobStatus;
  pid: number | null;
  pgid: number | null;
  exit_code: number | null;
  error: string | null;
  created_at: number;
  started_at: number | null;
  ended_at: number | null;
  // Present when status === "running" and the progress file matches this job.
  phase?: "embedding" | "llm_tagging";
  processed?: number;
  total?: number;
  stored?: number;
  skipped?: number;
  projects?: string[];
  elapsed?: number;
  percent?: number;
  eta_seconds?: number | null;
}

export interface QueueResponse {
  active: QueueJob | null;
  queued: QueueJob[];
  recent: QueueJob[];
}

// ── Graph (Obsidian-style) ─────────────────────────────────────

export type GraphNodeKind = "project" | "topic" | "source" | "chunk";
export type GraphEdgeKind = "contains" | "relates" | "sequence" | "similar";

export interface GraphNode {
  id: string;
  kind: GraphNodeKind;
  label: string;
  count: number;
  color: string;
  focus?: boolean;
  fullPath?: string;
  project?: string;
  content?: string;
  chunk_index?: number;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  weight: number;
  kind: GraphEdgeKind;
}

export interface GraphScopeData {
  scope: string;
  center: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// ── Sync types ─────────────────────────────────────────────────

/** What this machine is willing to do in a live sync (both sides enforce their own). */
export type SyncDirection = "both" | "send" | "receive";

export interface SyncEvent {
  type:
    | "room"
    | "session"
    | "status"
    | "approval_required"
    | "auto_accepted"
    | "trusted"
    | "paired"
    | "peer_connected"
    | "confirm_push"
    | "plan"
    | "refused"
    | "workspace_created"
    | "progress"
    | "pull_complete"
    | "push_complete"
    | "done"
    | "cancelled"
    | "warning"
    | "error";
  room_id?: string;
  /** room: the one-time share code (room ID and PIN). */
  code?: string;
  /** room: what the other machine enters — the code, behind the relay address on a custom relay. */
  target?: string;
  /** room: the relay this session uses. */
  relay?: string;
  pin?: string;
  session_id?: string;
  status?: string;
  hostname?: string;
  user?: string;
  os?: string;
  fingerprint?: string;
  phase?: string;
  dataset?: string;
  done?: number;
  total?: number;
  stats?: SyncStats;
  pull_stats?: SyncStats | null;
  push_stats?: SyncStats | null;
  message?: string;
  /** room: the served workspace; session/workspace_created: the local workspace. */
  workspace?: string;
  /** room: what peers may do; plan: the phases this session runs. */
  pull?: boolean;
  push?: boolean;
  /** plan: why each missing phase is left out. */
  skipped?: string[];
  /** refused: which peer request the provider's policy rejected. */
  request?: "pull" | "push";
}

export interface SyncStats {
  memories: { inserted: number; skipped: number };
  facts: { inserted: number; skipped: number };
}

export interface DatasetProgress {
  dataset: string;
  done: number;
  total: number;
}
