"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Activity, Check, Copy, Eraser, FolderInput, FolderOpen, Globe, ListOrdered, MessagesSquare, PackageOpen, Play, Plug,
  RefreshCw, RotateCw, SlidersHorizontal, Square, Stethoscope, Tags, TerminalSquare, Unplug, type LucideIcon,
} from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectSeparator, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Hint } from "@/components/hint";
import { Spinner } from "@/components/loaders";
import { PageHeader, PaneHeading, PaneItem, SplitPage } from "@/components/split-page";
import { PathBrowserDialog } from "@/components/path-browser-dialog";
import {
  getCommandCatalog, getStats, streamCommand,
  type CatalogCommand, type ClientTarget, type CommandCatalog, type CommandField,
} from "@/lib/api";
import { useFollowOutput } from "@/lib/use-follow-output";
import { cn } from "@/lib/utils";

// The commands, their fields and the clients come from GET /api/commands
// (imprint/commands.py and `imprint clients`); only their icons live here.
const ICONS: Record<string, LucideIcon> = {
  learn: MessagesSquare, ingest: FolderInput, refresh: RefreshCw, "ingest-url": Globe, "refresh-urls": RotateCw,
  retag: Tags, sync: PackageOpen, connect: Plug, disconnect: Unplug, status: Activity, doctor: Stethoscope,
  config: SlidersHorizontal,
};

function CommandIcon({ name, className }: { name: string; className?: string }) {
  const Icon = ICONS[name] ?? TerminalSquare;
  return <Icon className={className} />;
}

const ALL_PROJECTS = "\u0000all";
const ALL_CLIENTS = "all";

type Params = Record<string, string | boolean>;
type Run = { state: "idle" | "running" | "cancelled" | "error" } | { state: "done"; code: number };

/** What a field's value reads as, for the confirmation; "" when unset. */
function describe(field: CommandField, value: string | boolean | undefined, clients: ClientTarget[]): string {
  if (field.kind === "switch") return value ? "Yes" : "";
  if (field.kind === "client") {
    const name = (value as string) || ALL_CLIENTS;
    return name === ALL_CLIENTS ? "All clients" : clients.find((c) => c.name === name)?.label ?? name;
  }
  if (field.kind === "project") return (value as string) || "All projects";
  return String(value ?? "").trim();
}

