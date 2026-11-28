"use client";

import { useEffect, useId, useMemo, useState } from "react";
import {
  ArrowLeft, Ban, Circle, CircleCheck, CircleDashed, CircleDot, CircleX, Eye, GitBranch, Link2, Pencil, Plus, Send, Trash2, Unlock,
  UserMinus, X,
} from "lucide-react";
import { toast } from "sonner";
import { Markdown } from "@/components/markdown";
import { Spinner } from "@/components/loaders";
import { Timestamp } from "@/components/timestamp";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import {
  TASK_STATUSES, changeTask, deleteTask, getProgress, getTask, getTaskInbox, markTaskCommentsRead, type BoardOptions, type BoardTask, type RelatedTask, type TaskAction,
  type TaskDetails, type TaskEvent, type TaskPriority, type TaskStatus,
} from "@/lib/api";
import { useCached } from "@/lib/use-cached";
import { cn } from "@/lib/utils";
import { StatusDot } from "../status";
import { PRIORITY_LABELS, STATUS_HINTS, STATUS_LABELS, ago, who } from "./format";
import { CopyId, TaskRefs, copyTask, remarkTaskLinks, taskLinkRenderer } from "./links";
import { lines } from "./new-task-dialog";
import { ProgressSummary } from "./progress";

const PRIORITIES: TaskPriority[] = ["P0", "P1", "P2", "P3"];
const NO_FEATURE = "__none__";
const OTHER_PROJECT = "__other__";
/** A Feature's Tasks shown before "Show all". */
const TASKS_SHOWN = 8;
/** Linked Tasks in the order a reader looks for them: what is moving, what is next, what ended. */
const LINK_ORDER: TaskStatus[] = ["in_progress", "review", "ready", "backlog", "done", "cancelled"];
const FIELD_NAMES: Record<string, string> = {
  title: "the title", description: "the description", project: "the project", priority: "the priority",
  acceptance: "the acceptance criteria", position: "its place in the column",
};
const KIND_LABELS: Record<string, string> = {
  created: "created it", edited: "edited", appended: "added to the description", moved: "moved it", tagged: "changed tags",
  assigned: "changed assignees", linked: "changed links", blocked: "marked it blocked", unblocked: "cleared the block",
  claimed: "claimed it", released: "released it", checkpoint: "recorded a checkpoint", comment: "commented", deleted: "deleted it",
  renamed: "gave it a new id, since another device's Task had the same one",
};

