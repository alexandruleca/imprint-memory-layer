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

/** A topic's memories from one source, in source order. */
export interface TopicSource {
  source: string;
  project: string;
  /** How it was stored: "conversation", "file", "url", …; "" for older memories. */
  source_type: string;
  /** A conversation's first question; "" for files. */
  title: string;
  chunks: Pick<MemoryNode, "id" | "label" | "type" | "chunk_index" | "tags" | "content">[];
}

export interface TopicDetailData {
  topic: string;
  /** The project the detail is limited to, if any. */
  project: string | null;
  total: number;
  projects: [string, number][];
  /** Largest group first. */
  sources: TopicSource[];
}

/** One readable piece of a source: a conversation event, or a run of a file
 * between chunk boundaries (GET /api/sources/read). */
export interface SourceBlock {
  text: string;
  start: number;
  end: number;
  /** Chunk indexes that cover this block. */
  chunks: number[];
  /** A chunk covering it carries the topic the reader was opened for. */
  match: boolean;
  // Conversation events only (imprint/conversation_source.py).
  event?: number;
  role?: "user" | "assistant" | "system" | "developer" | "tool" | "context" | "speaker";
  kind?: "message" | "analysis" | "tool_call" | "tool_output" | "client_context" | "attachment";
  turn?: number;
  timestamp?: string | number;
  tool?: string;
  call_id?: string;
  audio_start?: number;
  audio_end?: number;
  language?: string;
  speaker?: string | null;
}

export interface SourceRead {
  source: string;
  project: string;
  kind: string;
  available: true;
  title: string;
  topic: string;
  total_chars: number;
  chunk_count: number;
  updated_at?: number;
  details: { event_count?: number; omissions?: Record<string, number>; media?: string };
  blocks: SourceBlock[];
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
  /** Canonical owner when known; linked sources cannot be moved from the related project. */
  project?: string | null;
  linked?: boolean;
  source: string;
  chunks: number;
  /** A conversation's first question; "" for files. */
  title?: string;
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
  /** What the setting is for and when to change it, and a value with its situation. */
  help?: string;
  example?: string;
  /** Credential: `value` is masked ("****" + last 4 characters, or empty when unset). */
  secret?: boolean;
  /** Known values; some, such as GPUs, are read from this device. */
  choices?: ConfigChoice[];
  /** Values outside `choices` are valid too. */
  custom?: boolean;
}

export interface ConfigChoice {
  value: string | number | boolean;
  label: string;
  detail: string;
  /** Why this device cannot use the value; empty when it can. */
  unavailable: string;
}

export interface ChatSession {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count?: number;
}

export interface ChatMessage {
  role: "user" | "assistant" | "tool";
  content: string;
  tool_name?: string;
  tool_args?: Record<string, unknown>;
}

/** /api/chat/status. A missing local binding or model is set up by the first chat. */
export interface ChatStatus {
  installed: boolean;
  provider: string;
  model?: string;
  model_ready: boolean;
  model_path: string;
  model_file?: string;
  error: string | null;
}

/** One server-sent event of a chat reply (imprint/chat.py stream_reply). */
export type ChatEvent =
  | { type: "download_start"; file: string; repo: string; path: string }
  | { type: "download_progress"; downloaded: number; total: number }
  | { type: "download_complete"; path: string }
  | { type: "load_start" }
  | { type: "notice"; text: string; title?: string; code?: string }
  | { type: "analysis"; text: string }
  | { type: "token"; text: string }
  | { type: "tool_call"; name: string; args: Record<string, unknown> }
  | { type: "tool_result"; name: string; result: string }
  | { type: "assistant_message"; text: string }
  | { type: "error"; error: string }
  | { type: "done" };

export interface IngestionJob {
  pid: number;
  command: string;
  phase?: string;
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

/** `yielded`: a background tagging run that stepped aside for another job
 * and starts again when the queue is idle (ADR 0067). */
export type QueueJobStatus = "queued" | "running" | "done" | "failed" | "cancelled" | "yielded";

export interface QueueJob {
  id: string;
  command: string;
  body: Record<string, unknown>;
  /** What the dashboard calls the job and what it was asked to do (imprint/commands.py describe). */
  label?: string;
  params?: { label: string; value: string }[];
  /** The workspace it was queued for. */
  workspace?: string | null;
  status: QueueJobStatus;
  pid: number | null;
  pgid: number | null;
  exit_code: number | null;
  error: string | null;
  created_at: number;
  started_at: number | null;
  ended_at: number | null;
  // Present when status === "running" and the progress record matches this job.
  phase?: string;
  processed?: number;
  total?: number;
  /** What processed and total count; a total of 0 is a step of unknown size. */
  unit?: string;
  stored?: number;
  skipped?: number;
  projects?: string[];
  elapsed?: number;
  percent?: number;
  eta_seconds?: number | null;
}

/** Background LLM tagging (ADR 0067): `off` while tagger.llm is off; `running`;
 * `yielded` after stepping aside for another job; `waiting` to start when the
 * queue is idle; `paused` after a cancel, until more memories wait; `failed`,
 * retried at `resume_at`; `idle` with nothing to tag. */
export type BackgroundTaggingState = "off" | "running" | "yielded" | "waiting" | "paused" | "failed" | "idle";

export interface BackgroundTagging {
  enabled: boolean;
  /** Memories waiting for LLM tagging, across workspaces; null when unknown. */
  pending: number | null;
  state: BackgroundTaggingState;
  /** The running or latest run. */
  job: QueueJob | null;
  resume_at: number | null;
}

export interface QueueResponse {
  active: QueueJob | null;
  queued: QueueJob[];
  recent: QueueJob[];
  background?: BackgroundTagging;
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
  /** `updated`: memories already here whose newer tags replaced ours. */
  memories: { inserted: number; updated?: number; skipped: number };
  facts: { inserted: number; skipped: number };
}

export interface DatasetProgress {
  dataset: string;
  done: number;
  total: number;
}
