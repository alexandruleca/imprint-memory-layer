"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Empty, EmptyDescription } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Spinner } from "@/components/loaders";
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
  const fieldId = useId();
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
          if (ev.status === "failed" || ev.status === "cancelled") {
            toast.error(`${transfer === "copy" ? "Copy" : "Move"} ${ev.status}`, { description: "The log shows what happened." });
          } else if (!dryRun) {
            setDone(true);
            toast.success(`${transfer === "copy" ? "Copied" : "Moved"} ${selection} to ${toWs}`);
          }
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

  const label = (w: string) => (w === activeWorkspace ? `${w} (active)` : w);

  return (
    <Dialog open onOpenChange={(next) => { if (!next && !busy) handleClose(); }}>
      <DialogContent showCloseButton={false} aria-busy={busy} className="sm:max-w-2xl">
        <DialogTitle>{title}</DialogTitle>

        {!presetLocked && (
          <ToggleGroup
            aria-label="Select by"
            size="sm"
            variant="outline"
            value={[mode]}
            disabled={busy}
            onValueChange={(value) => {
              // Selecting the current mode again would leave none selected.
              if (!value[0]) return;
              setMode(value[0] as Mode);
              setSelection("");
              setFilter("");
            }}
          >
            {(["project", "topic", "source"] as Mode[]).map((m) => (
              <ToggleGroupItem key={m} value={m}>By {m}</ToggleGroupItem>
            ))}
          </ToggleGroup>
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
          <div className="space-y-1">
            <Label htmlFor={`${fieldId}-from`} className="text-xs font-normal text-muted-foreground">From</Label>
            <Select items={workspaces.map((w) => ({ value: w, label: label(w) }))} value={fromWs} disabled={busy}
              onValueChange={(value) => setFromWs(value as string)}>
              <SelectTrigger id={`${fieldId}-from`} className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {workspaces.map((w) => <SelectItem key={w} value={w}>{label(w)}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor={`${fieldId}-to`} className="text-xs font-normal text-muted-foreground">To</Label>
            <Select value={toWs || null} disabled={busy} onValueChange={(value) => setToWs((value as string | null) ?? "")}>
              <SelectTrigger id={`${fieldId}-to`} className="w-full"><SelectValue placeholder="Select a workspace" /></SelectTrigger>
              <SelectContent>
                {workspaces.filter((w) => w !== fromWs).map((w) => <SelectItem key={w} value={w}>{w}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="space-y-1">
          <p id={`${fieldId}-transfer`} className="text-xs text-muted-foreground">Copy or move</p>
          <RadioGroup aria-labelledby={`${fieldId}-transfer`} value={transfer} disabled={busy}
            onValueChange={(value) => setTransfer(value as Transfer)} className="grid gap-2 sm:grid-cols-2">
            {TRANSFERS.map((option) => (
              <Label
                key={option.value}
                className={`items-start rounded border p-2 text-xs font-normal leading-normal cursor-pointer has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-primary ${
                  transfer === option.value ? "border-primary bg-primary/5" : "border-border hover:bg-muted/40"
                }`}
              >
                <RadioGroupItem value={option.value} className="mt-0.5" />
                <span>
                  <span className="block font-medium">{option.label}</span>
                  <span className="text-muted-foreground">{option.detail}</span>
                </span>
              </Label>
            ))}
          </RadioGroup>
        </div>

        {!presetLocked && (
          <div className="space-y-1">
            <span className="text-xs text-muted-foreground capitalize">
              {mode} to {transfer}
            </span>
            <Input
              aria-label={`Filter ${mode}s`}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={`Filter ${mode}s…`}
              className="text-sm"
              disabled={busy}
            />
            <div className="max-h-44 overflow-y-auto rounded border border-border p-1">
              {filtered.length === 0 ? (
                <Empty className="p-2">
                  <EmptyDescription className="text-xs">No matches</EmptyDescription>
                </Empty>
              ) : (
                filtered.map((opt) => (
                  <Button
                    key={opt}
                    variant="ghost"
                    size="xs"
                    aria-pressed={selection === opt}
                    onClick={() => setSelection(opt)}
                    disabled={busy}
                    className={`w-full justify-start font-mono font-normal ${selection === opt ? "bg-muted" : ""}`}
                  >
                    <span className="truncate">{opt}</span>
                  </Button>
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

        <DialogFooter className="sm:justify-between">
          <Button
            variant="ghost"
            onClick={handleClose}
            disabled={busy}
          >
            {done ? "Close" : "Cancel"}
          </Button>
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => run(true)}
              disabled={!canRun}
            >
              Dry run
            </Button>
            <Button
              onClick={() => run(false)}
              disabled={!canRun}
            >
              {busy && <Spinner />}
              {busy ? "Running…" : transfer === "copy" ? "Copy" : "Move"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
