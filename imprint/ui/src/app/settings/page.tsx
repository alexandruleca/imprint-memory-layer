"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { toast } from "sonner";
import {
  ArrowUpCircle, Bot, Boxes, Cpu, Database, FileInput, FolderTree, KeyRound, Layers, MessageSquare, Mic, ScrollText, Search,
  Scissors, ShieldCheck, SquareTerminal, Tags, type LucideIcon,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { getConfig, getFsRoots, resetConfigValue, setConfigValue } from "@/lib/api";
import { ListSkeleton } from "@/components/loaders";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader, PaneHeading, PaneLink, SplitPage } from "@/components/split-page";
import { WorkspaceManager } from "@/components/workspace-manager";
import { WslProfiles } from "@/components/wsl-profiles";
import { SettingRow, settingTitle } from "./_components/setting-row";
import type { ConfigSetting } from "@/lib/types";

const GROUPS: Record<string, { title: string; desc: string; icon: LucideIcon }> = {
  model: { title: "Embeddings", desc: "The local model that turns text into the vectors search compares, and the hardware it runs on. Changing the model, its file, revision, dimension, token cap, pooling or query instruction needs a new workspace and a reindex; device, GPU, thread and batch settings only change speed.", icon: Cpu },
  huggingface: { title: "Hugging Face", desc: "Credentials for downloading models from the Hugging Face Hub, needed only for gated or private models or when anonymous downloads are rate-limited.", icon: KeyRound },
  qdrant: { title: "Vector store", desc: "Where the Qdrant vector database runs and how Imprint reaches it. By default Imprint downloads and manages a local Qdrant; these settings point it at your own server instead.", icon: Database },
  chunker: { title: "Chunking", desc: "How files and conversations are split into chunks before they are embedded. Changes apply when sources are next indexed or refreshed and keep existing workspaces working.", icon: Scissors },
  tagger: { title: "Tagging", desc: "How memories are labeled with topics, domains and types for filtering and browsing: cheap built-in zero-shot tagging by default, LLM tagging optionally. The local settings configure the on-device helper model, which local recall, summaries and project analysis use too.", icon: Tags },
  redact: { title: "Redaction", desc: "Removing secrets before anything is stored or sent to a model. Rules for known credential formats always run; this adds optional model-based detection.", icon: ShieldCheck },
  ingest: { title: "Ingestion", desc: "How files, documents, images and web pages become memories: size limits, document types, local OCR, alternative extractors, and how URLs are fetched and crawled.", icon: FileInput },
  voice: { title: "Voice", desc: "Echo, the local voice companion: where its voice runtime is installed and how it looks and sounds.", icon: Mic },
  audio: { title: "Audio ingestion", desc: "Local, on-CPU transcription of audio files during ingestion: the speech model, the language, and limits on size and length. Changes re-transcribe audio when it is next indexed.", icon: Mic },
  associations: { title: "Project associations", desc: "Linking a source to other projects it mentions, so its memories show up in their searches too, and where conversations without a known project folder go.", icon: FolderTree },
  chat: { title: "Chat", desc: "The dashboard's chat, where a model answers questions by searching your memory: which model, where it runs (on this computer or a remote provider), and how long and how much it may research per answer.", icon: MessageSquare },
  summarizer: { title: "Session summaries", desc: "An optional step that, after each agent session, has a model write a recap and save the session's decisions, bug causes, patterns and facts as memories. Off by default, and local unless you choose a remote provider.", icon: ScrollText },
  recall: { title: "Recall", desc: "The recall tool lets agents run several memory lookups in one call; optionally, a model on this computer plans those lookups and answers with citations.", icon: Search },
  workspace: {
    title: "Workspace selection",
    desc: "A project's approved .imprint/workspace.json or roots choose an agent session's workspace; this covers the rest.",
    icon: FolderTree,
  },
  update: { title: "Updates", desc: "Which releases this installation is offered: stable, or dev builds as well.", icon: ArrowUpCircle },
  collection: { title: "Storage", desc: "The Qdrant collection the default workspace uses; other workspaces get their own automatically.", icon: Layers },
};
const SUBGROUPS: Record<string, string> = { "tagger.local": "Local model (llama.cpp)" };

