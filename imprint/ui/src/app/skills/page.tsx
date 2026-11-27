"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  FolderDown, GitFork, History, Package, Plus, Power, PowerOff, Puzzle, RefreshCw, RotateCcw, Save, Trash2, TriangleAlert,
  Undo2, UserRound,
} from "lucide-react";
import { ListSkeleton } from "@/components/loaders";
import { PageHeader, PaneHeading, PaneItem, SplitPage } from "@/components/split-page";
import { Timestamp, useNow } from "@/components/timestamp";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  addSkill, adoptSkill, applySkills, changeSkill, forkSkill, getSkill, getSkills, resetSkill,
  type FoundSkill, type SkillItem, type SkillKind, type SkillReport, type Skills, type SkillState, type SkillVersion,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const DESCRIPTION = "Skills and personas you keep once. Imprint writes them into the folders your coding clients read, takes back edits you make there, and keeps them the same on every paired device. Built-in skills come with each Imprint release.";
const NAME = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const NOUN: Record<SkillKind, string> = { skill: "skill", persona: "persona" };
const KIND_HELP: Record<SkillKind, string> = {
  skill: "A skill is instructions an agent loads when its description matches the task, for example how to cut a release.",
  persona: "A persona is a role Claude Code can hand work to as a subagent, for example a careful code reviewer.",
};

type Selection = { item: string } | { found: string } | null;
type Detail = SkillItem & { files: Record<string, string>; history: SkillVersion[]; difference: string };

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
function KindIcon({ kind, className }: { kind: SkillKind; className?: string }) {
  const classes = cn("size-4 shrink-0", className);
  return kind === "skill" ? <Puzzle className={classes} aria-hidden /> : <UserRound className={classes} aria-hidden />;
}
const stateLabel: Record<SkillState, string> = { active: "Active", disabled: "Disabled", retired: "Removed" };

/** Tell the user when a change was kept but did not reach the clients. */
function reportOutcome(report: SkillReport | undefined) {
  if (report?.error) toast.error(report.error);
  else if (report?.problems?.length) toast.warning(`${report.problems.length} folder${report.problems.length === 1 ? "" : "s"} left as they were: see Skills`);
}

