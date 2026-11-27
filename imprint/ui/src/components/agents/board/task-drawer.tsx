"use client";

import { useId, useState } from "react";
import { Ban, GitBranch, Pencil, Plus, Send, Trash2, Unlock, UserMinus, X } from "lucide-react";
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
  TASK_STATUSES, changeTask, deleteTask, getTask, type BoardOptions, type BoardTask, type TaskAction, type TaskDetails,
  type TaskPriority, type TaskStatus,
} from "@/lib/api";
import { useCached } from "@/lib/use-cached";
import { cn } from "@/lib/utils";
import { StatusDot } from "../status";
import { PRIORITY_LABELS, STATUS_HINTS, STATUS_LABELS, ago, who } from "./format";
import { lines } from "./new-task-dialog";

const PRIORITIES: TaskPriority[] = ["P0", "P1", "P2", "P3"];
const NO_FEATURE = "__none__";
const KIND_LABELS: Record<string, string> = {
  created: "created it", edited: "edited", appended: "added to the description", moved: "moved it", tagged: "changed tags",
  assigned: "changed assignees", linked: "changed links", blocked: "marked it blocked", unblocked: "cleared the block",
  claimed: "claimed it", released: "released it", checkpoint: "recorded a checkpoint", comment: "commented", deleted: "deleted it",
  renamed: "gave it a new id, since another device's Task had the same one",
};

/** One Task: everything about it, and every change the user can make. */
export function TaskDrawer({ taskId, options, now, simple, onChanged, onClose }: {
  taskId: string; options: BoardOptions; now: Date; simple: boolean;
  onChanged: () => void; onClose: () => void;
}) {
  const details = useCached<TaskDetails>(`task:${taskId}`, () => getTask(taskId), { refreshInterval: 5000, persist: false });
  const [deleting, setDeleting] = useState(false);

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
        <SheetHeader className="border-b border-border pr-12"><SheetTitle>{taskId}</SheetTitle></SheetHeader>
        <div className="p-4" aria-busy={!details.error}>
          {details.error ? (
            <Alert variant="destructive"><AlertDescription>{details.error.message}</AlertDescription></Alert>
          ) : <Spinner />}
        </div>
      </>
    );
  }
  const { task, history, handoff } = details.data;
  const ended = task.status === "done" || task.status === "cancelled";
  return (
    <>
      <SheetHeader className="gap-1 border-b border-border pr-12">
        <SheetDescription className="flex items-center gap-2 font-mono text-xs">
          {task.id} · {task.project}{task.former_ids?.length ? ` · was ${task.former_ids.join(", ")}` : ""}
        </SheetDescription>
        <EditableTitle task={task} onSave={(title) => act("edit", { title })} />
      </SheetHeader>
      <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto p-4">
        <div className="grid gap-3 sm:grid-cols-3">
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
          <FeatureField task={task} options={options} onLink={(feature) => act("link", { feature })} />
          <p className="-mt-1 text-xs text-muted-foreground sm:col-span-3">{STATUS_LABELS[task.status]}: {STATUS_HINTS[task.status].toLowerCase()}.</p>
        </div>

        <HolderPanel task={task} now={now} simple={simple} onRelease={() => act("release", {}, "Claim released")}
          onBack={() => act("move", { status: task.acceptance.length ? "ready" : "backlog" }, task.acceptance.length ? "Back in Ready" : "Back in Backlog")} />
        <BlockedPanel task={task} onBlock={(reason) => act("block", { reason })} onUnblock={() => act("unblock")} />

        <Section title="Acceptance criteria">
          <ListEditor items={task.acceptance} empty="None yet: add some before moving it to Ready."
            label="Acceptance criteria, one per line" onSave={(items) => act("edit", { acceptance: items })} />
        </Section>
        <Section title="Description">
          <DescriptionEditor text={task.description} onSave={(description) => act("edit", { description })} />
        </Section>
        <Section title="Tags">
          <Chips values={task.tags} prefix="#" label="tag" onAdd={(v) => act("tags", { add: [v] })} onRemove={(v) => act("tags", { remove: [v] })} />
        </Section>
        <Section title="Assignees">
          <Chips values={task.assignees} label="assignee" onAdd={(v) => act("assignees", { add: [v] })} onRemove={(v) => act("assignees", { remove: [v] })} />
          <p className="text-xs text-muted-foreground">
            {task.assignees.length ? "Only these take it unasked." : "None: any agent may take it."} &quot;user&quot; is you; also a client (codex), a device (@laptop) or both (codex@laptop).
          </p>
        </Section>
        {(task.open_blockers.length > 0 || task.blockers.length > 0 || task.tasks.length > 0) && (
          <Section title="Links">
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
              {task.blockers.length > 0 && <><dt className="text-muted-foreground">Waits on</dt><dd className="font-mono text-xs leading-5">
                {task.blockers.map((b) => `${b}${task.open_blockers.includes(b) ? " (open)" : " (done)"}`).join(", ")}</dd></>}
              {task.tasks.length > 0 && <><dt className="text-muted-foreground">Its Tasks</dt><dd className="font-mono text-xs leading-5">{task.tasks.join(", ")}</dd></>}
            </dl>
          </Section>
        )}

        {(handoff.last_checkpoint || handoff.branch) && (
          <Section title="Where the work stands">
            {handoff.last_checkpoint && (
              <div className="rounded-lg bg-muted/60 p-3 text-sm">
                <p className="text-xs text-muted-foreground">Last checkpoint, {who(handoff.last_checkpoint.by)}, <Timestamp seconds={handoff.last_checkpoint.at} now={now} /></p>
                <p className="mt-1 whitespace-pre-wrap">{handoff.last_checkpoint.text}</p>
                {handoff.last_checkpoint.next && <p className="mt-1 text-muted-foreground">Next: {handoff.last_checkpoint.next}</p>}
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

        <Comments task={task} now={now} onSend={(text, to) => act("comment", { text, to })} />

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
                      {KIND_LABELS[event.kind] ?? event.kind}
                      {event.kind === "moved" && typeof event.data.status === "string" && ` to ${STATUS_LABELS[event.data.status as TaskStatus] ?? event.data.status}`}
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

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1">
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
        <SelectTrigger aria-label="Feature" className="w-full"><SelectValue /></SelectTrigger>
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

function BlockedPanel({ task, onBlock, onUnblock }: { task: BoardTask; onBlock: (reason: string) => Promise<boolean>; onUnblock: () => void }) {
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  if (task.blocked) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Blocked</AlertTitle>
        <AlertDescription>{task.blocked.reason} <span className="text-xs">({who(task.blocked.by)})</span></AlertDescription>
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

function ListEditor({ items, empty, label, onSave }: { items: string[]; empty: string; label: string; onSave: (items: string[]) => Promise<boolean> }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  if (!editing) {
    return (
      <div className="flex items-start gap-2">
        {items.length ? <ul className="min-w-0 flex-1 list-disc space-y-0.5 pl-5 text-sm">{items.map((item, i) => <li key={i}>{item}</li>)}</ul>
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

function DescriptionEditor({ text, onSave }: { text: string; onSave: (text: string) => Promise<boolean> }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(text);
  if (!editing) {
    return (
      <div className="flex items-start gap-2">
        {text ? <Markdown text={text} className="min-w-0 flex-1 text-sm" /> : <p className="flex-1 text-sm text-muted-foreground">No description.</p>}
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

function Comments({ task, now, onSend }: { task: BoardTask; now: Date; onSend: (text: string, to: string[]) => Promise<boolean> }) {
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
              <p className="mt-1 whitespace-pre-wrap">{c.text}</p>
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
