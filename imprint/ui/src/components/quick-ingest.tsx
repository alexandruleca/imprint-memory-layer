"use client";

import Link from "next/link";
import { useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Spinner } from "@/components/loaders";
import { enqueueCommand } from "@/lib/api";
import { PathBrowserDialog } from "@/components/path-browser-dialog";
import { Globe, FileText, FolderOpen, RefreshCw, Tags, GraduationCap, X, Play, CheckCircle2, CircleAlert } from "lucide-react";

type Command = "ingest-url" | "ingest" | "refresh" | "retag" | "learn";

type Field = {
  key: string;
  label: string;
  placeholder?: string;
  type?: "text" | "checkbox";
  // Offers the server-side path picker for this field.
  browse?: "dir" | "file";
  showWhen?: { key: string; value: boolean };
};

type Action = {
  id: string;
  command: Command;
  label: string;
  desc: string;
  icon: React.ElementType;
  fields: Field[];
  required?: string[];
};

const ACTIONS: Action[] = [
  {
    id: "ingest-url",
    command: "ingest-url",
    label: "Ingest URL",
    desc: "Fetch and index a web page, PDF, or document URL",
    icon: Globe,
    fields: [
      { key: "url", label: "URL", placeholder: "https://example.com/page.html" },
      { key: "project", label: "Project (optional)", placeholder: "my-project" },
      { key: "force", label: "Force re-ingest", type: "checkbox" },
      { key: "follow", label: "Follow links", type: "checkbox" },
      { key: "follow_depth", label: "Follow depth", placeholder: "1", showWhen: { key: "follow", value: true } },
    ],
    required: ["url"],
  },
  {
    id: "ingest-document",
    command: "ingest",
    label: "Ingest Document",
    desc: "Index one PDF, Word, EPUB, or other document file",
    icon: FileText,
    fields: [
      { key: "dir", label: "Document", placeholder: "/home/user/Documents/report.pdf", browse: "file" },
    ],
    required: ["dir"],
  },
  {
    id: "ingest-directory",
    command: "ingest",
    label: "Ingest Directory",
    desc: "Index every supported file in a directory",
    icon: FolderOpen,
    fields: [
      { key: "dir", label: "Directory", placeholder: "/home/user/my-project", browse: "dir" },
    ],
    required: ["dir"],
  },
  {
    id: "refresh",
    command: "refresh",
    label: "Refresh",
    desc: "Re-index only files changed since last ingest",
    icon: RefreshCw,
    fields: [
      { key: "dir", label: "Directory (optional)", placeholder: "/home/user/my-project", browse: "dir" },
    ],
  },
  {
    id: "retag",
    command: "retag",
    label: "Retag",
    desc: "Re-classify existing memories with the LLM tagger",
    icon: Tags,
    fields: [
      { key: "project", label: "Project filter (optional)", placeholder: "my-project" },
      { key: "all", label: "Include already-tagged (--all)", type: "checkbox" },
      { key: "dry_run", label: "Dry run", type: "checkbox" },
    ],
  },
  {
    id: "learn",
    command: "learn",
    label: "Learn",
    desc: "Index Claude Code and Codex conversations (including WSL profiles) and memory files",
    icon: GraduationCap,
    fields: [],
  },
];

type Status =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "enqueued"; jobId: string; position: number }
  | { kind: "error"; message: string };

