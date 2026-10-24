"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Activity, Check, Copy, Eraser, FolderInput, FolderOpen, Globe, ListOrdered, MessagesSquare, PackageOpen, Play,
  RefreshCw, RotateCw, SlidersHorizontal, Square, Tags, type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectSeparator, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Hint } from "@/components/hint";
import { Spinner } from "@/components/loaders";
import { PathBrowserDialog } from "@/components/path-browser-dialog";
import { getStats, streamCommand } from "@/lib/api";
import { cn } from "@/lib/utils";

type Field =
  | { key: string; label: string; kind: "path"; browse: "dir" | "any"; required?: boolean }
  | { key: string; label: string; kind: "text"; placeholder?: string; required?: boolean; suggestProjects?: boolean }
  | { key: string; label: string; kind: "switch"; hint: string }
  | { key: string; label: string; kind: "project" };

type Command = {
  name: string;
  label: string;
  desc: string;
  icon: LucideIcon;
  group: string;
  fields: Field[];
  /** Sent with every run: the CLI subcommand this entry stands for. */
  fixed?: Record<string, string>;
};

const COMMANDS: Command[] = [
  { name: "learn", label: "Learn conversations", desc: "Index coding-assistant conversations and memory files", icon: MessagesSquare, group: "Index", fields: [] },
  { name: "ingest", label: "Ingest directory", desc: "Index a directory or file into memory", icon: FolderInput, group: "Index", fields: [
    { key: "dir", label: "Directory", kind: "path", browse: "any", required: true },
    { key: "force", label: "Re-index everything", kind: "switch", hint: "Also re-embed files that have not changed" },
  ]},
  { name: "refresh", label: "Refresh files", desc: "Re-index only files changed since the last run", icon: RefreshCw, group: "Index", fields: [
    { key: "dir", label: "Directory", kind: "path", browse: "dir", required: true },
  ]},
  { name: "ingest-url", label: "Ingest URL", desc: "Fetch a page or file and index it", icon: Globe, group: "Index", fields: [
    { key: "url", label: "URL", kind: "text", placeholder: "https://", required: true },
    { key: "project", label: "Project", kind: "text", placeholder: "urls", suggestProjects: true },
    { key: "force", label: "Fetch again", kind: "switch", hint: "Re-index even when the URL has not changed" },
  ]},
  { name: "refresh-urls", label: "Refresh URLs", desc: "Re-check stored URLs and re-index the changed ones", icon: RotateCw, group: "Index", fields: [] },
  { name: "retag", label: "Retag memories", desc: "Tag stored chunks again with the configured tagger", icon: Tags, group: "Maintain", fields: [
    { key: "project", label: "Project", kind: "project" },
    { key: "all", label: "Include tagged chunks", kind: "switch", hint: "Also retag chunks the LLM tagger already handled" },
    { key: "dry_run", label: "Dry run", kind: "switch", hint: "Tag without writing the results" },
  ]},
  { name: "sync", label: "Export snapshot", desc: "Write a sync bundle of the active workspace", icon: PackageOpen, group: "Maintain", fields: [], fixed: { action: "export" } },
  { name: "status", label: "Status", desc: "Server state, memory counts and hooks", icon: Activity, group: "Inspect", fields: [] },
  { name: "config", label: "Show config", desc: "Every setting with its value and source", icon: SlidersHorizontal, group: "Inspect", fields: [] },
];
const GROUPS = [...new Set(COMMANDS.map((c) => c.group))];
const ALL_PROJECTS = "\u0000all";

type Run = { state: "idle" | "running" | "cancelled" | "error" } | { state: "done"; code: number };