/** One Task: everything about it, and every change the user can make. */
export function TaskDrawer({ taskId, options, known, now, simple, backTo, onOpenTask, onBack, onChanged, onClose }: {
  taskId: string; options: BoardOptions; now: Date; simple: boolean;
  /** Every Task the Board shows: what a link shows when the Task's details lack it, and ids to suggest. */
  known: BoardTask[];
  /** The Task this one was opened from, to go back to. */
  backTo?: string;
  onOpenTask: (id: string) => void; onBack: () => void;
  onChanged: () => void; onClose: () => void;
}) {
  const details = useCached<TaskDetails>(`task:${taskId}`, () => getTask(taskId), { refreshInterval: 5000, persist: false });
  const [deleting, setDeleting] = useState(false);

  // Opening a Task reads what was written to the person on it (ADR 0241).
  useEffect(() => {
    let cancelled = false;
    getTaskInbox().then((inbox) => {
      if (cancelled || !Array.isArray(inbox.comments)) return;
      const here = inbox.comments.filter((c) => c.task === taskId && (!c.workspace || c.workspace === inbox.workspace));
      if (here.length) void markTaskCommentsRead(here.map((c) => c.id)).catch(() => {});
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [taskId]);

  async function act(action: TaskAction, body: Record<string, unknown> = {}, done?: string): Promise<boolean> {
    try {
      const { task } = await changeTask(taskId, action, body);
      if (details.data) details.mutate({ ...details.data, task });
      void details.refresh();
      onChanged();
      if (done) toast.success(done);
      return true;
    } catch (e) {
      toast.error("Not changed", { description: e instanceof Error ? e.message : String(e) });
      return false;
    }
  }

  async function remove() {
    try {
      await deleteTask(taskId);
      toast.success(`Deleted ${taskId}`);
      onChanged();
      onClose();
    } catch (e) {
      toast.error("Not deleted", { description: e instanceof Error ? e.message : String(e) });
    }
  }

  if (!details.data) {
    return (
      <>
        <SheetHeader className="border-b border-border pr-12">
          {backTo && <BackButton to={backTo} onBack={onBack} />}
          <SheetTitle className="font-mono">{taskId}</SheetTitle>
        </SheetHeader>
        <div className="p-4" aria-busy={!details.error}>
          {details.error ? (
            <Alert variant="destructive"><AlertDescription>{details.error.message}</AlertDescription></Alert>
          ) : <Spinner />}
        </div>
      </>
    );
  }
  const { task, history, handoff } = details.data;
  const related = details.data.related ?? {};
  const ended = task.status === "done" || task.status === "cancelled";
  const refs = (text: string) => <TaskRefs text={text} onOpen={onOpenTask} />;
  return (
    <>
      <SheetHeader className="gap-1.5 border-b border-border pr-12">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          {backTo && <BackButton to={backTo} onBack={onBack} />}
          <CopyId id={task.id} className="text-foreground/80" />
          <Button variant="ghost" size="icon-xs" aria-label="Copy link" title="Copy a link to this task"
            onClick={() => void copyTask(task.id, "link")}>
            <Link2 aria-hidden />
          </Button>
          {task.former_ids?.length ? <span className="font-mono">was {task.former_ids.join(", ")}</span> : null}
        </div>
        <EditableTitle task={task} onSave={(title) => act("edit", { title })} />
        <SheetDescription className="text-xs">
          {task.project}{task.tasks.length ? <> · a Feature of {task.tasks.length} tasks</> : null}
        </SheetDescription>
      </SheetHeader>
      <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto p-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Status">
            <Select items={TASK_STATUSES.map((s) => ({ value: s, label: STATUS_LABELS[s] }))} value={task.status}
              onValueChange={(next) => { if (next && next !== task.status) void act("move", { status: next }, `Moved to ${STATUS_LABELS[next as TaskStatus]}`); }}>
              <SelectTrigger aria-label="Status" className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>{TASK_STATUSES.map((s) => <SelectItem key={s} value={s}>{STATUS_LABELS[s]}</SelectItem>)}</SelectContent>
            </Select>
          </Field>
          <Field label="Priority">
            <Select items={PRIORITIES.map((p) => ({ value: p, label: PRIORITY_LABELS[p] }))} value={task.priority}
              onValueChange={(next) => { if (next && next !== task.priority) void act("edit", { priority: next }); }}>
              <SelectTrigger aria-label="Priority" className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>{PRIORITIES.map((p) => <SelectItem key={p} value={p}>{PRIORITY_LABELS[p]}</SelectItem>)}</SelectContent>
            </Select>
          </Field>
          <ProjectField task={task} options={options} onSave={(project) => act("edit", { project }, `Project: ${project}`)} />
          <FeatureField task={task} options={options} onLink={(feature) => act("link", { feature })} />
          <p className="-mt-1 text-xs text-muted-foreground sm:col-span-2">{STATUS_LABELS[task.status]}: {STATUS_HINTS[task.status].toLowerCase()}.</p>
        </div>

        {task.tasks.length > 0 && <FeatureProgress task={task} />}
        <HolderPanel task={task} now={now} simple={simple} onRelease={() => act("release", {}, "Claim released")}
          onBack={() => act("move", { status: task.acceptance.length ? "ready" : "backlog" }, task.acceptance.length ? "Back in Ready" : "Back in Backlog")} />
        <BlockedPanel task={task} refs={refs} onBlock={(reason) => act("block", { reason })} onUnblock={() => act("unblock")} />

        <RelatedTasks task={task} related={related} known={known} onOpen={onOpenTask}
          onAddBlocker={(id) => act("link", { add_blockers: [id] }, `Waits on ${id}`)}
          onRemoveBlocker={(id) => act("link", { remove_blockers: [id] }, `No longer waits on ${id}`)} />

        <Section title="Acceptance criteria">
          <ListEditor items={task.acceptance} refs={refs} empty="None yet: add some before moving it to Ready."
            label="Acceptance criteria, one per line" onSave={(items) => act("edit", { acceptance: items })} />
        </Section>
        <Section title="Description">
          <DescriptionEditor text={task.description} onOpenTask={onOpenTask} onSave={(description) => act("edit", { description })} />
        </Section>
        <Section title="Tags">
          <Chips values={task.tags} prefix="#" label="tag" onAdd={(v) => act("tags", { add: [v] })} onRemove={(v) => act("tags", { remove: [v] })} />
        </Section>
        <Section title="Assignees">
          <Chips values={task.assignees} label="assignee" onAdd={(v) => act("assignees", { add: [v] })} onRemove={(v) => act("assignees", { remove: [v] })} />
          <p className="text-xs text-muted-foreground">
            {task.feature ? "Agents take it when you ask for its Feature, never unasked."
              : task.tasks.length ? "Agents work this Feature's tasks when you ask for it, never unasked."
              : task.assignees.length ? "Only these take it unasked." : "None: any agent may take it."} &quot;user&quot; is you; also a client (codex), a device (@laptop) or both (codex@laptop).
          </p>
        </Section>
        {(handoff.last_checkpoint || handoff.branch) && (
          <Section title="Where the work stands">
            {handoff.last_checkpoint && (
              <div className="rounded-lg bg-muted/60 p-3 text-sm">
                <p className="text-xs text-muted-foreground">Last checkpoint, {who(handoff.last_checkpoint.by)}, <Timestamp seconds={handoff.last_checkpoint.at} now={now} /></p>
                <p className="mt-1 whitespace-pre-wrap">{refs(handoff.last_checkpoint.text)}</p>
                {handoff.last_checkpoint.next && <p className="mt-1 text-muted-foreground">Next: {refs(handoff.last_checkpoint.next)}</p>}
              </div>
            )}
            {!simple && (handoff.branch || handoff.files.length > 0 || handoff.conversation) && (
              <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
                {handoff.branch && <><dt className="text-muted-foreground"><GitBranch className="inline size-3.5" aria-hidden /> Branch</dt>
                  <dd className="font-mono text-xs leading-5 break-all">{handoff.branch}{handoff.commit && ` @ ${handoff.commit}`}</dd></>}
                {handoff.files.length > 0 && <><dt className="text-muted-foreground">Files</dt>
                  <dd className="font-mono text-xs leading-5 break-all">{handoff.files.slice(0, 12).join(", ")}{handoff.files.length > 12 && ` and ${handoff.files.length - 12} more`}</dd></>}
                {handoff.conversation && <><dt className="text-muted-foreground">Agent&apos;s conversation</dt><dd className="font-mono text-xs leading-5 break-all" title="Searchable in Imprint once it has been learned">{handoff.conversation}</dd></>}
              </dl>
            )}
          </Section>
        )}

        <Comments task={task} now={now} refs={refs} onSend={(text, to) => act("comment", { text, to })} />

        {!simple && (
          <Collapsible>
            <CollapsibleTrigger render={<Button variant="ghost" size="sm" className="-ml-2 text-muted-foreground" />}>
              History ({history.length})
            </CollapsibleTrigger>
            <CollapsibleContent>
              <ol className="mt-2 space-y-1 text-xs text-muted-foreground">
                {[...history].reverse().map((event) => (
                  <li key={event.id} className="flex gap-2">
                    <span className="w-24 shrink-0 tabular-nums"><Timestamp seconds={event.recorded_at} now={now} /></span>
                    <span className="min-w-0">
                      <span className="text-foreground">{event.actor === "user" ? "You" : event.actor === "system" ? "Imprint" : event.agent || event.client || "An agent"}</span>{" "}
                      {eventText(event)}
                    </span>
                  </li>
                ))}
              </ol>
            </CollapsibleContent>
          </Collapsible>
        )}

        <div className="mt-auto flex flex-wrap gap-2 border-t border-border pt-4">
          {!ended && (
            <Button variant="outline" size="sm" onClick={() => void act("move", { status: "cancelled" }, "Cancelled")}>
              <Ban aria-hidden /> Cancel task
            </Button>
          )}
          <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={() => setDeleting(true)}>
            <Trash2 aria-hidden /> Delete
          </Button>
          <p className="basis-full text-xs text-muted-foreground">
            Created by {who(task.created_by)}, <Timestamp seconds={task.created_at} now={now} />.
          </p>
        </div>
      </div>
      <AlertDialog open={deleting} onOpenChange={setDeleting}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {task.id}?</AlertDialogTitle>
            <AlertDialogDescription>
              “{task.title}”, its comments and its history are removed for good; only a note that it was deleted stays.
              Cancelling keeps it readable instead.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => { setDeleting(false); void remove(); }}>Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

/** A Feature's progress and how long is left. */
function FeatureProgress({ task }: { task: BoardTask }) {
  const report = useCached(`tasks-progress:${task.id}`, () => getProgress({ feature: task.id }), { refreshInterval: 15_000, persist: false });
  const entry = report.data?.features?.[0];
  if (!entry) return null;
  return <div className="rounded-lg border border-border p-3"><ProgressSummary entry={entry} name="Progress" /></div>;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="grid min-w-0 gap-1">
      <span className="text-[11px] font-medium text-muted-foreground">{label}</span>
      {children}
      {hint && <span className="text-[11px] text-muted-foreground">{hint}</span>}
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="grid gap-1.5">
      <h3 className="text-sm font-semibold">{title}</h3>
      {hint && <p className="-mt-1 text-xs text-muted-foreground">{hint}</p>}
      {children}
    </section>
  );
}

function EditableTitle({ task, onSave }: { task: BoardTask; onSave: (title: string) => Promise<boolean> }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(task.title);
  if (!editing) {
    return (
      <SheetTitle className="flex items-start gap-1">
        <span className="min-w-0 break-words">{task.title}</span>
        <Button variant="ghost" size="icon-xs" aria-label="Rename the task" onClick={() => { setValue(task.title); setEditing(true); }}>
          <Pencil aria-hidden />
        </Button>
      </SheetTitle>
    );
  }
  return (
    <form className="flex gap-2" onSubmit={async (e) => { e.preventDefault(); if (await onSave(value)) setEditing(false); }}>
      <SheetTitle className="sr-only">{task.title}</SheetTitle>
      <Input aria-label="Title" value={value} onChange={(e) => setValue(e.target.value)} maxLength={200} autoFocus />
      <Button type="submit" size="sm" disabled={!value.trim()}>Save</Button>
      <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
    </form>
  );
}

function FeatureField({ task, options, onLink }: { task: BoardTask; options: BoardOptions; onLink: (feature: string) => Promise<boolean> }) {
  const features = options.features.filter((f) => f.id !== task.id);
  if (task.tasks.length > 0) {
    return <Field label="Feature"><p className="py-1.5 text-sm text-muted-foreground">This is a Feature with {task.tasks.length} Tasks.</p></Field>;
  }
  const items = [{ value: NO_FEATURE, label: "No Feature" }, ...features.map((f) => ({ value: f.id, label: f.title }))];
  return (
    <Field label="Feature">
      <Select items={items} value={task.feature ?? NO_FEATURE}
        onValueChange={(next) => { const value = next === NO_FEATURE ? "" : String(next ?? ""); if (value !== (task.feature ?? "")) void onLink(value); }}>
        <SelectTrigger aria-label="Feature" className="w-full min-w-0"><SelectValue className="truncate" /></SelectTrigger>
        <SelectContent>{items.map((f) => <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>)}</SelectContent>
      </Select>
    </Field>
  );
}

/** Who holds the Task, how fresh its claim is, and what to do when it stopped. */
function HolderPanel({ task, now, simple, onRelease, onBack }: {
  task: BoardTask; now: Date; simple: boolean; onRelease: () => void; onBack: () => void;
}) {
  if (task.stale && task.previous_holder) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Stale: {who(task.previous_holder)} stopped</AlertTitle>
        <AlertDescription>
          Its session ended or made no progress for too long, so its claim was released. Another agent can take it over from
          its last checkpoint; agents in its project may do so on their own.
        </AlertDescription>
        <AlertAction>
          <Button variant="outline" size="sm" onClick={onBack}>Move back to {task.acceptance.length ? "Ready" : "Backlog"}</Button>
        </AlertAction>
      </Alert>
    );
  }
  if (!task.holder) return null;
  return (
    <div className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border p-3 text-sm",
      task.stalling ? "border-amber-500/40 bg-amber-500/10" : "border-border")}>
      <StatusDot connected={!task.stalling} decorative />
      <span className="min-w-0"><span className="font-medium">{who(task.holder)}</span> is working on it</span>
      {!simple && (
        <span className="text-xs text-muted-foreground">
          on {task.holder.device} since <Timestamp seconds={task.holder.since} now={now} />
          {task.holder.progress_at !== undefined && <> · last progress {ago(task.holder.progress_at, now)}</>}
        </span>
      )}
      {task.stalling && <span className="basis-full text-xs text-amber-800 dark:text-amber-200">Stalling: no progress for a while. The agent has been told; its claim is released if this goes on.</span>}
      <Button variant="outline" size="xs" className="ml-auto" onClick={onRelease}><UserMinus aria-hidden /> Release</Button>
    </div>
  );
}

