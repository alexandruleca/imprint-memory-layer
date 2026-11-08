"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { ArrowRight, Check, CircleAlert, CircleDashed, FolderTree, Search, Tags, FileText, X } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectSeparator, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Spinner } from "@/components/loaders";
import {
  getProjects,
  getTopics,
  getWorkspaces,
  listAllSources,
  migrateContent,
  previewMigrate,
  type MigrateMode,
  type MigratePreview,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type Transfer = "copy" | "move";
type Option = { value: string; label: string; count: number };

const NOUN: Record<MigrateMode, [string, string]> = {
  project: ["project", "projects"], topic: ["topic", "topics"], source: ["source", "sources"],
};
const ICON = { project: FolderTree, topic: Tags, source: FileText } as const;
/** config.validate_workspace_name. */
const WORKSPACE_NAME = /^[a-z0-9][a-z0-9-]{0,39}$/;
const NEW = "\u0000new";
/** Options shown at once; the search narrows the rest. */
const SHOWN = 100;

const n = (value: number) => value.toLocaleString();
const plural = (count: number, one: string, many = `${one}s`) => `${n(count)} ${count === 1 ? one : many}`;

const TRANSFERS: { value: Transfer; label: string; detail: (from: string) => string }[] = [
  { value: "copy", label: "Copy", detail: (from) => `${from || "This workspace"} keeps everything. Use it to build a workspace to share.` },
  { value: "move", label: "Move", detail: (from) => `Taken out of ${from || "this workspace"} once it is in the target.` },
];

export type MigratePreset = {
  mode: MigrateMode;
  /** One value (kept for callers that pass a single name)… */
  value?: string;
  /** …or several of the same kind. */
  values?: string[];
};

type Props = {
  open: boolean;
  onClose: () => void;
  /** After at least one run applied. */
  onDone: () => void;
  workspaces?: string[];
  activeWorkspace?: string;
  preset?: MigratePreset;
};

type Run = { value: string; status: "waiting" | "running" | "done" | "failed" | "cancelled"; moved?: number; facts?: number; last?: string };

/** Copy or move projects, topics or sources to another workspace: pick, see
 * what it takes, run, and see what happened (ADR 0150 for the preview). */
export function MigrateDialog(props: Props) {
  return props.open ? <MigrateDialogContent {...props} /> : null;
}

function presetValues(preset?: MigratePreset) {
  return preset ? (preset.values ?? (preset.value ? [preset.value] : [])) : [];
}

function MigrateDialogContent({ onClose, onDone, workspaces: wsProp, activeWorkspace: activeProp, preset }: Props) {
  const id = useId();
  const [workspaces, setWorkspaces] = useState<string[]>(wsProp ?? []);
  const [from, setFrom] = useState(activeProp ?? "");
  const [mode, setMode] = useState<MigrateMode>(preset?.mode ?? "project");
  const [selected, setSelected] = useState<string[]>(presetValues(preset));
  const [target, setTarget] = useState("");
  const [newName, setNewName] = useState("");
  const [transfer, setTransfer] = useState<Transfer>("copy");
  const [preview, setPreview] = useState<{ key: string; data?: MigratePreview; error?: string } | null>(null);
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [log, setLog] = useState("");
  const [applied, setApplied] = useState(false);
  const running = useRef<{ cancel: () => void } | null>(null);
  const stopped = useRef(false);

  useEffect(() => {
    if (wsProp?.length && activeProp) return;
    let live = true;
    getWorkspaces().then((state) => {
      if (!live) return;
      setWorkspaces(state.workspaces);
      setFrom(state.active);
    }).catch(() => {});
    return () => { live = false; };
  }, [wsProp, activeProp]);

  const others = workspaces.filter((w) => w !== from);
  const creating = target === NEW;
  const to = creating ? newName.trim() : target;
  const nameProblem = creating && newName.trim() && !WORKSPACE_NAME.test(newName.trim())
    ? "Lowercase letters, digits and hyphens, starting with a letter or digit."
    : creating && workspaces.includes(newName.trim()) ? "That workspace exists: pick it from the list." : "";
  const ready = !!from && !!to && to !== from && !nameProblem && selected.length > 0;
  const key = ready ? JSON.stringify([from, to, mode, selected]) : "";

  // The preview follows the choices, a moment after the last change.
  useEffect(() => {
    if (!key || runs) return;
    const ctrl = new AbortController();
    const timer = setTimeout(() => {
      setPreview({ key });
      previewMigrate({ from, to, mode, values: selected }, ctrl.signal)
        .then((data) => setPreview({ key, data }))
        .catch((e) => { if (!ctrl.signal.aborted) setPreview({ key, error: e instanceof Error ? e.message : String(e) }); });
    }, 250);
    return () => { clearTimeout(timer); ctrl.abort(); };
  }, [key, runs, from, to, mode, selected]);

  const shown = preview && preview.key === key ? preview : null;
  const busy = !!runs && runs.some((r) => r.status === "waiting" || r.status === "running");
  const finished = !!runs && !busy;
  const verb = transfer === "copy" ? "Copy" : "Move";

  function start() {
    if (!shown?.data) return;
    const queue = [...selected];
    stopped.current = false;
    setLog("");
    setRuns(queue.map((value) => ({ value, status: "waiting" })));
    const update = (value: string, patch: Partial<Run>) =>
      setRuns((prev) => prev && prev.map((r) => (r.value === value ? { ...r, ...patch } : r)));
    const next = (index: number) => {
      if (index >= queue.length) return;
      const value = queue[index];
      if (stopped.current) { update(value, { status: "cancelled" }); next(index + 1); return; }
      update(value, { status: "running" });
      running.current = migrateContent(
        { from, to, copy: transfer === "copy", project: mode === "project" ? value : undefined,
          topic: mode === "topic" ? value : undefined, source: mode === "source" ? value : undefined },
        (ev) => {
          if (ev.type === "output" && typeof ev.text === "string") {
            const text = ev.text;
            setLog((l) => (l + text).slice(-20_000));
            const line = text.trim().split("\n").filter(Boolean).at(-1);
            const doneLine = /(\d+) (?:copied|moved) of \d+ scanned.*?\+ (\d+) new KG facts/.exec(text);
            update(value, { last: line, ...(doneLine ? { moved: Number(doneLine[1]), facts: Number(doneLine[2]) } : {}) });
          } else if (ev.type === "done") {
            const status = ev.status === "failed" ? "failed" : ev.status === "cancelled" ? "cancelled" : "done";
            update(value, { status });
            if (status === "done") setApplied(true);
            running.current = null;
            next(index + 1);
          } else if (ev.type === "error") {
            setLog((l) => `${l}ERROR: ${String(ev.error)}\n`);
            update(value, { status: "failed", last: String(ev.error) });
            running.current = null;
            next(index + 1);
          }
        },
      );
    };
    next(0);
  }

  // The toast once every run has ended.
  const reported = useRef(false);
  useEffect(() => {
    if (!finished || reported.current || !runs) return;
    reported.current = true;
    const failed = runs.filter((r) => r.status === "failed").length;
    if (failed) toast.error(`${verb} failed for ${plural(failed, NOUN[mode][0], NOUN[mode][1])}`, { description: "The dialog shows what happened." });
    else if (runs.every((r) => r.status === "done")) toast.success(`${transfer === "copy" ? "Copied" : "Moved"} to ${to}`);
  }, [finished, runs, verb, mode, transfer, to]);

  function stop() {
    stopped.current = true;
    running.current?.cancel();
  }

  /** Back to the review, the other way round: the move-back undo of ADR 0036's kind, previewed again. */
  function moveBack() {
    const back = from;
    setFrom(to);
    if (!workspaces.includes(to)) setWorkspaces((w) => [...w, to]);
    setTarget(back);
    setNewName("");
    setTransfer("move");
    setRuns(null);
    setLog("");
    reported.current = false;
  }

  function close() {
    if (busy) return;
    if (applied) onDone();
    onClose();
  }

  const title = preset
    ? `Copy or move ${selected.length === 1 ? `this ${NOUN[mode][0]}` : `${plural(selected.length, NOUN[mode][0], NOUN[mode][1])}`} to another workspace`
    : "Copy or move to another workspace";

  return (
    <Dialog open onOpenChange={(next) => { if (!next) close(); }}>
      <DialogContent showCloseButton={!busy} aria-busy={busy} className="max-h-[92dvh] gap-5 overflow-y-auto sm:max-w-2xl [&>*]:min-w-0">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            Curate workspaces: memories keep their vectors and ids, so nothing is re-embedded and a repeated copy
            updates what it copied before instead of duplicating it.
          </DialogDescription>
        </DialogHeader>

        {!runs ? (
          <>
            <Step number={1} title="What">
              {preset ? (
                <SelectedChips mode={mode} values={selected} onRemove={selected.length > 1 ? (v) => setSelected((s) => s.filter((x) => x !== v)) : undefined} />
              ) : (
                <Picker mode={mode} selected={selected}
                  onMode={(next) => { setMode(next); setSelected([]); }}
                  onSelected={setSelected} />
              )}
            </Step>

            <Step number={2} title="Where to">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                <div className="min-w-0 flex-1 space-y-1">
                  <p className="text-xs text-muted-foreground">From</p>
                  <p className="flex h-9 items-center rounded-lg border border-border bg-muted/40 px-3 font-mono text-sm" title="The workspace the dashboard shows">
                    <span className="truncate">{from || "…"}</span>
                  </p>
                </div>
                <ArrowRight aria-hidden className="mx-auto size-4 shrink-0 rotate-90 text-muted-foreground sm:mb-2.5 sm:rotate-0" />
                <div className="min-w-0 flex-1 space-y-1">
                  <Label htmlFor={`${id}-to`} className="text-xs font-normal text-muted-foreground">To</Label>
                  <Select items={[...others.map((w) => ({ value: w, label: w })), { value: NEW, label: "New workspace…" }]}
                    value={target || null} onValueChange={(value) => setTarget((value as string | null) ?? "")}>
                    <SelectTrigger id={`${id}-to`} className="w-full font-mono"><SelectValue placeholder="Choose a workspace" /></SelectTrigger>
                    <SelectContent>
                      {others.map((w) => <SelectItem key={w} value={w} className="font-mono">{w}</SelectItem>)}
                      {others.length > 0 && <SelectSeparator />}
                      <SelectItem value={NEW}>New workspace…</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              {creating && (
                <div className="space-y-1">
                  <Input aria-label="New workspace name" placeholder="new-workspace-name" value={newName} maxLength={40} autoFocus
                    aria-invalid={!!nameProblem} onChange={(e) => setNewName(e.target.value.toLowerCase())} className="font-mono" />
                  <p className={cn("text-xs", nameProblem ? "text-destructive" : "text-muted-foreground")}>
                    {nameProblem || "Created when the copy or move runs; a preview creates nothing."}
                  </p>
                </div>
              )}
            </Step>

            <Step number={3} title="How">
              <RadioGroup aria-label="Copy or move" value={transfer} onValueChange={(value) => setTransfer(value as Transfer)}
                className="grid gap-2 sm:grid-cols-2">
                {TRANSFERS.map((option) => (
                  <Label key={option.value}
                    className={cn("cursor-pointer items-start rounded-lg border p-3 text-sm font-normal leading-normal has-[:focus-visible]:ring-3 has-[:focus-visible]:ring-ring/50",
                      transfer === option.value ? "border-primary bg-primary/5" : "border-border hover:bg-muted/40")}>
                    <RadioGroupItem value={option.value} className="mt-0.5" />
                    <span>
                      <span className="block font-medium">{option.label}</span>
                      <span className="text-xs text-muted-foreground">{option.detail(from)}</span>
                    </span>
                  </Label>
                ))}
              </RadioGroup>
            </Step>

            <PreviewPanel ready={ready} preview={shown} mode={mode} transfer={transfer} from={from} to={to} />
          </>
        ) : (
          <Progress runs={runs} mode={mode} transfer={transfer} from={from} to={to} log={log} />
        )}

        <DialogFooter className="gap-2 sm:justify-between">
          {!runs ? (
            <>
              <Button variant="ghost" onClick={close}>Cancel</Button>
              <Button onClick={start} disabled={!shown?.data || shown.data.total === 0}
                variant={transfer === "move" ? "destructive" : "default"}>
                {shown?.data ? `${verb} ${plural(shown.data.total, "memory", "memories")} to ${to}` : verb}
              </Button>
            </>
          ) : busy ? (
            <>
              <span className="text-xs text-muted-foreground">Runs in the queue; closing waits until it ends.</span>
              <Button variant="outline" onClick={stop}>Stop</Button>
            </>
          ) : (
            <>
              {transfer === "move" && runs.some((r) => r.status === "done")
                ? <Button variant="outline" onClick={moveBack}>Move back…</Button> : <span />}
              <Button onClick={close}>Done</Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Step({ number, title, children }: { number: number; title: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="space-y-2">
      <h3 className="flex items-center gap-2 text-sm font-medium">
        <span aria-hidden className="grid size-5 place-items-center rounded-full bg-muted text-[11px] tabular-nums text-muted-foreground">{number}</span>
        {title}
      </h3>
      {children}
    </section>
  );
}

function SelectedChips({ mode, values, onRemove }: { mode: MigrateMode; values: string[]; onRemove?: (value: string) => void }) {
  const Icon = ICON[mode];
  return (
    <ul aria-label={`Selected ${NOUN[mode][1]}`} className="flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
      {values.map((value) => (
        <li key={value} className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-muted/40 py-0.5 pr-1 pl-2.5 text-xs">
          <Icon aria-hidden className="size-3 shrink-0 text-muted-foreground" />
          <span className={cn("truncate", mode === "source" && "font-mono")} title={value}>{value}</span>
          {onRemove ? (
            <Button variant="ghost" size="icon-xs" className="size-5 rounded-full" aria-label={`Remove ${value}`} onClick={() => onRemove(value)}><X /></Button>
          ) : <span className="w-1" />}
        </li>
      ))}
    </ul>
  );
}

/** Choose a kind, then any number of its names, searched. */
function Picker({ mode, selected, onMode, onSelected }: {
  mode: MigrateMode; selected: string[]; onMode: (mode: MigrateMode) => void; onSelected: (values: string[]) => void;
}) {
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<{ mode: MigrateMode; q: string; list: Option[]; more: number } | null>(null);
  const [error, setError] = useState("");
  const sources = useRef<Option[] | null>(null);
  const projects = useRef<Option[] | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    const q = query.trim();
    const timer = setTimeout(async () => {
      try {
        let list: Option[];
        let more = 0;
        if (mode === "topic") {
          const page = await getTopics({ q, limit: SHOWN }, undefined, ctrl.signal);
          list = page.topics.map((t) => ({ value: t.name, label: t.name, count: t.count }));
          more = Math.max(0, (page.matched ?? list.length) - list.length);
        } else {
          if (mode === "project" && !projects.current) {
            projects.current = (await getProjects()).projects.map((p) => ({ value: p.name, label: p.name, count: p.count }));
          }
          if (mode === "source" && !sources.current) {
            sources.current = (await listAllSources()).sources.filter((s) => s.source)
              .map((s) => ({ value: s.source, label: s.title || s.source, count: s.chunks }));
          }
          const all = (mode === "project" ? projects.current : sources.current) ?? [];
          const needle = q.toLowerCase();
          const matched = needle ? all.filter((o) => o.value.toLowerCase().includes(needle) || o.label.toLowerCase().includes(needle)) : all;
          list = matched.slice(0, SHOWN);
          more = matched.length - list.length;
        }
        if (!ctrl.signal.aborted) { setOptions({ mode, q, list, more }); setError(""); }
      } catch (e) {
        if (!ctrl.signal.aborted) setError(e instanceof Error ? e.message : String(e));
      }
    }, mode === "topic" && q ? 200 : 0);
    return () => { clearTimeout(timer); ctrl.abort(); };
  }, [mode, query]);

  const list = options?.mode === mode ? options : null;
  const chosen = new Set(selected);
  const toggle = (value: string, on: boolean) => onSelected(on ? [...selected, value] : selected.filter((v) => v !== value));
  const visible = list?.list ?? [];
  const allOn = visible.length > 0 && visible.every((o) => chosen.has(o.value));

  return (
    <div className="space-y-2">
      <ToggleGroup aria-label="Kind" size="sm" variant="outline" value={[mode]}
        onValueChange={(value) => { if (value[0]) { onMode(value[0] as MigrateMode); setQuery(""); } }}>
        {(["project", "topic", "source"] as MigrateMode[]).map((m) => {
          const Icon = ICON[m];
          return <ToggleGroupItem key={m} value={m} className="capitalize"><Icon aria-hidden /> {NOUN[m][1]}</ToggleGroupItem>;
        })}
      </ToggleGroup>
      <InputGroup>
        <InputGroupAddon><Search aria-hidden /></InputGroupAddon>
        <InputGroupInput aria-label={`Search ${NOUN[mode][1]}`} placeholder={`Search ${NOUN[mode][1]}…`} value={query}
          onChange={(e) => setQuery(e.target.value)} />
      </InputGroup>
      <div className="overflow-hidden rounded-lg border border-border">
        <div className="flex items-center gap-2 border-b border-border bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground">
          <Checkbox aria-label={`Select every listed ${NOUN[mode][0]}`} checked={allOn} disabled={!visible.length}
            onCheckedChange={(on) => onSelected(on ? [...new Set([...selected, ...visible.map((o) => o.value)])]
              : selected.filter((v) => !visible.some((o) => o.value === v)))} />
          <span className="flex-1">{selected.length ? `${plural(selected.length, NOUN[mode][0], NOUN[mode][1])} selected` : `Select one or more ${NOUN[mode][1]}`}</span>
          {selected.length > 0 && <Button variant="ghost" size="xs" onClick={() => onSelected([])}>Clear</Button>}
        </div>
        <ul aria-label={NOUN[mode][1]} className="max-h-52 overflow-y-auto p-1" aria-busy={!list}>
          {error ? <li role="alert" className="p-3 text-xs text-destructive">{error}</li>
            : !list ? Array.from({ length: 5 }).map((_, i) => <li key={i} className="p-1.5"><Skeleton className="h-5 w-full" /></li>)
              : !visible.length ? <li className="p-4 text-center text-xs text-muted-foreground">{query ? `No ${NOUN[mode][0]} matches “${query}”.` : `No ${NOUN[mode][1]} in this workspace.`}</li>
                : visible.map((o) => (
                  <li key={o.value}>
                    <label className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-sm hover:bg-muted has-[:focus-visible]:ring-3 has-[:focus-visible]:ring-ring/50">
                      <Checkbox checked={chosen.has(o.value)} onCheckedChange={(on) => toggle(o.value, on)} />
                      <span className="min-w-0 flex-1">
                        <span className={cn("block truncate", mode === "source" && o.label === o.value && "font-mono text-xs")} title={o.value}>{o.label}</span>
                        {mode === "source" && o.label !== o.value && <span className="block truncate font-mono text-[11px] text-muted-foreground">{o.value}</span>}
                      </span>
                      <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{n(o.count)}</span>
                    </label>
                  </li>
                ))}
          {list && list.more > 0 && <li className="px-2 py-1.5 text-center text-xs text-muted-foreground">{n(list.more)} more: search to narrow the list.</li>}
        </ul>
      </div>
      {selected.some((v) => !visible.some((o) => o.value === v)) && (
        <SelectedChips mode={mode} values={selected.filter((v) => !visible.some((o) => o.value === v))}
          onRemove={(v) => onSelected(selected.filter((x) => x !== v))} />
      )}
    </div>
  );
}

function PreviewPanel({ ready, preview, mode, transfer, from, to }: {
  ready: boolean; preview: { data?: MigratePreview; error?: string } | null; mode: MigrateMode; transfer: Transfer; from: string; to: string;
}) {
  if (!ready) {
    return <p className="rounded-lg border border-dashed border-border px-3 py-3 text-sm text-muted-foreground">Choose what and where to see what it takes.</p>;
  }
  if (preview?.error) {
    return <Alert variant="destructive"><CircleAlert /><AlertDescription>The preview failed: {preview.error}</AlertDescription></Alert>;
  }
  const data = preview?.data;
  if (!data) {
    return (
      <div role="status" aria-label="Counting" className="space-y-2 rounded-lg border border-border p-3">
        <Skeleton className="h-5 w-2/3" /><Skeleton className="h-4 w-1/2" />
      </div>
    );
  }
  const fresh = data.total - data.existing;
  return (
    <section aria-label="Preview" role="status" className="space-y-2 rounded-lg border border-border bg-muted/30 p-3 text-sm">
      <p className="font-medium">
        {data.total === 0 ? `Nothing to ${transfer}: no memory matches.` : (
          <>{transfer === "copy" ? "Copies" : "Moves"} {plural(data.total, "memory", "memories")} from {plural(data.sources, "source")} to{" "}
            <span className="font-mono">{to}</span>{data.target_exists ? "" : ", a new workspace"}.</>
        )}
      </p>
      {data.total > 0 && (
        <ul className="space-y-1 text-xs text-muted-foreground">
          {data.existing > 0 && (
            <li>{n(data.existing)} of them are already in {to} and are updated in place; {n(fresh)} are new there.</li>
          )}
          {transfer === "move"
            ? <li className="text-foreground">They are removed from <span className="font-mono">{from}</span>, with their full-source snapshots.</li>
            : <li>{from} keeps all of them.</li>}
          {mode !== "topic" && <li>Knowledge-graph facts about these sources are copied too (never removed).</li>}
          {mode === "topic" && <li>A topic takes only its tagged passages; a source&apos;s full text goes along only when every passage of it does.</li>}
          {data.projects.length > 0 && (
            <li>From {data.projects.slice(0, 4).map(([name, count]) => `${name || "no project"} (${n(count)})`).join(", ")}
              {data.projects.length > 4 ? ` and ${data.projects.length - 4} more projects` : ""}.</li>
          )}
        </ul>
      )}
      {data.items.length > 1 && (
        <ul aria-label="Per selection" className="max-h-28 space-y-0.5 overflow-y-auto border-t border-border pt-2 text-xs">
          {data.items.map((item) => (
            <li key={item.value} className="flex gap-3">
              <span className="min-w-0 flex-1 truncate" title={item.value}>{item.value}</span>
              <span className="shrink-0 tabular-nums text-muted-foreground">{plural(item.memories, "memory", "memories")}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Progress({ runs, mode, transfer, from, to, log }: {
  runs: Run[]; mode: MigrateMode; transfer: Transfer; from: string; to: string; log: string;
}) {
  const ended = runs.filter((r) => r.status !== "waiting" && r.status !== "running").length;
  const moved = runs.reduce((sum, r) => sum + (r.moved ?? 0), 0);
  const allDone = runs.every((r) => r.status === "done");
  const verbed = transfer === "copy" ? "copied" : "moved";
  const summary = useMemo(() => {
    if (ended < runs.length) return `${transfer === "copy" ? "Copying" : "Moving"} ${ended + 1} of ${runs.length} to ${to}…`;
    if (allDone) return `Done: ${n(moved)} ${moved === 1 ? "memory" : "memories"} ${verbed} from ${from} to ${to}.`;
    return `Finished with problems: ${runs.filter((r) => r.status === "done").length} of ${runs.length} ${verbed}.`;
  }, [ended, runs, transfer, to, allDone, moved, verbed, from]);
  return (
    <div className="space-y-3">
      <p role="status" aria-live="polite" className="text-sm font-medium">{summary}</p>
      <ul aria-label={`${NOUN[mode][1]} to ${transfer}`} className="max-h-60 divide-y divide-border overflow-y-auto rounded-lg border border-border">
        {runs.map((run) => (
          <li key={run.value} className="flex items-start gap-2.5 px-3 py-2 text-sm">
            <span className="mt-0.5 shrink-0">
              {run.status === "running" ? <Spinner className="size-4" />
                : run.status === "done" ? <Check aria-label="Done" className="size-4 text-success" />
                  : run.status === "waiting" ? <CircleDashed aria-label="Waiting" className="size-4 text-muted-foreground" />
                    : <CircleAlert aria-label={run.status === "failed" ? "Failed" : "Stopped"} className="size-4 text-destructive" />}
            </span>
            <span className="min-w-0 flex-1">
              <span className={cn("block truncate", mode === "source" && "font-mono text-xs")} title={run.value}>{run.value}</span>
              <span className="block truncate text-xs text-muted-foreground">
                {run.status === "done" && run.moved !== undefined
                  ? `${plural(run.moved, "memory", "memories")} ${verbed}${run.facts ? `, ${plural(run.facts, "fact")}` : ""}`
                  : run.status === "waiting" ? "Waiting" : run.status === "cancelled" ? "Stopped" : run.last ?? "Starting…"}
              </span>
            </span>
          </li>
        ))}
      </ul>
      {log && (
        <Collapsible>
          <CollapsibleTrigger render={<Button variant="ghost" size="xs" className="font-normal text-muted-foreground" />}>Show the log</CollapsibleTrigger>
          <CollapsibleContent>
            <pre className="mt-1 max-h-40 overflow-auto rounded-lg bg-muted p-2 font-mono text-xs whitespace-pre-wrap">{log}</pre>
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  );
}