export default function CommandsPage() {
  const [selected, setSelected] = useState(COMMANDS[0]);
  const [params, setParams] = useState<Record<string, string | boolean>>({});
  const [output, setOutput] = useState("");
  const [run, setRun] = useState<Run>({ state: "idle" });
  const [projects, setProjects] = useState<string[]>([]);
  const [browsing, setBrowsing] = useState<Extract<Field, { kind: "path" }> | null>(null);
  const [copied, setCopied] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const end = useRef<HTMLSpanElement>(null);
  const running = run.state === "running";

  useEffect(() => {
    getStats().then((s) => setProjects((s.projects ?? []).map(([name]) => name))).catch(() => {});
    return () => controller.current?.abort();
  }, []);

  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [output]);

  const missing = selected.fields.some((f) => "required" in f && f.required && !String(params[f.key] ?? "").trim());

  function choose(command: Command) {
    if (running || command.name === selected.name) return;
    setSelected(command);
    setParams({});
  }

  function start() {
    if (running || missing) return;
    const body: Record<string, unknown> = { ...selected.fixed };
    for (const f of selected.fields) {
      const value = params[f.key];
      if (typeof value === "string" ? value.trim() : value) body[f.key] = typeof value === "string" ? value.trim() : value;
    }
    setOutput("");
    setRun({ state: "running" });
    controller.current = streamCommand(selected.name, body, (ev) => {
      if (ev.type === "output") {
        setOutput((prev) => prev + (ev.text as string));
      } else if (ev.type === "done") {
        setRun({ state: "done", code: ev.exit_code as number });
      } else if (ev.type === "error") {
        setOutput((prev) => `${prev}${prev && !prev.endsWith("\n") ? "\n" : ""}ERROR: ${ev.error}\n`);
        setRun({ state: "error" });
      }
    });
  }

  function cancel() {
    controller.current?.abort();
    setRun({ state: "cancelled" });
  }

  async function copy() {
    await navigator.clipboard.writeText(output);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  const Icon = selected.icon;
  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 sm:p-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold">Commands</h1>
          <p className="max-w-xl text-sm text-muted-foreground">
            Run Imprint CLI commands on this device. Jobs run one at a time through the queue.
          </p>
        </div>
        <Button variant="outline" size="sm" nativeButton={false} render={<Link href="/queue/" />}>
          <ListOrdered />
          Open queue
        </Button>
      </header>

      <div className="grid gap-6 lg:grid-cols-[17rem_minmax(0,1fr)]">
        <nav aria-label="Commands" className="space-y-4">
          {GROUPS.map((group) => (
            <div key={group} className="space-y-1">
              <p className="px-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">{group}</p>
              <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-1">
                {COMMANDS.filter((c) => c.group === group).map((c) => {
                  const active = c.name === selected.name;
                  const ItemIcon = c.icon;
                  return (
                    <button
                      key={c.name}
                      type="button"
                      aria-pressed={active}
                      disabled={running && !active}
                      onClick={() => choose(c)}
                      className={cn(
                        "flex w-full items-start gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50",
                        active ? "border-primary/40 bg-primary/5" : "border-transparent hover:bg-muted",
                      )}
                    >
                      <ItemIcon className={cn("mt-0.5 size-4 shrink-0", active ? "text-primary" : "text-muted-foreground")} />
                      <span className="min-w-0">
                        <span className="block text-sm font-medium">{c.label}</span>
                        <span className="block text-xs text-muted-foreground">{c.desc}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="min-w-0 space-y-4">
          <Card>
            <CardHeader className="flex items-start gap-3">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <Icon className="size-4" />
              </span>
              <div className="min-w-0 space-y-1">
                <CardTitle>{selected.label}</CardTitle>
                <CardDescription>{selected.desc}</CardDescription>
                <code className="inline-block rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground">
                  imprint {selected.name}{selected.fixed?.action ? ` ${selected.fixed.action}` : ""}
                </code>
              </div>
            </CardHeader>
            {selected.fields.length > 0 && (
              <CardContent className="space-y-4">
                {selected.fields.map((f) => (
                  <FieldInput
                    key={`${selected.name}-${f.key}`}
                    id={`command-${selected.name}-${f.key}`}
                    field={f}
                    value={params[f.key]}
                    projects={projects}
                    disabled={running}
                    onChange={(value) => setParams((p) => ({ ...p, [f.key]: value }))}
                    onBrowse={() => f.kind === "path" && setBrowsing(f)}
                    onSubmit={start}
                  />
                ))}
              </CardContent>
            )}
            <CardFooter className="justify-end gap-2 border-t">
              {running ? (
                <Button variant="outline" onClick={cancel}>
                  <Square />
                  Cancel
                </Button>
              ) : (
                <Button onClick={start} disabled={missing}>
                  <Play />
                  Run
                </Button>
              )}
            </CardFooter>
          </Card>

          <Card className="gap-0 py-0">
            <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
              <span className="text-sm font-medium">Output</span>
              <RunBadge run={run} />
              <div className="ml-auto flex items-center gap-1">
                <Hint label={copied ? "Copied" : "Copy output"}>
                  <Button variant="ghost" size="icon-sm" aria-label="Copy output" disabled={!output} onClick={copy}>
                    {copied ? <Check /> : <Copy />}
                  </Button>
                </Hint>
                <Hint label="Clear">
                  <Button variant="ghost" size="icon-sm" aria-label="Clear output" disabled={!output || running}
                    onClick={() => { setOutput(""); setRun({ state: "idle" }); }}>
                    <Eraser />
                  </Button>
                </Hint>
              </div>
            </div>
            <ScrollArea className="h-80 bg-muted/30">
              <pre aria-live="polite" className="min-h-80 p-4 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words">
                {output || <span className="text-muted-foreground">{running ? "Waiting for output…" : "Run a command to see its output here."}</span>}
                <span ref={end} />
              </pre>
            </ScrollArea>
          </Card>
        </div>
      </div>

      <PathBrowserDialog
        open={browsing !== null}
        mode={browsing?.browse ?? "dir"}
        title={browsing?.browse === "any" ? "Choose a directory or file" : "Choose a directory"}
        initialPath={browsing ? String(params[browsing.key] ?? "") : undefined}
        onSelect={(path) => { if (browsing) setParams((p) => ({ ...p, [browsing.key]: path })); setBrowsing(null); }}
        onCancel={() => setBrowsing(null)}
      />
    </div>
  );
}

function RunBadge({ run }: { run: Run }) {
  if (run.state === "idle") return null;
  if (run.state === "running") {
    return <Badge variant="secondary" className="gap-1"><Spinner className="size-3" />Running</Badge>;
  }
  if (run.state === "done" && run.code === 0) {
    return <Badge variant="secondary" className="bg-emerald-500/10 text-emerald-700 dark:text-emerald-400">Succeeded</Badge>;
  }
  if (run.state === "cancelled") return <Badge variant="outline">Cancelled</Badge>;
  return <Badge variant="destructive">{run.state === "done" ? `Failed, exit ${run.code}` : "Failed"}</Badge>;
}

function FieldInput({ id, field: f, value, projects, disabled, onChange, onBrowse, onSubmit }: {
  id: string;
  field: Field;
  value: string | boolean | undefined;
  projects: string[];
  disabled: boolean;
  onChange: (value: string | boolean) => void;
  onBrowse: () => void;
  onSubmit: () => void;
}) {
  if (f.kind === "switch") {
    return (
      <div className="flex items-center justify-between gap-4">
        <div className="space-y-0.5">
          <Label htmlFor={id} className="font-medium">{f.label}</Label>
          <p className="text-xs text-muted-foreground">{f.hint}</p>
        </div>
        <Switch id={id} checked={!!value} disabled={disabled} onCheckedChange={(on: boolean) => onChange(on)} />
      </div>
    );
  }

  if (f.kind === "project") {
    const items = [{ value: ALL_PROJECTS, label: "All projects" }, ...projects.map((p) => ({ value: p, label: p }))];
    return (
      <div className="space-y-1.5">
        <Label htmlFor={id} className="font-medium">{f.label}</Label>
        <Select items={items} value={(value as string) || ALL_PROJECTS} disabled={disabled}
          onValueChange={(v) => onChange(v === ALL_PROJECTS || v === null ? "" : (v as string))}>
          <SelectTrigger id={id} className="w-full sm:w-80"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_PROJECTS}>All projects</SelectItem>
            {projects.length > 0 && <SelectSeparator />}
            {projects.map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>
    );
  }

  const listId = f.kind === "text" && f.suggestProjects ? `${id}-projects` : undefined;
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id} className="font-medium">
        {f.label}
        {!f.required && <span className="font-normal text-muted-foreground">(optional)</span>}
      </Label>
      <div className="flex gap-2">
        <Input
          id={id}
          value={(value as string) ?? ""}
          disabled={disabled}
          list={listId}
          placeholder={f.kind === "path" ? (f.browse === "any" ? "/path/to/directory-or-file" : "/path/to/directory") : f.placeholder}
          className="min-w-0 flex-1 font-mono text-sm"
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onSubmit()}
        />
        {f.kind === "path" && (
          <Button variant="outline" disabled={disabled} onClick={onBrowse}>
            <FolderOpen />
            Browse
          </Button>
        )}
      </div>
      {listId && <datalist id={listId}>{projects.map((p) => <option key={p} value={p} />)}</datalist>}
    </div>
  );
}
