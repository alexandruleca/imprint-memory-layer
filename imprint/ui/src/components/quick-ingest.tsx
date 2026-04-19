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
import { Globe, FolderOpen, RefreshCw, Tags, GraduationCap, X, Play, CheckCircle2 } from "lucide-react";

type ActionKey = "ingest-url" | "ingest" | "refresh" | "retag" | "learn";

type Field = {
  key: string;
  label: string;
  placeholder?: string;
  type?: "text" | "checkbox";
};

type Action = {
  key: ActionKey;
  label: string;
  desc: string;
  icon: React.ElementType;
  accent: string;
  fields: Field[];
  required?: string[];
};

const ACTIONS: Action[] = [
  {
    key: "ingest-url",
    label: "Ingest URL",
    desc: "Fetch and index a web page, PDF, or document URL",
    icon: Globe,
    accent: "#60a5fa",
    fields: [
      { key: "url", label: "URL", placeholder: "https://example.com/page.html" },
      { key: "project", label: "Project (optional)", placeholder: "my-project" },
      { key: "force", label: "Force re-ingest", type: "checkbox" },
    ],
    required: ["url"],
  },
  {
    key: "ingest",
    label: "Ingest Directory",
    desc: "Index files from a directory or single file",
    icon: FolderOpen,
    accent: "#4ecdc4",
    fields: [
      { key: "dir", label: "Path", placeholder: "/home/user/my-project" },
    ],
    required: ["dir"],
  },
  {
    key: "refresh",
    label: "Refresh",
    desc: "Re-index only files changed since last ingest",
    icon: RefreshCw,
    accent: "#f59e0b",
    fields: [
      { key: "dir", label: "Path (optional)", placeholder: "/home/user/my-project" },
    ],
  },
  {
    key: "retag",
    label: "Retag",
    desc: "Re-classify existing memories with the LLM tagger",
    icon: Tags,
    accent: "#a78bfa",
    fields: [
      { key: "project", label: "Project filter (optional)", placeholder: "my-project" },
      { key: "all", label: "Include already-tagged (--all)", type: "checkbox" },
      { key: "dry_run", label: "Dry run", type: "checkbox" },
    ],
  },
  {
    key: "learn",
    label: "Learn",
    desc: "Index Claude Code conversation transcripts + memory files",
    icon: GraduationCap,
    accent: "#34d399",
    fields: [],
  },
];

type Status =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "enqueued"; jobId: string; position: number }
  | { kind: "error"; message: string };

export function QuickIngest() {
  const [active, setActive] = useState<ActionKey | null>(null);
  const [params, setParams] = useState<Record<string, string | boolean>>({});
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [browserMode, setBrowserMode] = useState<"dir" | "any" | null>(null);

  function selectAction(key: ActionKey) {
    if (active === key) {
      setActive(null);
    } else {
      setActive(key);
      setParams({});
      setStatus({ kind: "idle" });
    }
  }

  async function run() {
    if (!active || status.kind === "submitting") return;
    const action = ACTIONS.find((a) => a.key === active);
    if (!action) return;

    for (const r of action.required ?? []) {
      if (!params[r]) {
        setStatus({ kind: "error", message: `${r} is required` });
        return;
      }
    }

    const body: Record<string, unknown> = {};
    for (const f of action.fields) {
      const v = params[f.key];
      if (f.type === "checkbox") {
        if (v) body[f.key] = true;
      } else if (typeof v === "string" && v.trim()) {
        body[f.key] = v.trim();
      }
    }

    setStatus({ kind: "submitting" });
    try {
      const { job_id, position } = await enqueueCommand(action.key, body);
      setStatus({ kind: "enqueued", jobId: job_id, position });
    } catch (e) {
      setStatus({
        kind: "error",
        message: e instanceof Error ? e.message : String(e),
      });
    }
  }

  const activeAction = active ? ACTIONS.find((a) => a.key === active) ?? null : null;
  const submitting = status.kind === "submitting";

  return (
    <Card>
      <CardContent className="p-4 space-y-3">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
          {ACTIONS.map((a) => {
            const Icon = a.icon;
            const isActive = active === a.key;
            return (
              <button
                key={a.key}
                onClick={() => selectAction(a.key)}
                className={`relative rounded-lg border p-3 text-left transition-colors ${
                  isActive
                    ? "border-primary bg-muted/40"
                    : "border-border hover:border-muted-foreground/40 hover:bg-muted/20"
                }`}
              >
                <div className="flex items-center gap-2">
                  <div
                    className="w-7 h-7 rounded-md flex items-center justify-center shrink-0"
                    style={{ backgroundColor: `${a.accent}22`, color: a.accent }}
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
                <code className="bg-muted px-1.5 py-0.5 rounded">imprint {activeAction.key}</code>
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
              {activeAction.fields.map((f) => (
                <div key={f.key} className="flex items-center gap-2">
                  <label className="text-xs w-36 text-muted-foreground shrink-0">
                    {f.label}
                  </label>
                  {f.type === "checkbox" ? (
                    <Switch
                      checked={!!params[f.key]}
                      onCheckedChange={(checked: boolean) =>
                        setParams({ ...params, [f.key]: checked })
                      }
                    />
                  ) : (
                    <div className="flex flex-1 gap-2">
                      <Input
                        value={(params[f.key] as string) ?? ""}
                        onChange={(e) =>
                          setParams({ ...params, [f.key]: e.target.value })
                        }
                        placeholder={f.placeholder}
                        className="flex-1 h-8 text-sm"
                      />
                      {(activeAction.key === "ingest" ||
                        activeAction.key === "refresh") &&
                        f.key === "dir" && (
                          <>
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => setBrowserMode("dir")}
                            >
                              Pick dir
                            </Button>
                            {activeAction.key === "ingest" && (
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={() => setBrowserMode("any")}
                              >
                                Pick file
                              </Button>
                            )}
                          </>
                        )}
                    </div>
                  )}
                </div>
              ))}
              {(activeAction.key === "ingest" ||
                activeAction.key === "refresh") && (
                <p className="text-[11px] text-muted-foreground pl-[152px]">
                  Paths are resolved on the server (not in your browser). The
                  picker browses the server&apos;s filesystem — on WSL2 that
                  means WSL paths like <code>/mnt/c/...</code>.
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
              <div className="flex items-center gap-2 text-xs rounded-md border border-emerald-500/30 bg-emerald-950/10 px-3 py-2">
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
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
                  className="text-emerald-400 underline-offset-2 hover:underline shrink-0"
                >
                  view queue
                </Link>
              </div>
            )}
            {status.kind === "error" && (
              <div className="text-xs text-red-400 bg-red-950/20 border border-red-500/30 rounded-md px-3 py-2">
                {status.message}
              </div>
            )}
          </div>
        )}
      </CardContent>
      <PathBrowserDialog
        open={browserMode !== null}
        mode={browserMode ?? "dir"}
        initialPath={(params.dir as string) || ""}
        onCancel={() => setBrowserMode(null)}
        onSelect={(abs) => {
          setParams((p) => ({ ...p, dir: abs }));
          setBrowserMode(null);
        }}
      />
    </Card>
  );
}
