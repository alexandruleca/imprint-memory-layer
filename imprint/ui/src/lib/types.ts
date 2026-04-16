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

export interface MemoryNode {
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