function BlockedPanel({ task, refs, onBlock, onUnblock }: {
  task: BoardTask; refs: (text: string) => React.ReactNode; onBlock: (reason: string) => Promise<boolean>; onUnblock: () => void;
}) {
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  if (task.blocked) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Blocked</AlertTitle>
        <AlertDescription>{refs(task.blocked.reason)} <span className="text-xs">({who(task.blocked.by)})</span></AlertDescription>
        <AlertAction><Button variant="outline" size="sm" onClick={onUnblock}><Unlock aria-hidden /> Clear</Button></AlertAction>
      </Alert>
    );
  }
  if (!asking) {
    return <div><Button variant="ghost" size="sm" className="-ml-2 text-muted-foreground" onClick={() => setAsking(true)}><Ban aria-hidden /> Mark blocked</Button></div>;
  }
  return (
    <form className="flex gap-2" onSubmit={async (e) => { e.preventDefault(); if (await onBlock(reason)) { setAsking(false); setReason(""); } }}>
      <Input aria-label="Why it is blocked" placeholder="Why it cannot go on" value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
      <Button type="submit" size="sm" disabled={!reason.trim()}>Block</Button>
      <Button type="button" size="sm" variant="ghost" onClick={() => setAsking(false)}>Cancel</Button>
    </form>
  );
}