export default function CommandsPage() {
  const [catalog, setCatalog] = useState<CommandCatalog | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [params, setParams] = useState<Params>({});
  const [output, setOutput] = useState("");
  const [run, setRun] = useState<Run>({ state: "idle" });
  const [projects, setProjects] = useState<string[]>([]);
  const [browsing, setBrowsing] = useState<Extract<CommandField, { kind: "path" }> | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [copied, setCopied] = useState(false);
  const controller = useRef<ReturnType<typeof streamCommand> | null>(null);
  const running = run.state === "running";

  // A failed reload keeps the catalog already shown; only a first load fails the page.
  const loadCatalog = useCallback(() => {
    getCommandCatalog()
      .then((c) => { setCatalog(c); setCatalogError(""); })
      .catch((e) => setCatalogError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    loadCatalog();
    getStats().then((s) => setProjects((s.projects ?? []).map(([name]) => name))).catch(() => {});
    return () => controller.current?.abort();
  }, [loadCatalog]);

  const log = useFollowOutput(output);

  const commands = catalog?.commands ?? [];
  const clients = catalog?.clients ?? [];
  const groups = [...new Set(commands.map((c) => c.group))];
  const selected = commands.find((c) => c.name === selectedName) ?? commands[0];

  const missing = !selected || selected.fields.some((f) => "required" in f && f.required && !String(params[f.key] ?? "").trim());

  function choose(command: CatalogCommand) {
    if (running || command.name === selected?.name) return;
    setSelectedName(command.name);
    setParams({});
  }

  function body(command: CatalogCommand) {
    const result: Record<string, unknown> = { ...command.fixed };
    for (const f of command.fields) {
      const value = params[f.key];
      if (f.kind === "client") result[f.key] = (value as string) || ALL_CLIENTS;
      else if (typeof value === "string" ? value.trim() : value) result[f.key] = typeof value === "string" ? value.trim() : value;
    }
    return result;
  }

  function start() {
    if (running || missing || !selected) return;
    if (selected.confirm) setConfirming(true);
    else launch(selected);
  }

  function launch(command: CatalogCommand) {
    setOutput("");
    setRun({ state: "running" });
    controller.current = streamCommand(command.name, body(command), (ev) => {
      if (ev.type === "output") {
        setOutput((prev) => prev + (ev.text as string));
      } else if (ev.type === "merged") {
        // An identical job was already waiting (ADR 0068); this follows it.
        setOutput((prev) => `${prev}An identical job was already waiting; following it instead of queueing another.\n`);
      } else if (ev.type === "done") {
        setRun({ state: "done", code: ev.exit_code as number });
        // A finished job can change what the catalog reports, such as which clients are connected.
        loadCatalog();
      } else if (ev.type === "error") {
        setOutput((prev) => `${prev}${prev && !prev.endsWith("\n") ? "\n" : ""}ERROR: ${ev.error}\n`);
        setRun({ state: "error" });
      }
    }, false);
  }

  function cancel() {
    controller.current?.cancel();
    setRun({ state: "cancelled" });
  }

  async function copy() {
    await navigator.clipboard.writeText(output);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <SplitPage
      fill
      header={
        <PageHeader
          title="Commands"
          description="Run Imprint's CLI commands on this device, and connect or disconnect your AI clients. Jobs run one at a time through the queue."
          actions={
            <Button variant="outline" size="sm" nativeButton={false} render={<Link href="/queue/" />}>
              <ListOrdered />
              Open queue
            </Button>
          }
        />
      }
      list={
        <nav aria-label="Commands" aria-busy={!catalog && !catalogError}>
          {!catalog && !catalogError && Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="m-1 h-12" />)}
          {groups.map((group) => (
            <div key={group}>
              <PaneHeading>{group}</PaneHeading>
              <div className="space-y-0.5">
                {commands.filter((c) => c.group === group).map((c) => {
                  const active = c.name === selected?.name;
                  return (
                    <PaneItem key={c.name} selected={active} disabled={running && !active} onClick={() => choose(c)} className="items-start">
                      <CommandIcon name={c.name} className={cn("mt-0.5 size-4 shrink-0", active ? "text-foreground" : "text-muted-foreground")} />
                      <span className="min-w-0">
                        <span className="block">{c.label}</span>
                        <span className="block text-xs font-normal text-muted-foreground">{c.description}</span>
                      </span>
                    </PaneItem>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>
      }
    >
      {!catalog && catalogError && (
        <Alert variant="destructive">
          <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
            <span>Commands could not be loaded: {catalogError}</span>
            <Button variant="outline" size="sm" onClick={loadCatalog}>
              <RotateCw />
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {selected ? (
        <Card className="shrink-0">
          <CardHeader className="flex items-start gap-3">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-foreground">
              <CommandIcon name={selected.name} className="size-4" />
            </span>
            <div className="min-w-0 space-y-1">
              <CardTitle>{selected.label}</CardTitle>
              <CardDescription>{selected.description}</CardDescription>
              <code className="inline-block rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground">
                imprint {[selected.name, ...Object.values(selected.fixed ?? {})].join(" ")}
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
                  clients={clients}
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
              <Button variant={selected.confirm ? "destructive" : "default"} onClick={start} disabled={missing}>
                <Play />
                Run
              </Button>
            )}
          </CardFooter>
        </Card>
      ) : (
        !catalogError && <Skeleton className="h-40 w-full shrink-0" />
      )}

      <Card className="h-96 shrink-0 gap-0 py-0 lg:h-auto lg:min-h-64 lg:shrink lg:flex-1">
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
        <ScrollArea className="min-h-0 flex-1 bg-muted/30" viewportRef={log}>
          <pre aria-live="polite" className="p-4 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words">
            {output || <span className="text-muted-foreground">{running ? "Waiting for output…" : "Run a command to see its output here."}</span>}
          </pre>
        </ScrollArea>
      </Card>

      <PathBrowserDialog
        open={browsing !== null}
        mode={browsing?.browse ?? "dir"}
        title={browsing?.browse === "any" ? "Choose a directory or file" : "Choose a directory"}
        initialPath={browsing ? String(params[browsing.key] ?? "") : undefined}
        onSelect={(path) => { if (browsing) setParams((p) => ({ ...p, [browsing.key]: path })); setBrowsing(null); }}
        onCancel={() => setBrowsing(null)}
      />

      <AlertDialog open={confirming} onOpenChange={(open) => { if (!open) setConfirming(false); }}>
        {selected?.confirm && (
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{selected.label}?</AlertDialogTitle>
              <AlertDialogDescription>{selected.confirm}</AlertDialogDescription>
            </AlertDialogHeader>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
              {selected.fields.map((f) => {
                const text = describe(f, params[f.key], clients);
                return text && (
                  <div key={f.key} className="contents">
                    <dt className="text-muted-foreground">{f.label}</dt>
                    <dd className="font-medium wrap-anywhere">{text}</dd>
                  </div>
                );
              })}
            </dl>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <Button variant="destructive" onClick={() => { setConfirming(false); launch(selected); }}>
                {selected.label}
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        )}
      </AlertDialog>
    </SplitPage>
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

function FieldInput({ id, field: f, value, projects, clients, disabled, onChange, onBrowse, onSubmit }: {
  id: string;
  field: CommandField;
  value: string | boolean | undefined;
  projects: string[];
  clients: ClientTarget[];
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

  if (f.kind === "client") {
    const items = [{ value: ALL_CLIENTS, label: "All clients" }, ...clients.map((c) => ({ value: c.name, label: c.label }))];
    return (
      <div className="space-y-1.5">
        <Label htmlFor={id} className="font-medium">{f.label}</Label>
        <Select items={items} value={(value as string) || ALL_CLIENTS} disabled={disabled}
          onValueChange={(v) => onChange((v as string | null) ?? ALL_CLIENTS)}>
          <SelectTrigger id={id} className="w-full sm:w-80"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_CLIENTS}>All clients</SelectItem>
            {clients.length > 0 && <SelectSeparator />}
            {clients.map((c) => (
              <SelectItem key={c.name} value={c.name}>
                {c.label}
                <span className="ml-auto pl-4 text-xs text-muted-foreground">
                  {c.error ? "Settings unreadable" : c.connected ? "Connected" : "Not connected"}
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">Clients that are not installed on this device are skipped.</p>
      </div>
    );
  }

  const listId = f.kind === "text" && f.suggest_projects ? `${id}-projects` : undefined;
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
