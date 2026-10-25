"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Bot, Boxes, Cpu, Database, FileInput, KeyRound, Layers, MessageSquare, Mic, ScrollText, Search, Scissors,
  ShieldCheck, SquareTerminal, Tags, type LucideIcon,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { getConfig, resetConfigValue, setConfigValue } from "@/lib/api";
import { SettingsSkeleton } from "@/components/loaders";
import { WorkspaceManager } from "@/components/workspace-manager";
import { WslProfiles } from "@/components/wsl-profiles";
import { SettingRow, settingTitle } from "./_components/setting-row";
import type { ConfigSetting } from "@/lib/types";

const GROUPS: Record<string, { title: string; desc: string; icon: LucideIcon }> = {
  model: { title: "Embeddings", desc: "The embedding model and the hardware it runs on.", icon: Cpu },
  huggingface: { title: "Hugging Face", desc: "Access for gated or private model downloads.", icon: KeyRound },
  qdrant: { title: "Vector store", desc: "The local Qdrant server that holds memories.", icon: Database },
  chunker: { title: "Chunking", desc: "How files are split before embedding.", icon: Scissors },
  tagger: { title: "Tagging", desc: "Topic tags added to every chunk.", icon: Tags },
  redact: { title: "Redaction", desc: "Secrets replaced with placeholders before memories are stored.", icon: ShieldCheck },
  ingest: { title: "Ingestion", desc: "Documents, OCR and URL fetching.", icon: FileInput },
  voice: { title: "Voice", desc: "The native live voice runtime.", icon: Mic },
  chat: { title: "Chat", desc: "The dashboard chat assistant.", icon: MessageSquare },
  summarizer: { title: "Session summaries", desc: "LLM summaries written when a session ends.", icon: ScrollText },
  recall: { title: "Recall", desc: "The MCP recall tool's optional local model.", icon: Search },
  collection: { title: "Storage", desc: "Collection naming.", icon: Layers },
};
const SUBGROUPS: Record<string, string> = { "tagger.local": "Local model (llama.cpp)" };

function groupOf(key: string) {
  return key.split(".")[0];
}

function matches(s: ConfigSetting, query: string) {
  const text = `${s.key} ${s.desc} ${settingTitle(s.key)} ${GROUPS[groupOf(s.key)]?.title ?? ""}`.toLowerCase();
  return query.split(/\s+/).every((word) => text.includes(word));
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<ConfigSetting[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");

  useEffect(() => {
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
      setSettings((await getConfig()).settings);
      return true;
    } catch (e) {
      toast.error(`Could not save ${key}`, { description: e instanceof Error ? e.message : String(e) });
      return false;
    }
  }

  async function resetValue(s: ConfigSetting) {
    try {
      await resetConfigValue(s.key);
      setSettings((await getConfig()).settings);
      return true;
    } catch (e) {
      toast.error(`Could not ${s.secret ? "remove" : "reset"} ${s.key}`, { description: e instanceof Error ? e.message : String(e) });
      return false;
    }
  }

  if (loading) return <SettingsSkeleton />;

  const q = query.trim().toLowerCase();
  const groups: [string, ConfigSetting[]][] = [];
  for (const s of settings) {
    if (q && !matches(s, q)) continue;
    const name = groupOf(s.key);
    const group = groups.find(([g]) => g === name);
    if (group) group[1].push(s);
    else groups.push([name, [s]]);
  }
  const showWorkspaces = !q || "workspaces roots sources".includes(q);
  const showWsl = !q || "wsl linux distributions profiles enroll".includes(q);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 sm:p-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold">Settings</h1>
          <p className="max-w-xl text-sm text-muted-foreground">
            Saved to this device&apos;s <code className="rounded bg-muted px-1 text-xs">config.json</code>. Environment
            variables take precedence. Running servers apply changes when they restart.
          </p>
        </div>
        <InputGroup className="w-full sm:w-72">
          <InputGroupAddon><Search /></InputGroupAddon>
          <InputGroupInput aria-label="Filter settings" placeholder="Filter settings" value={query} onChange={(e) => setQuery(e.target.value)} />
        </InputGroup>
      </header>
      {error && <p role="alert" className="text-sm text-destructive">Settings could not load: {error}</p>}

      <div className="grid gap-6 lg:grid-cols-[11rem_minmax(0,1fr)]">
        <nav aria-label="Settings sections" className="sticky top-6 hidden space-y-0.5 self-start lg:block">
          {showWorkspaces && <NavLink href="#workspaces" icon={Boxes} label="Workspaces" />}
          {showWsl && <NavLink href="#wsl" icon={SquareTerminal} label="WSL profiles" />}
          {groups.map(([name, items]) => (
            <NavLink
              key={name}
              href={`#settings-${name}`}
              icon={GROUPS[name]?.icon ?? Bot}
              label={GROUPS[name]?.title ?? name}
              changed={items.filter((s) => s.source !== "default").length}
            />
          ))}
        </nav>

        <div className="min-w-0 space-y-6">
          {showWorkspaces && (
            <Card id="workspaces" className="scroll-mt-6">
              <SectionHeader icon={Boxes} title="Workspaces" desc="Isolated memory stores, their roots and sources." />
              <CardContent><WorkspaceManager /></CardContent>
            </Card>
          )}

          {showWsl && (
            <Card id="wsl" className="scroll-mt-6">
              <SectionHeader icon={SquareTerminal} title="WSL profiles" desc="AI clients inside WSL distributions that use this installation." />
              <CardContent><WslProfiles /></CardContent>
            </Card>
          )}

          {groups.map(([name, items]) => {
            const meta = GROUPS[name] ?? { title: name, desc: "", icon: Bot };
            let subgroup = "";
            return (
              <Card key={name} id={`settings-${name}`} className="scroll-mt-6 gap-0 pb-0">
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

          {q && !groups.length && !showWorkspaces && !showWsl && (
            <Empty className="border border-dashed border-border">
              <EmptyHeader>
                <EmptyTitle>No matching settings</EmptyTitle>
                <EmptyDescription>Nothing matches &ldquo;{query}&rdquo;.</EmptyDescription>
              </EmptyHeader>
            </Empty>
          )}
        </div>
      </div>
    </div>
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

function NavLink({ href, icon: Icon, label, changed = 0 }: { href: string; icon: LucideIcon; label: string; changed?: number }) {
  return (
    <a href={href} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
      <Icon className="size-4 shrink-0" />
      <span className="flex-1 truncate">{label}</span>
      {changed > 0 && (
        <span className="rounded-full bg-primary/10 px-1.5 text-[10px] font-medium text-primary" title={`${changed} changed from the default`}>
          {changed}
        </span>
      )}
    </a>
  );
}