function ListEditor({ items, refs, empty, label, onSave }: {
  items: string[]; refs: (text: string) => React.ReactNode; empty: string; label: string; onSave: (items: string[]) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  if (!editing) {
    return (
      <div className="flex items-start gap-2">
        {items.length ? <ul className="min-w-0 flex-1 list-disc space-y-0.5 pl-5 text-sm">{items.map((item, i) => <li key={i}>{refs(item)}</li>)}</ul>
          : <p className="flex-1 text-sm text-muted-foreground">{empty}</p>}
        <Button variant="ghost" size="icon-xs" aria-label={`Edit ${label}`} onClick={() => { setText(items.join("\n")); setEditing(true); }}><Pencil aria-hidden /></Button>
      </div>
    );
  }
  return (
    <form className="grid gap-2" onSubmit={async (e) => { e.preventDefault(); if (await onSave(lines(text))) setEditing(false); }}>
      <Textarea aria-label={label} value={text} onChange={(e) => setText(e.target.value)} rows={4} autoFocus />
      <div className="flex gap-2"><Button type="submit" size="sm">Save</Button><Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button></div>
    </form>
  );
}

function DescriptionEditor({ text, onOpenTask, onSave }: { text: string; onOpenTask: (id: string) => void; onSave: (text: string) => Promise<boolean> }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(text);
  const renderLink = useMemo(() => taskLinkRenderer(onOpenTask), [onOpenTask]);
  if (!editing) {
    return (
      <div className="flex items-start gap-2">
        {text ? <Markdown text={text} className="min-w-0 flex-1 text-sm" remarkPlugins={[remarkTaskLinks]} renderLink={renderLink} /> : <p className="flex-1 text-sm text-muted-foreground">No description.</p>}
        <Button variant="ghost" size="icon-xs" aria-label="Edit the description" onClick={() => { setValue(text); setEditing(true); }}><Pencil aria-hidden /></Button>
      </div>
    );
  }
  return (
    <form className="grid gap-2" onSubmit={async (e) => { e.preventDefault(); if (await onSave(value)) setEditing(false); }}>
      <Textarea aria-label="Description" value={value} onChange={(e) => setValue(e.target.value)} rows={6} autoFocus />
      <div className="flex gap-2"><Button type="submit" size="sm">Save</Button><Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button></div>
    </form>
  );
}

function Chips({ values, prefix = "", label, onAdd, onRemove }: {
  values: string[]; prefix?: string; label: string; onAdd: (value: string) => Promise<boolean>; onRemove: (value: string) => void;
}) {
  const [value, setValue] = useState("");
  return (
    <div className="flex flex-wrap items-center gap-1">
      {values.map((v) => (
        <Badge key={v} variant="secondary" className="h-6 gap-0.5 pr-0.5 font-normal">
          {prefix}{v === "user" ? "you (user)" : v}
          <Button variant="ghost" size="icon-xs" className="size-5" aria-label={`Remove ${label} ${v}`} onClick={() => onRemove(v)}><X aria-hidden /></Button>
        </Badge>
      ))}
      <form className="flex items-center gap-1" onSubmit={async (e) => { e.preventDefault(); if (value.trim() && await onAdd(value.trim())) setValue(""); }}>
        <Input aria-label={`Add a ${label}`} placeholder={`Add ${label}`} value={value} onChange={(e) => setValue(e.target.value)} className="h-7 w-28 text-xs" />
        <Button type="submit" variant="ghost" size="icon-xs" aria-label={`Add ${label}`} disabled={!value.trim()}><Plus aria-hidden /></Button>
      </form>
    </div>
  );
}

function Comments({ task, now, refs, onSend }: {
  task: BoardTask; now: Date; refs: (text: string) => React.ReactNode; onSend: (text: string, to: string[]) => Promise<boolean>;
}) {
  const id = useId();
  const [text, setText] = useState("");
  const [to, setTo] = useState("");
  const shown = task.comments.slice(-20);
  const suggestions = [...new Set(["holder", ...(task.holder?.name ? [task.holder.name] : []), ...task.assignees.filter((a) => a !== "user")])];
  return (
    <Section title={`Comments${task.comment_count ? ` (${task.comment_count})` : ""}`}>
      {shown.length > 0 ? (
        <ol className="space-y-2">
          {shown.map((c) => (
            <li key={c.id} className="rounded-lg bg-muted/60 p-2.5 text-sm">
              <p className="text-xs text-muted-foreground">
                <span className="font-medium text-foreground">{c.by.kind === "user" ? "You" : who(c.by)}</span>
                {c.to.length > 0 && <> to {c.to.map((t) => (t === "user" ? "you" : t)).join(", ")}</>}, <Timestamp seconds={c.at} now={now} />
              </p>
              <p className="mt-1 whitespace-pre-wrap">{refs(c.text)}</p>
            </li>
          ))}
        </ol>
      ) : <p className="text-sm text-muted-foreground">No comments yet. Agents read the ones addressed to them on their next Imprint call.</p>}
      <form className="grid gap-2" onSubmit={async (e) => {
        e.preventDefault();
        const sent = await onSend(text, to.split(",").map((t) => t.trim()).filter(Boolean));
        if (sent) { setText(""); setTo(""); }
      }}>
        <Textarea aria-label="Comment" placeholder="Write a comment; @name addresses an agent" value={text} onChange={(e) => setText(e.target.value)} rows={2} />
        <div className="flex flex-wrap items-center gap-2">
          <Label htmlFor={`${id}-to`} className="text-xs font-normal text-muted-foreground">To</Label>
          <Input id={`${id}-to`} value={to} onChange={(e) => setTo(e.target.value)} list={`${id}-who`} placeholder="holder (default)" className="h-8 w-44 text-xs" />
          <datalist id={`${id}-who`}>{suggestions.map((s) => <option key={s} value={s} />)}</datalist>
          <Button type="submit" size="sm" className="ml-auto" disabled={!text.trim()}><Send aria-hidden /> Send</Button>
        </div>
      </form>
    </Section>
  );
}

function BackButton({ to, onBack }: { to: string; onBack: () => void }) {
  return (
    <Button variant="ghost" size="xs" className="-ml-2 text-muted-foreground" onClick={onBack} aria-label={`Back to ${to}`}>
      <ArrowLeft aria-hidden /> <span className="font-mono">{to}</span>
    </Button>
  );
}

/** What an event in the history did, in words. */
function eventText(event: TaskEvent): string {
  const label = KIND_LABELS[event.kind] ?? event.kind;
  if (event.kind === "moved" && typeof event.data.status === "string") {
    return `${label} to ${STATUS_LABELS[event.data.status as TaskStatus] ?? event.data.status}`;
  }
  if (event.kind === "edited") {
    const fields = Object.keys(event.data).filter((field) => field in FIELD_NAMES);
    if (fields.length === 1 && fields[0] === "project" && typeof event.data.project === "string") return `moved it to the project ${event.data.project}`;
    if (fields.length) return `changed ${fields.map((field) => FIELD_NAMES[field]).join(", ")}`;
  }
  return label;
}

/** The project: one the Board knows, or another typed in. */
function ProjectField({ task, options, onSave }: { task: BoardTask; options: BoardOptions; onSave: (project: string) => Promise<boolean> }) {
  const [typing, setTyping] = useState(false);
  const [value, setValue] = useState("");
  const projects = [...new Set([task.project, ...options.projects])].sort((a, b) => a.localeCompare(b));
  if (typing) {
    return (
      <Field label="Project">
        <form className="flex min-w-0 gap-1" onSubmit={async (e) => { e.preventDefault(); if (await onSave(value.trim())) setTyping(false); }}>
          <Input aria-label="Project name" value={value} onChange={(e) => setValue(e.target.value)} placeholder="Project name" maxLength={200}
            className="h-8 min-w-0" autoFocus onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setTyping(false); } }} />
          <Button type="submit" size="sm" disabled={!value.trim() || value.trim() === task.project}>Save</Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setTyping(false)}>Cancel</Button>
        </form>
      </Field>
    );
  }
  const items = [...projects.map((p) => ({ value: p, label: p })), { value: OTHER_PROJECT, label: "Another project…" }];
  return (
    <Field label="Project">
      <Select items={items} value={task.project}
        onValueChange={(next) => {
          if (next === OTHER_PROJECT) { setValue(""); setTyping(true); }
          else if (next && next !== task.project) void onSave(String(next));
        }}>
        <SelectTrigger aria-label="Project" className="w-full min-w-0"><SelectValue className="truncate" /></SelectTrigger>
        <SelectContent>{items.map((p) => <SelectItem key={p.value} value={p.value}>{p.label}</SelectItem>)}</SelectContent>
      </Select>
    </Field>
  );
}

