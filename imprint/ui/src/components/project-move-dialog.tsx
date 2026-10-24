"use client";

import { useId, useState } from "react";
import { toast } from "sonner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Spinner } from "@/components/loaders";
import { moveProject, type ProjectMove } from "@/lib/api";

type Destination = "existing" | "new";

const count = (n: number, noun: string) => `${n.toLocaleString()} ${noun}${n === 1 ? "" : "s"}`;

type Props = {
  open: boolean;
  project: string;
  /** Source keys to move; null moves (or merges) the whole project. */
  sources: string[] | null;
  /** Projects of the workspace, as move targets. */
  projects: string[];
  onClose: () => void;
  onMoved: (result: ProjectMove) => void;
};

/** Choose a target project, review what moves, then confirm (ADR 0036). */
export function ProjectMoveDialog(props: Props) {
  return props.open ? <ProjectMoveDialogContent {...props} /> : null;
}

function ProjectMoveDialogContent({ project, sources, projects, onClose, onMoved }: Props) {
  const targets = projects.filter((name) => name !== project);
  const [destination, setDestination] = useState<Destination>(targets.length ? "existing" : "new");
  const [existing, setExisting] = useState("");
  const [name, setName] = useState("");
  const [preview, setPreview] = useState<ProjectMove | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fieldId = useId();

  const target = destination === "existing" ? existing : name.trim();
  const what = sources ? `${count(sources.length, "source")} from ${project}` : project;

  async function review() {
    setBusy(true); setError("");
    try { setPreview(await moveProject({ project, target, sources: sources ?? undefined })); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function apply() {
    if (!preview) return;
    setBusy(true); setError("");
    try {
      const result = await moveProject({ project, target: preview.target, sources: sources ?? undefined,
                                         workspace: preview.workspace, apply: true });
      toast.success(`Moved ${count(result.chunks, "chunk")} to ${result.target}`);
      onMoved(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(next) => { if (!next && !busy) onClose(); }}>
      <DialogContent showCloseButton={false} aria-busy={busy} className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{sources ? "Move sources to another project" : `Move or merge ${project}`}</DialogTitle>
          <DialogDescription>
            {sources
              ? `Moves every memory of ${what}.`
              : "Moves every memory of this project. Into an existing project this is a merge."}{" "}
            Refresh, learning and sync keep them in the new project. Files and source keys don&apos;t change.
          </DialogDescription>
        </DialogHeader>

        {!preview ? (
          <RadioGroup aria-label="Target project" value={destination} disabled={busy}
            onValueChange={(value) => setDestination(value as Destination)} className="gap-3">
            <div className="space-y-1.5">
              <Label className="font-normal"><RadioGroupItem value="existing" disabled={!targets.length} /> Existing project</Label>
              <Select value={existing || null} disabled={busy || destination !== "existing" || !targets.length}
                onValueChange={(value) => setExisting((value as string | null) ?? "")}>
                <SelectTrigger id={`${fieldId}-existing`} aria-label="Existing project" className="w-full">
                  <SelectValue placeholder={targets.length ? "Select a project" : "No other projects"} />
                </SelectTrigger>
                <SelectContent>
                  {targets.map((option) => <SelectItem key={option} value={option}>{option}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label className="font-normal"><RadioGroupItem value="new" /> New project</Label>
              <Input aria-label="New project name" placeholder="Project name" value={name} maxLength={256}
                disabled={busy || destination !== "new"} onChange={(e) => setName(e.target.value)} />
            </div>
          </RadioGroup>
        ) : (
          <div className="space-y-2" role="status">
            <p>
              {count(preview.chunks, "chunk")}
              {preview.sources ? ` from ${count(preview.sources, "source")}` : ""} move from{" "}
              <span className="font-medium">{preview.project}</span> to{" "}
              <span className="font-medium">{preview.target}</span>
              {preview.target_exists ? ", an existing project." : ", a new project."}
            </p>
            {preview.whole && (
              <p className="text-muted-foreground">
                Memories saved as {preview.project} later, by refresh, learning or agents, also go to {preview.target}.
              </p>
            )}
            {preview.sample.length > 0 && (
              <ul className="max-h-32 overflow-auto rounded border p-2 font-mono text-xs">
                {preview.sample.map((source) => <li key={source} className="truncate">{source}</li>)}
              </ul>
            )}
            <p className="text-xs text-muted-foreground">
              To undo, move them back. Paired devices keep their own project layout.
            </p>
          </div>
        )}

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <DialogFooter>
          <Button variant="ghost" disabled={busy} onClick={preview ? () => setPreview(null) : onClose}>
            {preview ? "Back" : "Cancel"}
          </Button>
          {preview ? (
            <Button disabled={busy || preview.chunks === 0} onClick={apply}>
              {busy && <Spinner />} Move {count(preview.chunks, "chunk")}
            </Button>
          ) : (
            <Button disabled={busy || !target || target === project} onClick={review}>
              {busy && <Spinner />} Review move
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