function groupOf(key: string) {
  return key.split(".")[0];
}

/** The section in the URL's hash (#settings-chat, #workspaces). */
function useHash() {
  return useSyncExternalStore(
    (notify) => { window.addEventListener("hashchange", notify); return () => window.removeEventListener("hashchange", notify); },
    () => window.location.hash.slice(1),
    () => "",
  );
}

const DEVICE_SECTIONS = [
  { id: "workspaces", title: "Workspaces", desc: "Isolated memory stores, their roots and sources.", icon: Boxes, words: "workspaces roots sources" },
  { id: "wsl", title: "WSL profiles", desc: "AI clients inside WSL distributions that use this installation.", icon: SquareTerminal, words: "wsl linux distributions profiles enroll" },
];

function matches(s: ConfigSetting, query: string) {
  const text = `${s.key} ${s.desc} ${s.help ?? ""} ${settingTitle(s.key)} ${GROUPS[groupOf(s.key)]?.title ?? ""}`.toLowerCase();
  return query.split(/\s+/).every((word) => text.includes(word));
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<ConfigSetting[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const hash = useHash();
  const [windows, setWindows] = useState(false);
  const deviceSections = DEVICE_SECTIONS.filter((section) => section.id !== "wsl" || windows);

  async function load() {
    setSettings((await getConfig()).settings);
  }

  useEffect(() => {
    getFsRoots().then((roots) => setWindows(roots.platform === "Windows")).catch(() => {});
    getConfig()
      .then((d) => setSettings(d.settings))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  // Rows confirm success inline. Secret values never appear in these
  // messages: only the key does.
  async function saveValue(key: string, value: string) {
    try {
      await setConfigValue(key, value);
      await load();
      return true;
    } catch (e) {
      toast.error(`Could not save ${key}`, { description: e instanceof Error ? e.message : String(e) });
      return false;
    }
  }

  async function resetValue(s: ConfigSetting) {
    try {
      await resetConfigValue(s.key);
      await load();
      return true;
    } catch (e) {
      toast.error(`Could not ${s.secret ? "remove" : "reset"} ${s.key}`, { description: e instanceof Error ? e.message : String(e) });
      return false;
    }
  }

  const header = (
    <PageHeader
      title="Settings"
      description={<>Saved to this device&apos;s <code className="rounded bg-muted px-1 text-xs">config.json</code>. Environment variables take precedence. Running servers apply changes when they restart.</>}
      actions={
        <InputGroup className="w-full bg-card sm:w-72">
          <InputGroupAddon><Search /></InputGroupAddon>
          <InputGroupInput aria-label="Filter settings" placeholder="Filter settings" value={query} onChange={(e) => setQuery(e.target.value)} />
        </InputGroup>
      }
    />
  );

  if (loading) return (
    <SplitPage header={header} list={<ListSkeleton count={12} />} listWidth="14rem">
      <Skeleton className="h-96 w-full rounded-xl" />
    </SplitPage>
  );

  const q = query.trim().toLowerCase();
  const groups: [string, ConfigSetting[]][] = [];
  for (const s of settings) {
    const name = groupOf(s.key);
    const group = groups.find(([g]) => g === name);
    if (group) group[1].push(s);
    else groups.push([name, [s]]);
  }
  const ids = [...groups.map(([name]) => `settings-${name}`), ...deviceSections.map((d) => d.id)];
  // Filtering searches every section; otherwise one section shows at a time.
  const current = ids.includes(hash) ? hash : ids[0];
  const visible = (id: string) => (q ? true : id === current);
  const found = groups
    .map(([name, items]) => [name, q ? items.filter((s) => matches(s, q)) : items] as [string, ConfigSetting[]])
    .filter(([name, items]) => items.length > 0 && visible(`settings-${name}`));
  const devices = deviceSections.filter((d) => (q ? d.words.includes(q) : d.id === current));

  return (
    <SplitPage
      header={header}
      listWidth="14rem"
      list={
        <nav aria-label="Settings sections">
          <PaneHeading>Configuration</PaneHeading>
          <div className="space-y-0.5">
            {groups.map(([name, items]) => {
              const Icon = GROUPS[name]?.icon ?? Bot;
              const changed = items.filter((s) => s.source !== "default").length;
              const hits = q ? items.filter((s) => matches(s, q)).length : 0;
              return (
                <PaneLink key={name} href={`#settings-${name}`} selected={!q && current === `settings-${name}`} onClick={() => setQuery("")}
                  className={q && !hits ? "opacity-50" : undefined}>
                  <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="min-w-0 flex-1 truncate">{GROUPS[name]?.title ?? name}</span>
                  {(q ? hits : changed) > 0 && (
                    <span className="rounded-full bg-muted px-1.5 text-[10px] font-medium text-muted-foreground tabular-nums"
                      title={q ? `${hits} matching` : `${changed} changed from the default`}>
                      {q ? hits : changed}
                    </span>
                  )}
                </PaneLink>
              );
            })}
          </div>
          <PaneHeading>This device</PaneHeading>
          <div className="space-y-0.5">
            {deviceSections.map(({ id, title, icon: Icon }) => (
              <PaneLink key={id} href={`#${id}`} selected={!q && current === id} onClick={() => setQuery("")}>
                <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                <span className="min-w-0 flex-1 truncate">{title}</span>
              </PaneLink>
            ))}
          </div>
        </nav>
      }
    >
      {error && <p role="alert" className="text-sm text-destructive">Settings could not load: {error}</p>}

      {found.map(([name, items]) => {
        const meta = GROUPS[name] ?? { title: name, desc: "", icon: Bot };
        let subgroup = "";
        return (
          <Card key={name} id={`settings-${name}`} className="gap-0 pb-0">
            <SectionHeader icon={meta.icon} title={meta.title} desc={meta.desc} />
            <CardContent className="@container mt-4 divide-y divide-border border-t border-border px-0">
              {items.map((s) => {
                const prefix = s.key.split(".").slice(0, -1).join(".");
                const heading = SUBGROUPS[prefix] && prefix !== subgroup ? SUBGROUPS[prefix] : "";
                subgroup = prefix;
                return (
                  <div key={s.key}>
                    {heading && (
                      <p className="bg-muted/40 px-4 py-1.5 text-xs font-medium text-muted-foreground sm:px-6">{heading}</p>
                    )}
                    <SettingRow setting={s} onSave={saveValue} onReset={resetValue} />
                  </div>
                );
              })}
            </CardContent>
          </Card>
        );
      })}

      {devices.map(({ id, title, desc, icon }) => (
        <Card key={id} id={id}>
          <SectionHeader icon={icon} title={title} desc={desc} />
          <CardContent>{id === "workspaces" ? <WorkspaceManager /> : <WslProfiles />}</CardContent>
        </Card>
      ))}

      {q && !found.length && !devices.length && (
        <Empty className="border border-dashed border-border">
          <EmptyHeader>
            <EmptyTitle>No matching settings</EmptyTitle>
            <EmptyDescription>Nothing matches &ldquo;{query}&rdquo;.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}
    </SplitPage>
  );
}

function SectionHeader({ icon: Icon, title, desc }: { icon: LucideIcon; title: string; desc: string }) {
  return (
    <CardHeader className="flex items-center gap-3">
      <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
        <Icon className="size-4" />
      </span>
      <div className="min-w-0">
        <CardTitle>{title}</CardTitle>
        {desc && <CardDescription className="text-xs">{desc}</CardDescription>}
      </div>
    </CardHeader>
  );
}