const STATUS_MARKS: Record<TaskStatus, { icon: typeof Circle; tone: string }> = {
  backlog: { icon: CircleDashed, tone: "text-muted-foreground" },
  ready: { icon: Circle, tone: "text-muted-foreground" },
  in_progress: { icon: CircleDot, tone: "text-amber-600 dark:text-amber-400" },
  review: { icon: Eye, tone: "text-sky-600 dark:text-sky-400" },
  done: { icon: CircleCheck, tone: "text-emerald-600 dark:text-emerald-400" },
  cancelled: { icon: CircleX, tone: "text-muted-foreground" },
};

type Link = { id: string; info: RelatedTask | null };

/** The Tasks this one links to, each a row that opens it: its Feature, the
 * Feature's Tasks, what it waits on and what waits on it. */
function RelatedTasks({ task, related, known, onOpen, onAddBlocker, onRemoveBlocker }: {
  task: BoardTask; related: Record<string, RelatedTask>; known: BoardTask[]; onOpen: (id: string) => void;
  onAddBlocker: (id: string) => Promise<boolean>; onRemoveBlocker: (id: string) => void;
}) {
  const [all, setAll] = useState(false);
  const [adding, setAdding] = useState(false);
  const [value, setValue] = useState("");
  const listId = useId();
  const link = (id: string): Link => {
    const shown = known.find((t) => t.id === id);
    return { id, info: related[id] ?? (shown ? { title: shown.title, status: shown.status, priority: shown.priority, project: shown.project,
      stale: shown.stale, blocked: !!shown.blocked } : null) };
  };
  const order = (a: Link, b: Link) => (a.info && b.info
    ? LINK_ORDER.indexOf(a.info.status) - LINK_ORDER.indexOf(b.info.status) || a.info.priority.localeCompare(b.info.priority)
    : 0) || a.id.localeCompare(b.id);
  const children = task.tasks.map(link).sort(order);
  const shownChildren = all ? children : children.slice(0, TASKS_SHOWN);
  const blockers = task.blockers.map(link);
  const blocking = (task.blocking ?? []).map(link).sort(order);
  const candidates = known.filter((t) => t.id !== task.id && !task.blockers.includes(t.id) && t.status !== "done" && t.status !== "cancelled");
  const groups: { title: string; links: Link[]; removable?: boolean; more?: number }[] = [
    { title: "Feature", links: task.feature ? [link(task.feature)] : [] },
    { title: `Tasks in this Feature (${children.length})`, links: shownChildren, more: children.length - shownChildren.length },
    { title: "Waits on", links: blockers, removable: true },
    { title: "Waiting on it", links: blocking },
  ].filter((g) => g.links.length > 0);
  return (
    <Section title="Related">
      {groups.length === 0 && <p className="text-sm text-muted-foreground">Not linked to other tasks.</p>}
      {groups.map((group) => (
        <div key={group.title} className="grid gap-0.5">
          <h4 className="text-[11px] font-medium text-muted-foreground">{group.title}</h4>
          <ul className="-mx-2 grid">
            {group.links.map((l) => (
              <RelatedRow key={l.id} link={l} onOpen={onOpen} onRemove={group.removable ? () => onRemoveBlocker(l.id) : undefined} />
            ))}
          </ul>
          {group.more ? (
            <Button variant="link" size="sm" className="h-auto justify-self-start px-0 text-xs" onClick={() => setAll(true)}>
              Show all {children.length}
            </Button>
          ) : null}
        </div>
      ))}
      {adding ? (
        <form className="flex flex-wrap items-center gap-2" onSubmit={async (e) => {
          e.preventDefault();
          if (await onAddBlocker(value.trim())) { setValue(""); setAdding(false); }
        }}>
          <Input aria-label="Task it waits on" placeholder="T-xxxxx" value={value} onChange={(e) => setValue(e.target.value)} list={listId}
            className="h-8 w-40 font-mono text-xs" autoFocus
            onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setAdding(false); } }} />
          <datalist id={listId}>{candidates.map((t) => <option key={t.id} value={t.id} label={t.title} />)}</datalist>
          <Button type="submit" size="sm" disabled={!value.trim()}>Add</Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setAdding(false)}>Cancel</Button>
        </form>
      ) : (
        <div>
          <Button variant="ghost" size="sm" className="-ml-2 text-muted-foreground" onClick={() => setAdding(true)}>
            <Plus aria-hidden /> Add a task it waits on
          </Button>
        </div>
      )}
    </Section>
  );
}