export default function SkillsPage() {
  const now = useNow();
  const [data, setData] = useState<Skills | null>(null);
  const [found, setFound] = useState<FoundSkill[] | null>(null);
  const [report, setReport] = useState<SkillReport | null>(null);
  const [loadError, setLoadError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [selected, setSelected] = useState<Selection>(null);
  const [creating, setCreating] = useState<SkillKind | null>(null);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const applied = await applySkills();
      setData((prev) => (prev ? { ...prev, items: applied.items } : prev));
      setFound(applied.found);
      setReport(applied.report);
    } catch (e) {
      toast.error(`Could not update the clients' folders: ${message(e)}`);
    } finally { setRefreshing(false); }
  }, []);

  useEffect(() => {
    let live = true;
    getSkills().then((skills) => {
      if (!live) return;
      setData(skills);
      setReport(skills.report);
      void refresh();
    }).catch((e) => { if (live) setLoadError(message(e)); });
    return () => { live = false; };
  }, [refresh]);

  const items = useMemo(() => data?.items ?? [], [data]);
  const groups = useMemo(() => {
    const mine = items.filter((i) => i.origin !== "builtin");
    return {
      skill: mine.filter((i) => i.kind === "skill" && i.state !== "retired"),
      persona: mine.filter((i) => i.kind === "persona" && i.state !== "retired"),
      builtin: items.filter((i) => i.origin === "builtin"),
      retired: mine.filter((i) => i.state === "retired"),
    };
  }, [items]);

  const changed = useCallback((item: SkillItem & { report?: SkillReport }) => {
    setData((prev) => (prev ? { ...prev, items: [...prev.items.filter((i) => i.id !== item.id), item] } : prev));
    if (item.report && !item.report.error) setReport(item.report);
    reportOutcome(item.report);
  }, []);

  /** Forking or resetting changes two items (the built-in and its fork): list them all again. */
  const reload = useCallback(async () => {
    try {
      const skills = await getSkills();
      setData((prev) => (prev ? { ...prev, items: skills.items } : skills));
    } catch (e) {
      toast.error(`Could not list skills: ${message(e)}`);
    }
  }, []);

  const header = (
    <PageHeader title="Skills" count={data ? groups.skill.length + groups.persona.length + groups.builtin.filter((i) => !i.fork).length : undefined} updating={refreshing}
      description={DESCRIPTION}
      actions={<>
        <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={refreshing}>
          <RefreshCw className={cn(refreshing && "animate-spin")} /> Refresh
        </Button>
        <Button variant="outline" size="sm" onClick={() => setCreating("persona")}><UserRound /> New persona</Button>
        <Button size="sm" data-tour="skills-new" onClick={() => setCreating("skill")}><Plus /> New skill</Button>
      </>} />
  );

  if (loadError) {
    return (
      <div>
        {header}
        <Alert variant="destructive" className="mx-4 w-auto sm:mx-8">
          <TriangleAlert />
          <AlertTitle>Skills could not load</AlertTitle>
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      </div>
    );
  }
  if (!data) {
    return (
      <SplitPage header={header} list={<ListSkeleton count={8} />} listWidth="16rem">
        <Skeleton className="h-64 w-full rounded-xl" />
      </SplitPage>
    );
  }

  const isItem = (id: string) => selected !== null && "item" in selected && selected.item === id;
  const isFound = (path: string) => selected !== null && "found" in selected && selected.found === path;
  const row = (item: SkillItem) => {
    return (
      <PaneItem key={item.id} selected={isItem(item.id)} onClick={() => setSelected({ item: item.id })}
        className={cn(item.state !== "active" && "text-muted-foreground")}>
        <KindIcon kind={item.kind} className="text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate font-mono text-[13px]">{item.name}</span>
        {item.fork_of && <Badge variant="outline" className="h-4 px-1 text-[10px]">fork</Badge>}
        {item.origin === "builtin" && item.fork && <Badge variant="outline" className="h-4 px-1 text-[10px]">forked</Badge>}
        {item.state === "disabled" && <Badge variant="outline" className="h-4 px-1 text-[10px]">off</Badge>}
        {item.builtin_changed && <TriangleAlert className="size-3.5 shrink-0 text-amber-600" aria-label="The built-in changed since you forked it" />}
        {item.shadowed && item.origin !== "builtin" && <TriangleAlert className="size-3.5 shrink-0 text-amber-600" aria-label="Another item holds this name" />}
      </PaneItem>
    );
  };

  const list = (
    <nav aria-label="Skills and personas" data-tour="skills-list">
      <PaneHeading>Skills</PaneHeading>
      <div className="space-y-0.5">
        {groups.skill.length ? groups.skill.map(row) : <p className="px-2.5 py-1.5 text-xs text-muted-foreground">None yet</p>}
      </div>
      <PaneHeading>Personas</PaneHeading>
      <div className="space-y-0.5">
        {groups.persona.length ? groups.persona.map(row) : <p className="px-2.5 py-1.5 text-xs text-muted-foreground">None yet</p>}
      </div>
      {groups.builtin.length > 0 && (
        <>
          <PaneHeading>Built in</PaneHeading>
          <div className="space-y-0.5">{groups.builtin.map(row)}</div>
        </>
      )}
      {found && found.length > 0 && (
        <>
          <PaneHeading>In your clients</PaneHeading>
          <div className="space-y-0.5">
            {found.map((f) => {
              return (
                <PaneItem key={f.path} selected={isFound(f.path)} onClick={() => setSelected({ found: f.path })}
                  className="text-muted-foreground">
                  <KindIcon kind={f.kind} />
                  <span className="min-w-0 flex-1 truncate font-mono text-[13px]">{f.name}</span>
                  {f.problem
                    ? <TriangleAlert className="size-3.5 shrink-0 text-amber-600" aria-label="Cannot be adopted as it is" />
                    : <FolderDown className="size-3.5 shrink-0" aria-label="Can be adopted" />}
                </PaneItem>
              );
            })}
          </div>
        </>
      )}
      {groups.retired.length > 0 && (
        <>
          <PaneHeading>Removed</PaneHeading>
          <div className="space-y-0.5">{groups.retired.map(row)}</div>
        </>
      )}
    </nav>
  );

  const selectedItem = selected && "item" in selected ? items.find((i) => i.id === selected.item) : undefined;
  const selectedFound = selected && "found" in selected ? found?.find((f) => f.path === selected.found) : undefined;

  return (
    <SplitPage header={header} list={list} listWidth="16rem">
      {selectedItem ? (
        <ItemDetails key={`${selectedItem.id}:${selectedItem.hash}:${selectedItem.state}:${selectedItem.fork ?? ""}`}
          item={selectedItem} now={now}
          problems={(report?.problems ?? []).filter((p) => pathNames(p.path, selectedItem))}
          onChanged={changed}
          onReplaced={(item) => {
            changed(item);
            setSelected({ item: item.id });
            void reload();
          }}
          onSelect={(id) => setSelected({ item: id })} />
      ) : selectedFound ? (
        <FoundDetails found={selectedFound} onAdopted={(item) => {
          setFound((prev) => prev?.filter((f) => f.path !== selectedFound.path) ?? prev);
          setData((prev) => (prev ? { ...prev, items: [...prev.items, item] } : prev));
          setSelected({ item: item.id });
          void refresh();
        }} />
      ) : (
        <Overview report={report} empty={items.every((i) => i.origin === "builtin")} found={found?.length ?? 0} now={now}
          onCreate={setCreating} />
      )}
      <NewDialog kind={creating} onOpenChange={(open) => { if (!open) setCreating(null); }}
        maxName={data.max_name} maxDescription={data.max_description}
        onAdded={(item) => { changed(item); setSelected({ item: item.id }); setCreating(null); }} />
    </SplitPage>
  );
}