export function QuickIngest() {
  const [active, setActive] = useState<string | null>(null);
  const [params, setParams] = useState<Record<string, string | boolean>>({});
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [browsing, setBrowsing] = useState<Field | null>(null);

  function selectAction(id: string | undefined) {
    setActive(id ?? null);
    if (id) {
      setParams({});
      setStatus({ kind: "idle" });
    }
  }

  async function run() {
    if (!active || status.kind === "submitting") return;
    const action = ACTIONS.find((a) => a.id === active);
    if (!action) return;

    for (const r of action.required ?? []) {
      if (!params[r]) {
        const label = action.fields.find((f) => f.key === r)?.label ?? r;
        setStatus({ kind: "error", message: `${label} is required` });
        return;
      }
    }

    const body: Record<string, unknown> = {};
    for (const f of action.fields) {
      const v = params[f.key];
      if (f.type === "checkbox") {
        if (v) body[f.key] = true;
      } else if (typeof v === "string" && v.trim()) {
        const n = Number(v.trim());
        body[f.key] = f.key === "follow_depth" && !isNaN(n) ? n : v.trim();
      }
    }

    setStatus({ kind: "submitting" });
    try {
      const { job_id, position } = await enqueueCommand(action.command, body);
      setStatus({ kind: "enqueued", jobId: job_id, position });
    } catch (e) {
      setStatus({
        kind: "error",
        message: e instanceof Error ? e.message : String(e),
      });
    }
  }

  const activeAction = active ? ACTIONS.find((a) => a.id === active) ?? null : null;
  const submitting = status.kind === "submitting";

  return (
    <Card>
      <CardContent className="p-4 space-y-3">
        <ToggleGroup
          aria-label="Quick actions"
          spacing={2}
          value={active ? [active] : []}
          onValueChange={(value) => selectAction(value[0])}
          className="grid w-full grid-cols-1 sm:grid-cols-2 md:grid-cols-3 2xl:grid-cols-6"
        >
          {ACTIONS.map((a) => {
            const Icon = a.icon;
            return (
              <ToggleGroupItem
                key={a.id}
                value={a.id}
                variant="outline"
                className="h-auto min-w-0 justify-start gap-2 border-border p-3 text-left font-normal whitespace-normal hover:border-muted-foreground/40 hover:bg-muted/20 aria-pressed:border-primary aria-pressed:bg-muted/40"
              >
                <span className="grid size-7 shrink-0 place-items-center rounded-md bg-muted text-foreground">
                  <Icon aria-hidden />
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{a.label}</span>
                  <span className="block truncate text-[11px] text-muted-foreground">{a.desc}</span>
                </span>
              </ToggleGroupItem>
            );
          })}
        </ToggleGroup>

        {activeAction && (
          <div className="space-y-3 pt-2 border-t">
            <div className="flex items-center justify-between">
              <p className="text-xs text-muted-foreground">
                <code className="bg-muted px-1.5 py-0.5 rounded">imprint {activeAction.command}</code>
              </p>
              <Button variant="ghost" size="icon-sm" onClick={() => setActive(null)} aria-label="Close">
                <X />
              </Button>
            </div>
            <div className="space-y-2">
              {activeAction.fields.filter((f) =>
                !f.showWhen || !!params[f.showWhen.key] === f.showWhen.value
              ).map((f) => {
                const id = `quick-${activeAction.id}-${f.key}`;
                return (
                  <div key={f.key} className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-2">
                    <Label htmlFor={id} className="text-xs font-normal text-muted-foreground shrink-0 sm:w-36">
                      {f.label}
                    </Label>
                    {f.type === "checkbox" ? (
                      <Switch
                        id={id}
                        checked={!!params[f.key]}
                        onCheckedChange={(checked: boolean) =>
                          setParams({ ...params, [f.key]: checked })
                        }
                      />
                    ) : (
                      <div className="flex min-w-0 flex-1 gap-2">
                        <Input
                          id={id}
                          value={(params[f.key] as string) ?? ""}
                          onChange={(e) =>
                            setParams({ ...params, [f.key]: e.target.value })
                          }
                          placeholder={f.placeholder}
                          className="min-w-0 flex-1 text-sm"
                        />
                        {f.browse && (
                          <Button
                            type="button"
                            variant="outline"
                            onClick={() => setBrowsing(f)}
                          >
                            {f.browse === "file" ? "Browse…" : "Pick folder"}
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
              {activeAction.fields.some((f) => f.browse) && (
                <p className="text-[11px] text-muted-foreground sm:pl-[152px]">
                  Paths are resolved on the server (not in your browser). The
                  picker browses the server&apos;s filesystem — on WSL2 that
                  means WSL paths like <code>/mnt/c/</code>.
                </p>
              )}
            </div>

            <div className="flex items-center gap-2">
              <Button onClick={run} disabled={submitting}>
                {submitting ? <Spinner /> : <Play aria-hidden />}
                {submitting ? "Queuing…" : "Run"}
              </Button>
            </div>

            {status.kind === "enqueued" && (
              <Alert role="status" className="border-emerald-600/30 bg-emerald-50">
                <CheckCircle2 className="text-emerald-700" aria-hidden />
                <AlertDescription className="flex items-center gap-2 text-xs text-foreground">
                  <span className="flex-1">
                    {status.position > 0 ? (
                      <>
                        Queued at position <strong>{status.position}</strong> — will
                        start when the current job finishes.
                      </>
                    ) : (
                      <>Started now.</>
                    )}
                  </span>
                  <Link
                    href="/queue"
                    className="text-emerald-700 no-underline underline-offset-2 hover:underline shrink-0"
                  >
                    view queue
                  </Link>
                </AlertDescription>
              </Alert>
            )}
            {status.kind === "error" && (
              <Alert variant="destructive" className="border-destructive/30 bg-destructive/5">
                <CircleAlert aria-hidden />
                <AlertDescription className="text-xs">{status.message}</AlertDescription>
              </Alert>
            )}
          </div>
        )}
      </CardContent>
      <PathBrowserDialog
        open={browsing !== null}
        mode={browsing?.browse ?? "dir"}
        title={browsing?.browse === "file" ? "Pick a document" : "Pick a folder"}
        initialPath={browsing ? (params[browsing.key] as string) || "" : ""}
        onCancel={() => setBrowsing(null)}
        onSelect={(abs) => {
          if (browsing) setParams((p) => ({ ...p, [browsing.key]: abs }));
          setBrowsing(null);
        }}
      />
    </Card>
  );
}
