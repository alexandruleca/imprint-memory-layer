"use client";

import Link from "next/link";
import { useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Spinner } from "@/components/loaders";
import { enqueueCommand } from "@/lib/api";
import { PathBrowserDialog } from "@/components/path-browser-dialog";
import { Globe, FileText, FolderOpen, RefreshCw, Tags, GraduationCap, X, Play, CheckCircle2 } from "lucide-react";

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
    desc: "Index Claude Code conversation transcripts + memory files",
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

  function selectAction(id: string) {
    if (active === id) {
      setActive(null);
    } else {
      setActive(id);
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
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">
          {ACTIONS.map((a) => {
            const Icon = a.icon;
            const isActive = active === a.id;
            return (
              <button
                key={a.id}
                aria-pressed={isActive}
                onClick={() => selectAction(a.id)}
                className={`relative rounded-lg border p-3 text-left transition-colors ${
                  isActive
                    ? "border-primary bg-muted/40"
                    : "border-border hover:border-muted-foreground/40 hover:bg-muted/20"
                }`}
              >
                <div className="flex items-center gap-2">
                  <div
                    className="w-7 h-7 rounded-md flex items-center justify-center shrink-0 bg-muted text-foreground"
                  >
                    <Icon className="w-4 h-4" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-medium truncate">{a.label}</p>
                    <p className="text-[11px] text-muted-foreground truncate">{a.desc}</p>
                  </div>
                </div>
              </button>
            );
          })}
        </div>

        {activeAction && (
          <div className="space-y-3 pt-2 border-t">
            <div className="flex items-center justify-between">
              <p className="text-xs text-muted-foreground">
                <code className="bg-muted px-1.5 py-0.5 rounded">imprint {activeAction.command}</code>
              </p>
              <button
                onClick={() => setActive(null)}
                className="text-muted-foreground hover:text-foreground"
                aria-label="Close"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="space-y-2">
              {activeAction.fields.filter((f) =>
                !f.showWhen || !!params[f.showWhen.key] === f.showWhen.value
              ).map((f) => {
                const id = `quick-${activeAction.id}-${f.key}`;
                return (
                  <div key={f.key} className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-2">
                    <label htmlFor={id} className="text-xs text-muted-foreground shrink-0 sm:w-36">
                      {f.label}
                    </label>
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
                          className="min-w-0 flex-1 h-8 text-sm"
                        />
                        {f.browse && (
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
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
              <Button onClick={run} size="sm" disabled={submitting}>
                {submitting ? (
                  <Spinner className="w-3.5 h-3.5" />
                ) : (
                  <Play className="w-3.5 h-3.5" />
                )}{" "}
                {submitting ? "Queuing…" : "Run"}
              </Button>
            </div>

            {status.kind === "enqueued" && (
              <div className="flex items-center gap-2 text-xs rounded-md border border-emerald-600/30 bg-emerald-50 px-3 py-2">
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-700 shrink-0" />
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
                  className="text-emerald-700 underline-offset-2 hover:underline shrink-0"
                >
                  view queue
                </Link>
              </div>
            )}
            {status.kind === "error" && (
              <div className="text-xs text-red-700 bg-red-50 border border-red-600/30 rounded-md px-3 py-2">
                {status.message}
              </div>
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
