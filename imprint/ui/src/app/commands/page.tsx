"use client";

import { useEffect, useRef, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { streamCommand, getConfig } from "@/lib/api";
import { Spinner } from "@/components/loaders";
import { Skeleton } from "@/components/ui/skeleton";
import type { ConfigSetting } from "@/lib/types";

const COMMANDS = [
  { name: "status", label: "Status", desc: "Show server status, memory stats, hook count", fields: [] },
  { name: "retag", label: "Retag Memories", desc: "Re-tag existing chunks with local LLM tagger", fields: [
    { key: "project", label: "Project filter (optional)", type: "text" },
    { key: "dry_run", label: "Dry run", type: "checkbox" },
  ]},
  { name: "ingest", label: "Ingest Directory", desc: "Index files from a directory into memory", fields: [
    { key: "dir", label: "Directory path", type: "text" },
  ]},
  { name: "refresh", label: "Refresh Files", desc: "Re-index only changed files since last index", fields: [
    { key: "dir", label: "Directory path", type: "text" },
  ]},
  { name: "refresh-urls", label: "Refresh URLs", desc: "Re-check stored URLs and re-index changed", fields: [] },
  { name: "config", label: "Show Config", desc: "Show all settings and current values", fields: [] },
  { name: "sync", label: "Sync Export", desc: "Export snapshot bundle for syncing", fields: [
    { key: "action", label: "Action", type: "text", default: "export" },
  ]},
];

export default function CommandsPage() {
  const [selectedCmd, setSelectedCmd] = useState(COMMANDS[0]);
  const [params, setParams] = useState<Record<string, string | boolean>>({});
  const [output, setOutput] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [settings, setSettings] = useState<ConfigSetting[]>([]);
  const [configLoading, setConfigLoading] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getConfig().then((d) => setSettings(d.settings)).catch(() => {}).finally(() => setConfigLoading(false));
  }, []);

  function runCommand() {
    if (running) return;
    setRunning(true);
    setOutput([]);

    const body: Record<string, unknown> = {};
    for (const f of selectedCmd.fields) {
      const val = params[f.key];
      if (val !== undefined && val !== "") {
        body[f.key] = val;
      } else if ("default" in f) {
        body[f.key] = (f as { default: string }).default;
      }
    }

    streamCommand(selectedCmd.name, body, (ev) => {
      if (ev.type === "output") {
        setOutput((prev) => [...prev, ev.text as string]);
      } else if (ev.type === "done") {
        const code = ev.exit_code as number;
        setOutput((prev) => [...prev, `\n--- Exit code: ${code} ---\n`]);
        setRunning(false);
      } else if (ev.type === "error") {
        setOutput((prev) => [...prev, `ERROR: ${ev.error}\n`]);
        setRunning(false);
      }
      scrollRef.current?.scrollIntoView();
    });
  }

  // Group config settings
  const configGroups: Record<string, ConfigSetting[]> = {};
  for (const s of settings) {
    const group = s.key.split(".")[0];
    (configGroups[group] ||= []).push(s);
  }

  return (
    <div className="p-8 space-y-6">
      <h2 className="text-2xl font-bold">Commands</h2>

      <div className="grid grid-cols-3 gap-4">
        <div className="space-y-2">
          {COMMANDS.map((cmd) => (
            <Card
              key={cmd.name}
              className={`cursor-pointer transition-colors ${selectedCmd.name === cmd.name ? "border-primary" : "hover:border-muted-foreground/30"}`}
              onClick={() => { setSelectedCmd(cmd); setParams({}); }}
            >
              <CardContent className="p-3">
                <p className="text-sm font-medium">{cmd.label}</p>
                <p className="text-xs text-muted-foreground">{cmd.desc}</p>
              </CardContent>
            </Card>
          ))}
        </div>

        <div className="col-span-2 space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">
                <code className="text-xs bg-muted px-1.5 py-0.5 rounded mr-2">imprint {selectedCmd.name}</code>
                {selectedCmd.label}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {selectedCmd.fields.map((f) => (
                <div key={f.key} className="flex items-center gap-2">
                  <label className="text-sm w-36 text-muted-foreground">{f.label}</label>
                  {f.type === "checkbox" ? (
                    <Switch
                      checked={!!params[f.key]}
                      onCheckedChange={(checked: boolean) => setParams({ ...params, [f.key]: checked })}
                    />
                  ) : (
                    <Input
                      value={(params[f.key] as string) ?? ("default" in f ? (f as { default: string }).default : "")}
                      onChange={(e) => setParams({ ...params, [f.key]: e.target.value })}
                      className="flex-1 h-8 text-sm"
                    />
                  )}
                </div>
              ))}
              <button
                onClick={runCommand}
                disabled={running}
                className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50 flex items-center gap-2"
              >
                {running && <Spinner className="w-3.5 h-3.5" />}
                {running ? "Running..." : "Run"}
              </button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-sm">Output</CardTitle></CardHeader>
            <CardContent>
              <ScrollArea className="h-72">
                <pre className="text-xs font-mono whitespace-pre-wrap bg-muted p-3 rounded min-h-[100px]">
                  {output.join("") || "Click Run to execute the command."}
                  <span ref={scrollRef} />
                </pre>
              </ScrollArea>
            </CardContent>
          </Card>
        </div>
      </div>

      <Separator />

      <div>
        <h3 className="text-lg font-bold mb-4">Current Configuration</h3>
        <p className="text-sm text-muted-foreground mb-4">
          Change values via <code className="bg-muted px-1 rounded">imprint config set &lt;key&gt; &lt;value&gt;</code> or the Settings page.
        </p>
        <div className="grid grid-cols-2 gap-4">
          {configLoading && Array.from({ length: 4 }).map((_, i) => (
            <Card key={i}>
              <CardHeader className="pb-2"><Skeleton className="h-4 w-20" /></CardHeader>
              <CardContent className="space-y-2">
                {Array.from({ length: 3 }).map((_, j) => (
                  <div key={j} className="flex items-center gap-2">
                    <Skeleton className="h-3 w-32" />
                    <Skeleton className="h-3 flex-1" />
                  </div>
                ))}
              </CardContent>
            </Card>
          ))}
          {!configLoading && Object.entries(configGroups).map(([group, items]) => (
            <Card key={group}>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm capitalize">{group}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-1.5">
                  {items.map((s) => (
                    <div key={s.key} className="flex items-start gap-2 text-xs">
                      <code className="text-muted-foreground shrink-0 w-44 truncate" title={s.key}>
                        {s.key}
                      </code>
                      <span className="font-mono truncate flex-1" title={String(s.value)}>
                        {String(s.value)}
                      </span>
                      <Badge
                        variant={s.source === "default" ? "secondary" : "outline"}
                        className="text-[10px] shrink-0"
                      >
                        {s.source}
                      </Badge>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
