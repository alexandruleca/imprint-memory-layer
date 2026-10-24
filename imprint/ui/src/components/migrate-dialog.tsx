"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/modal";
import {
  getOverview,
  getTopics,
  getWorkspaces,
  listSources,
  migrateContent,
} from "@/lib/api";

type Mode = "project" | "topic" | "source";
type Transfer = "copy" | "move";

const TRANSFERS: { value: Transfer; label: string; detail: string }[] = [
  { value: "copy", label: "Copy", detail: "The source workspace keeps its memories — use this to curate a workspace to share." },
  { value: "move", label: "Move", detail: "Memories are removed from the source workspace after they are copied." },
];

export type MigratePreset = {
  mode: Mode;
  value: string;
};

type Props = {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
  workspaces?: string[];
  activeWorkspace?: string;
  preset?: MigratePreset;
};

export function MigrateDialog(props: Props) {
  return props.open ? <MigrateDialogContent {...props} /> : null;
}

function MigrateDialogContent({
  open,
  onClose,
  onDone,
  workspaces: wsProp,
  activeWorkspace: activeProp,
  preset,
}: Props) {
  const [workspaces, setWorkspaces] = useState<string[]>(wsProp ?? []);
  const [activeWorkspace, setActiveWorkspace] = useState<string>(
    activeProp ?? "",
  );

  const [mode, setMode] = useState<Mode>(preset?.mode ?? "project");
  const [fromWs, setFromWs] = useState(activeProp ?? wsProp?.[0] ?? "");
  const [toWs, setToWs] = useState(wsProp?.find((name) => name !== (activeProp ?? wsProp?.[0])) ?? "");
  const [projects, setProjects] = useState<string[]>([]);
  const [topics, setTopics] = useState<string[]>([]);
  const [sources, setSources] = useState<string[]>([]);
  const [selection, setSelection] = useState(preset?.value ?? "");
  // Copy is the non-destructive default here; the CLI keeps move as its default.
  const [transfer, setTransfer] = useState<Transfer>("copy");
  const transferName = useId();
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (wsProp?.length) return;
    let cancelled = false;
    getWorkspaces().then((state) => {
      if (cancelled) return;
      setWorkspaces(state.workspaces);
      setActiveWorkspace(state.active);
      setFromWs(state.active);
      setToWs(state.workspaces.find((name) => name !== state.active) ?? "");
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [wsProp]);

  // Load option lists once open (skip when we have a preset)
  useEffect(() => {
    if (!open || preset) return;
    if (mode === "project") {
      getOverview()
        .then((d) => setProjects(d.projects.map((p) => p.name)))
        .catch(() => {});
    } else if (mode === "topic") {
      getTopics()
        .then((d) => setTopics(d.topics.map((t) => t.name)))
        .catch(() => {});
    } else if (mode === "source") {
      listSources({ limit: 2000 })
        .then((d) => setSources(d.sources.map((s) => s.source)))
        .catch(() => {});
    }
  }, [open, mode, preset]);

  const options =
    mode === "project" ? projects : mode === "topic" ? topics : sources;
  const filtered = useMemo(() => {
    if (!filter) return options.slice(0, 50);
    const f = filter.toLowerCase();
    return options.filter((o) => o.toLowerCase().includes(f)).slice(0, 50);
  }, [options, filter]);

  const canRun =
    !busy && !done && fromWs && toWs && fromWs !== toWs && selection;

  function run(dryRun: boolean) {
    setBusy(true);
    setLog([]);
    setDone(false);
    migrateContent(
      {
        from: fromWs,
        to: toWs,
        project: mode === "project" ? selection : undefined,
        topic: mode === "topic" ? selection : undefined,
        source: mode === "source" ? selection : undefined,
        copy: transfer === "copy",
        dryRun,
      },
      (ev) => {
        if (ev.type === "output" && typeof ev.text === "string") {
          setLog((l) => [...l, ev.text as string]);
        } else if (ev.type === "done") {
          setBusy(false);
          if (!dryRun) setDone(true);
        } else if (ev.type === "error") {
          setLog((l) => [...l, `ERROR: ${ev.error}`]);
          setBusy(false);
        }
      },
    );
  }

  function handleClose() {
    if (done) onDone();
    onClose();
  }

  if (!open) return null;

  const presetLocked = Boolean(preset);
  const title = preset ? `Copy or move ${preset.mode}` : "Copy or move between workspaces";

  return (
    <Modal label={title} onClose={handleClose} busy={busy} className="max-w-2xl space-y-4 p-5">
        <h3 className="text-base font-semibold">{title}</h3>

        {!presetLocked && (
          <div className="flex gap-2 text-xs">
            {(["project", "topic", "source"] as Mode[]).map((m) => (
              <button
                key={m}
                onClick={() => {
                  setMode(m);
                  setSelection("");
                  setFilter("");
                }}
                className={`px-3 py-1 rounded ${
                  mode === m
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted hover:bg-muted/80"
                }`}
                disabled={busy}
              >
                By {m}
              </button>
            ))}
          </div>
        )}

        {presetLocked && (
          <div className="rounded border border-border bg-muted/40 px-3 py-2 text-xs">
            <span className="text-muted-foreground capitalize">
              {preset!.mode}:
            </span>{" "}
            <span className="font-mono">{preset!.value}</span>
          </div>
        )}

        <div className="grid grid-cols-2 gap-3 text-sm">
          <label className="space-y-1">
            <span className="text-xs text-muted-foreground">From</span>
            <select
              value={fromWs}
              onChange={(e) => setFromWs(e.target.value)}
              className="w-full bg-muted rounded px-2 py-1 text-sm"
              disabled={busy}
            >
              {workspaces.map((w) => (
                <option key={w} value={w}>
                  {w}
                  {w === activeWorkspace ? " (active)" : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1">
            <span className="text-xs text-muted-foreground">To</span>
            <select
              value={toWs}
              onChange={(e) => setToWs(e.target.value)}
              className="w-full bg-muted rounded px-2 py-1 text-sm"
              disabled={busy}
            >
              <option value="">— select —</option>
              {workspaces
                .filter((w) => w !== fromWs)
                .map((w) => (
                  <option key={w} value={w}>
                    {w}
                  </option>
                ))}
            </select>
          </label>
        </div>

        <fieldset disabled={busy}>
          <legend className="text-xs text-muted-foreground mb-1">Copy or move</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {TRANSFERS.map((option) => (
              <label
                key={option.value}
                className={`flex items-start gap-2 rounded border p-2 text-xs cursor-pointer has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-primary ${
                  transfer === option.value ? "border-primary bg-primary/5" : "border-border hover:bg-muted/40"
                }`}
              >
                <input
                  type="radio"
                  name={transferName}
                  value={option.value}
                  checked={transfer === option.value}
                  onChange={() => setTransfer(option.value)}
                  className="mt-0.5 accent-primary"
                />
                <span>
                  <span className="block font-medium">{option.label}</span>
                  <span className="text-muted-foreground">{option.detail}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        {!presetLocked && (
          <div className="space-y-1">
            <span className="text-xs text-muted-foreground capitalize">
              {mode} to {transfer}
            </span>
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={`Filter ${mode}s…`}
              className="h-8 text-sm"
              disabled={busy}
            />
            <div className="max-h-44 overflow-y-auto border border-border rounded">
              {filtered.length === 0 ? (
                <div className="p-2 text-xs text-muted-foreground">
                  no matches
                </div>
              ) : (
                filtered.map((opt) => (
                  <button
                    key={opt}
                    onClick={() => setSelection(opt)}
                    disabled={busy}
                    className={`w-full text-left px-2 py-1 text-xs font-mono hover:bg-muted ${
                      selection === opt ? "bg-muted" : ""
                    }`}
                  >
                    {opt}
                  </button>
                ))
              )}
            </div>
          </div>
        )}

        {log.length > 0 && (
          <pre className="max-h-32 overflow-auto bg-muted rounded p-2 text-xs font-mono whitespace-pre-wrap">
            {log.join("")}
          </pre>
        )}

        <div className="flex justify-between gap-2 pt-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleClose}
            disabled={busy}
          >
            {done ? "Close" : "Cancel"}
          </Button>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => run(true)}
              disabled={!canRun}
            >
              Dry run
            </Button>
            <Button
              size="sm"
              onClick={() => run(false)}
              disabled={!canRun}
            >
              {busy ? "Running…" : transfer === "copy" ? "Copy" : "Move"}
            </Button>
          </div>
        </div>
    </Modal>
  );
}
