"use client";

import { useId, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { createTask, type BoardOptions, type BoardTask, type TaskPriority } from "@/lib/api";
import { PRIORITY_LABELS } from "./format";

const PRIORITIES: TaskPriority[] = ["P0", "P1", "P2", "P3"];
const NO_FEATURE = "__none__";

/** Lines of a textarea, or comma-separated words of an input, without blanks. */
export const lines = (text: string) => text.split("\n").map((l) => l.trim()).filter(Boolean);
export const words = (text: string) => text.split(",").map((w) => w.trim()).filter(Boolean);

/** A new Task, put on the Board as the user's. Ready needs at least one
 * acceptance criterion, so an agent taking it unasked knows when it is done. */
export function NewTaskDialog({ open, onOpenChange, options, defaultProject, onCreated }: {
  open: boolean; onOpenChange: (open: boolean) => void; options: BoardOptions; defaultProject: string;
  onCreated: (task: BoardTask) => void;
}) {
  const id = useId();
  const [title, setTitle] = useState("");
  const [project, setProject] = useState(defaultProject);
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<TaskPriority>("P2");
  const [criteria, setCriteria] = useState("");
  const [tags, setTags] = useState("");
  const [assignees, setAssignees] = useState("");
  const [feature, setFeature] = useState(NO_FEATURE);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const readyBlocked = ready && lines(criteria).length === 0;

  function reset() {
    setTitle(""); setDescription(""); setPriority("P2"); setCriteria(""); setTags(""); setAssignees("");
    setFeature(NO_FEATURE); setReady(false); setProblem(null);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const { task } = await createTask({
        title, project, description, priority, status: ready ? "ready" : "backlog", acceptance: lines(criteria),
        tags: words(tags), assignees: words(assignees), feature: feature === NO_FEATURE ? undefined : feature,
      });
      toast.success(`Created ${task.id}`, { description: task.title });
      onCreated(task);
      reset();
      onOpenChange(false);
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const featureItems = [{ value: NO_FEATURE, label: "No Feature" }, ...options.features.map((f) => ({ value: f.id, label: `${f.id} · ${f.title}` }))];
  return (
    <Dialog open={open} onOpenChange={(next) => { if (!busy) onOpenChange(next); }}>
      <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-xl [&>*]:min-w-0">
        <DialogHeader>
          <DialogTitle>New task</DialogTitle>
          <DialogDescription>
            It goes to Backlog. Mark it ready for agents once it says when it is finished: agents may then take it on their own.
          </DialogDescription>
        </DialogHeader>
        <form id={`${id}-form`} onSubmit={submit} className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor={`${id}-title`}>Title</Label>
            <Input id={`${id}-title`} value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200} autoFocus
              placeholder="Add retries to the uploader" />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor={`${id}-project`}>Project</Label>
              <Input id={`${id}-project`} value={project} onChange={(e) => setProject(e.target.value)} required list={`${id}-projects`}
                placeholder="imprint-memory-layer" />
              <datalist id={`${id}-projects`}>{options.projects.map((p) => <option key={p} value={p} />)}</datalist>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor={`${id}-priority`}>Priority</Label>
              <Select items={PRIORITIES.map((p) => ({ value: p, label: PRIORITY_LABELS[p] }))} value={priority}
                onValueChange={(next) => setPriority((next as TaskPriority) ?? "P2")}>
                <SelectTrigger id={`${id}-priority`} className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>{PRIORITIES.map((p) => <SelectItem key={p} value={p}>{PRIORITY_LABELS[p]}</SelectItem>)}</SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`${id}-description`}>Description</Label>
            <Textarea id={`${id}-description`} value={description} onChange={(e) => setDescription(e.target.value)} rows={3}
              placeholder="What and why. Markdown works." />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`${id}-criteria`}>Acceptance criteria</Label>
            <Textarea id={`${id}-criteria`} value={criteria} onChange={(e) => setCriteria(e.target.value)} rows={3}
              aria-describedby={`${id}-criteria-help`} placeholder={"A 503 is retried three times\nThe upload resumes where it stopped"} />
            <p id={`${id}-criteria-help`} className="text-xs text-muted-foreground">One per line. How anyone can tell it is finished.</p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`${id}-tags`}>Tags</Label>
            <Input id={`${id}-tags`} value={tags} onChange={(e) => setTags(e.target.value)} placeholder="upload, bug" aria-describedby={`${id}-tags-help`} />
            <p id={`${id}-tags-help`} className="text-xs text-muted-foreground">Separated by commas.</p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`${id}-assignees`}>Assign to</Label>
            <Input id={`${id}-assignees`} value={assignees} onChange={(e) => setAssignees(e.target.value)} aria-describedby={`${id}-assign-help`}
              placeholder="codex, user" />
            <p id={`${id}-assign-help`} className="text-xs text-muted-foreground">
              Empty: any agent. &quot;user&quot; is you; also a client (codex), a device (@laptop) or both (codex@laptop).
            </p>
          </div>
          {options.features.length > 0 && (
            <div className="grid gap-1.5">
              <Label htmlFor={`${id}-feature`}>Feature</Label>
              <Select items={featureItems} value={feature} onValueChange={(next) => setFeature((next as string) ?? NO_FEATURE)}>
                <SelectTrigger id={`${id}-feature`} className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>{featureItems.map((f) => <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>)}</SelectContent>
              </Select>
            </div>
          )}
          <div className="flex items-start gap-2">
            <Checkbox id={`${id}-ready`} checked={ready} onCheckedChange={(checked) => setReady(checked === true)} />
            <div className="grid gap-0.5">
              <Label htmlFor={`${id}-ready`}>Ready for agents</Label>
              <p className={readyBlocked ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
                {readyBlocked ? "Add at least one acceptance criterion first." : "Agents may claim it, also without being asked."}
              </p>
            </div>
          </div>
          {problem && <p role="alert" className="text-sm text-destructive">{problem}</p>}
        </form>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>Cancel</Button>
          <Button type="submit" form={`${id}-form`} disabled={busy || !title.trim() || !project.trim() || readyBlocked}>Create task</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