function RelatedRow({ link, onOpen, onRemove }: { link: Link; onOpen: (id: string) => void; onRemove?: () => void }) {
  const { id, info } = link;
  const mark = info ? STATUS_MARKS[info.status] : null;
  const Icon = mark?.icon ?? Circle;
  const ended = info?.status === "done" || info?.status === "cancelled";
  return (
    <li className="group/row flex min-w-0 items-center gap-1 rounded-md hover:bg-muted/60">
      <button type="button" onClick={() => onOpen(id)} disabled={!info}
        aria-label={info ? `${id}: ${info.title}, ${STATUS_LABELS[info.status]}. Open` : `${id}, not on this Board`}
        className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-default">
        <Icon className={cn("size-3.5 shrink-0", mark?.tone ?? "text-muted-foreground")} aria-hidden />
        <span className="shrink-0 font-mono text-xs text-muted-foreground">{id}</span>
        <span className={cn("min-w-0 truncate text-sm", ended && "text-muted-foreground", info?.status === "cancelled" && "line-through")}>
          {info?.title ?? "Not on this Board any more"}
        </span>
        {info && (
          <span className="ml-auto flex shrink-0 items-center gap-1.5 pl-2 text-[11px] text-muted-foreground">
            {info.stale && <Badge variant="outline" className="h-4 border-destructive/40 px-1 text-[10px] text-destructive">Stale</Badge>}
            {info.blocked && <Badge variant="outline" className="h-4 border-destructive/40 px-1 text-[10px] text-destructive">Blocked</Badge>}
            <span className="hidden sm:inline">{STATUS_LABELS[info.status]}</span>
            <span className="tabular-nums">{info.priority}</span>
          </span>
        )}
      </button>
      {onRemove && (
        <Button variant="ghost" size="icon-xs" className="mr-1 shrink-0 opacity-60 group-hover/row:opacity-100 focus-visible:opacity-100"
          aria-label={`Stop waiting on ${id}`} title="Stop waiting on it" onClick={onRemove}>
          <X aria-hidden />
        </Button>
      )}
    </li>
  );
}