/** Whether a reported path is this item's folder or file in a client. */
function pathNames(path: string, item: SkillItem) {
  const last = path.split(/[\\/]/).filter(Boolean).pop() ?? "";
  return last === item.name || last === `${item.name}.md`;
}

function Overview({ report, empty, found, now, onCreate }: {
  report: SkillReport | null; empty: boolean; found: number; now: Date; onCreate: (kind: SkillKind) => void;
}) {
  return (
    <>
      {empty && (
        <Empty className="rounded-xl border border-dashed border-border">
          <EmptyHeader>
            <EmptyMedia variant="icon"><Puzzle /></EmptyMedia>
            <EmptyTitle>No skills or personas yet</EmptyTitle>
            <EmptyDescription>
              Write one here, or adopt one your clients already have{found ? ` (${found} found, listed on the left)` : ""}.
              Imprint then keeps it in every connected client and on every paired device.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent className="flex-row justify-center gap-2">
            <Button size="sm" onClick={() => onCreate("skill")}><Plus /> New skill</Button>
            <Button size="sm" variant="outline" onClick={() => onCreate("persona")}><UserRound /> New persona</Button>
          </EmptyContent>
        </Empty>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Where they go</CardTitle>
          <CardDescription>
            The fewest folders that reach every connected client. A client that already reads one of them gets nothing
            more, so each skill shows up once wherever it can.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {!report ? (
            <p className="text-muted-foreground">Not written yet.</p>
          ) : report.error ? (
            <p className="text-destructive">{report.error}</p>
          ) : report.folders.length === 0 ? (
            <p className="text-muted-foreground">
              No connected client reads skills. Connect Claude Code, Codex, Cursor, Copilot or OpenCode in Settings.
            </p>
          ) : (
            <ul className="space-y-2">
              {report.folders.map((f) => (
                <li key={`${f.kind}:${f.folder}`} className="flex flex-wrap items-baseline gap-x-2">
                  <span className="w-20 shrink-0 text-muted-foreground">{f.kind === "skill" ? "Skills" : "Personas"}</span>
                  <code className="min-w-0 break-all font-mono text-xs">{f.folder}</code>
                  <span className="text-xs text-muted-foreground">{f.clients.join(", ")}</span>
                </li>
              ))}
            </ul>
          )}
          {report?.at ? <p className="text-xs text-muted-foreground">Last written <Timestamp seconds={report.at} now={now} /></p> : null}
        </CardContent>
      </Card>
      {report && report.problems.length > 0 && (
        <Alert>
          <TriangleAlert />
          <AlertTitle>Left as they were</AlertTitle>
          <AlertDescription>
            <ul className="mt-1 space-y-1.5">
              {report.problems.map((p) => (
                <li key={p.path}><code className="break-all font-mono text-xs">{p.path}</code>: {p.problem}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}
    </>
  );
}

/** A unified diff, its added and removed lines coloured. */
function DiffView({ text, label }: { text: string; label: string }) {
  const tone = (line: string) => (
    line.startsWith("+++") || line.startsWith("---") ? "font-semibold text-foreground"
      : line.startsWith("@@") ? "text-muted-foreground"
        : line.startsWith("+") ? "bg-emerald-500/10 text-emerald-800 dark:text-emerald-300"
          : line.startsWith("-") ? "bg-red-500/10 text-red-800 dark:text-red-300" : "");
  return (
    <pre aria-label={label} className="max-h-96 overflow-auto rounded-md border border-border bg-muted/40 py-2 font-mono text-xs leading-relaxed">
      {text.replace(/\n$/, "").split("\n").map((line, index) => (
        <span key={index} className={cn("block whitespace-pre px-3", tone(line))}>{line || " "}</span>
      ))}
    </pre>
  );
}

function ItemDetails({ item, now, problems, onChanged, onReplaced, onSelect }: {
  item: SkillItem; now: Date; problems: { path: string; problem: string }[];
  onChanged: (item: SkillItem & { report?: SkillReport }) => void;
  /** After a fork or a reset: the item now holding the name, to select. */
  onReplaced: (item: SkillItem & { report?: SkillReport }) => void;
  onSelect: (id: string) => void;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [file, setFile] = useState("");
  const [busy, setBusy] = useState(false);
  const builtin = item.origin === "builtin";

  useEffect(() => {
    let live = true;
    getSkill(item.id).then((loaded) => {
      if (!live) return;
      setDetail(loaded);
      setDraft(loaded.files);
      setFile(Object.keys(loaded.files)[0] ?? "");
    }).catch((e) => { if (live) setError(message(e)); });
    return () => { live = false; };
  }, [item.id]);

  const dirty = detail !== null && Object.keys(draft).some((path) => draft[path] !== detail.files[path]);

  async function run(change: Parameters<typeof changeSkill>[1], done: string) {
    setBusy(true);
    try {
      const result = await changeSkill(item.id, change);
      onChanged(result);
      toast.success(done);
    } catch (e) {
      toast.error(message(e));
    } finally { setBusy(false); }
  }

  /** Fork or reset: the other item of the name holds it afterwards. */
  async function replace(action: typeof forkSkill, done: string) {
    setBusy(true);
    try {
      const result = await action(item.id);
      onReplaced(result);
      toast.success(done);
    } catch (e) {
      toast.error(message(e));
    } finally { setBusy(false); }
  }

  const resetButton = (
    <Button size="sm" variant="outline" disabled={busy}
      onClick={() => void replace(resetSkill, `Reset ${item.name}: your clients get the built-in again`)}>
      <Undo2 /> Reset to built-in
    </Button>
  );
  const origin = builtin ? "Ships with this Imprint release, read-only"
    : item.fork_of ? "Fork of the built-in skill"
      : item.origin === "adopted" ? "Adopted from a client's folder" : "Written in Imprint";

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2">
            <KindIcon kind={item.kind} className="text-muted-foreground" />
            <span className="font-mono">{item.name}</span>
            <Badge variant={item.state === "active" ? "secondary" : "outline"}>{stateLabel[item.state]}</Badge>
            <Badge variant="outline">{item.kind === "skill" ? "Skill" : "Persona"}</Badge>
            {builtin && <Badge variant="outline"><Package aria-hidden /> Built-in</Badge>}
            {item.fork_of && <Badge variant="outline"><GitFork aria-hidden /> Fork</Badge>}
          </CardTitle>
          <CardDescription>{item.description}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-muted-foreground">
            {origin}
            {!builtin && <> · changed <Timestamp seconds={item.updated_at} now={now} /></>}
            {" "}· {item.paths.length} file{item.paths.length === 1 ? "" : "s"}
          </p>
          {item.shadowed && !builtin && (
            <p className="text-sm text-amber-700 dark:text-amber-500">
              Another {NOUN[item.kind]} with this name was made on another device before they synced, and the older one is
              the one your clients get. Rename one of them in its front matter, or remove one.
            </p>
          )}
          {builtin && item.fork ? (
            <p className="text-sm text-muted-foreground">
              Your fork replaces it in every client and on every paired device.
            </p>
          ) : builtin ? (
            <p className="text-sm text-muted-foreground">
              To change it, fork it: your own copy then replaces it in every client and on every paired device.
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {builtin && item.fork ? (
              <>
                <Button size="sm" variant="outline" onClick={() => onSelect(item.fork!)}><GitFork /> Open your fork</Button>
                {resetButton}
              </>
            ) : item.state === "retired" ? (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => void run({ state: "active" }, `Restored ${item.name}`)}>
                <RotateCcw /> Restore
              </Button>
            ) : (
              <>
                {builtin && (
                  <Button size="sm" disabled={busy}
                    onClick={() => void replace(forkSkill, `Forked ${item.name}: your copy replaces the built-in everywhere`)}>
                    <GitFork /> Fork
                  </Button>
                )}
                {item.state === "active" ? (
                  <Button size="sm" variant="outline" disabled={busy} onClick={() => void run({ state: "disabled" }, `Disabled ${item.name}: the clients no longer have it`)}>
                    <PowerOff /> Disable
                  </Button>
                ) : (
                  <Button size="sm" variant="outline" disabled={busy} onClick={() => void run({ state: "active" }, `Enabled ${item.name}`)}>
                    <Power /> Enable
                  </Button>
                )}
                {item.fork_of ? resetButton : !builtin && (
                  <Button size="sm" variant="outline" disabled={busy} className="text-destructive"
                    onClick={() => void run({ state: "retired" }, `Removed ${item.name} here and on your paired devices`)}>
                    <Trash2 /> Remove
                  </Button>
                )}
              </>
            )}
          </div>
        </CardContent>
      </Card>

      {item.builtin_changed && (
        <Alert>
          <TriangleAlert />
          <AlertTitle>This release changed the built-in {item.name}</AlertTitle>
          <AlertDescription className="space-y-2">
            <p>
              It changed since you forked it. Your fork stays as you wrote it: compare the two below, and reset to take the
              built-in, or keep your fork.
            </p>
            {resetButton}
          </AlertDescription>
        </Alert>
      )}

      {detail?.difference && (
        <Card>
          <CardHeader>
            <CardTitle>Difference from the built-in</CardTitle>
            <CardDescription>
              Lines marked − are your fork&apos;s, lines marked + the built-in&apos;s in this release. Reset to built-in takes
              the built-in&apos;s.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <DiffView text={detail.difference} label={`Difference between your fork of ${item.name} and the built-in`} />
          </CardContent>
        </Card>
      )}

      {problems.map((p) => (
        <Alert key={p.path}>
          <TriangleAlert />
          <AlertTitle className="break-all font-mono text-xs">{p.path}</AlertTitle>
          <AlertDescription>{p.problem}</AlertDescription>
        </Alert>
      ))}

      <Card>
        <CardHeader>
          <CardTitle>Files</CardTitle>
          <CardDescription>
            {builtin
              ? "Read-only: it ships with Imprint and changes only with a release. Fork it to edit your own copy."
              : "Edits here reach every client and paired device. Edits made in a client's folder come back here too."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {error ? <p className="text-sm text-destructive">{error}</p> : !detail ? <Skeleton className="h-64 w-full" /> : (
            <>
              {Object.keys(draft).length > 1 && (
                <div role="tablist" aria-label="Files" className="flex flex-wrap gap-1">
                  {Object.keys(draft).map((path) => (
                    <Button key={path} role="tab" aria-selected={path === file} size="xs" variant={path === file ? "secondary" : "ghost"}
                      className="font-mono" onClick={() => setFile(path)}>
                      {path}{draft[path] !== detail.files[path] ? " •" : ""}
                    </Button>
                  ))}
                </div>
              )}
              <Textarea aria-label={file} value={draft[file] ?? ""} spellCheck={false} rows={18} readOnly={builtin}
                className={cn("font-mono text-xs leading-relaxed", builtin && "bg-muted/40")}
                onChange={(e) => setDraft((prev) => ({ ...prev, [file]: e.target.value }))}
                onKeyDown={(e) => {
                  if (e.key === "s" && (e.metaKey || e.ctrlKey) && dirty) { e.preventDefault(); void run({ files: draft }, `Saved ${item.name}`); }
                }} />
              {!builtin && (
                <div className="flex gap-2">
                  <Button size="sm" disabled={!dirty || busy} onClick={() => void run({ files: draft }, `Saved ${item.name}`)}>
                    <Save /> Save
                  </Button>
                  <Button size="sm" variant="ghost" disabled={!dirty || busy} onClick={() => setDraft(detail.files)}>Revert</Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {detail && detail.history.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><History className="size-4" aria-hidden /> Earlier versions</CardTitle>
            <CardDescription>Kept on this device only, newest first.</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y divide-border text-sm">
              {detail.history.map((v) => (
                <li key={v.hash} className="flex items-center justify-between gap-3 py-2">
                  <span>
                    Replaced <Timestamp seconds={v.replaced_at} now={now} />
                    <span className="text-muted-foreground"> · {v.paths.length} file{v.paths.length === 1 ? "" : "s"}</span>
                  </span>
                  <Button size="sm" variant="ghost" disabled={busy}
                    onClick={() => void run({ restore: v.hash }, `Restored an earlier version of ${item.name}`)}>
                    <RotateCcw /> Restore
                  </Button>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </>
  );
}

function FoundDetails({ found, onAdopted }: { found: FoundSkill; onAdopted: (item: SkillItem) => void }) {
  const [busy, setBusy] = useState(false);
  async function adopt() {
    setBusy(true);
    try {
      const item = await adoptSkill(found.path, found.kind);
      toast.success(`Adopted ${item.name}: every connected client and paired device gets it`);
      onAdopted(item);
    } catch (e) {
      toast.error(message(e));
    } finally { setBusy(false); }
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          <KindIcon kind={found.kind} className="text-muted-foreground" />
          <span className="font-mono">{found.name}</span>
          <Badge variant="outline">In {found.clients.join(", ") || "a client"}</Badge>
        </CardTitle>
        <CardDescription>{found.description || "No description."}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p><code className="break-all font-mono text-xs">{found.path}</code></p>
        <p className="text-muted-foreground">
          Imprint did not write this {NOUN[found.kind]}. Adopt it to keep it in every connected client and on your paired
          devices. It stays where it is, and Imprint manages it from then on.
        </p>
        {found.problem && <p className="text-destructive">{found.problem}</p>}
        <Button size="sm" disabled={busy || !!found.problem} onClick={() => void adopt()}><FolderDown /> Adopt</Button>
      </CardContent>
    </Card>
  );
}

function NewDialog({ kind, onOpenChange, maxName, maxDescription, onAdded }: {
  kind: SkillKind | null; onOpenChange: (open: boolean) => void; maxName: number; maxDescription: number;
  onAdded: (item: SkillItem & { report?: SkillReport }) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const shown = kind ?? "skill";
  const nameOk = NAME.test(name) && name.length <= maxName;
  const ready = nameOk && description.trim() !== "" && instructions.trim() !== "";

  async function add() {
    if (!kind) return;
    setBusy(true);
    setError("");
    try {
      const item = await addSkill({ kind, name, description, instructions });
      toast.success(`Added ${item.name}`);
      setName(""); setDescription(""); setInstructions("");
      onAdded(item);
    } catch (e) {
      setError(message(e));
    } finally { setBusy(false); }
  }

  return (
    <Dialog open={kind !== null} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>New {NOUN[shown]}</DialogTitle>
          <DialogDescription>{KIND_HELP[shown]}</DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); if (ready) void add(); }}>
          <div className="space-y-1.5">
            <Label htmlFor="skill-name">Name</Label>
            <Input id="skill-name" value={name} autoFocus placeholder={shown === "skill" ? "cut-a-release" : "code-reviewer"}
              className="font-mono" maxLength={maxName} onChange={(e) => setName(e.target.value.toLowerCase().replace(/[\s_]+/g, "-"))} />
            <p className={cn("text-xs", name && !nameOk ? "text-destructive" : "text-muted-foreground")}>
              Lower-case letters, digits and hyphens. It names the folder or file the clients read.
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="skill-description">Description</Label>
            <Textarea id="skill-description" value={description} rows={2} maxLength={maxDescription}
              placeholder={shown === "skill" ? "Cut a release: bump the version, tag it and write the notes. Use when asked to release." : "Reviews a diff for correctness bugs before it is committed."}
              onChange={(e) => setDescription(e.target.value)} />
            <p className="text-xs text-muted-foreground">Agents read this to decide when to use it: say what it does and when.</p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="skill-instructions">{shown === "skill" ? "Instructions" : "Its prompt"}</Label>
            <Textarea id="skill-instructions" value={instructions} rows={8} className="font-mono text-xs"
              onChange={(e) => setInstructions(e.target.value)} />
          </div>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!ready || busy}><Plus /> Add {NOUN[shown]}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
