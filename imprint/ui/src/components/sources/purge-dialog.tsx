"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Spinner } from "@/components/loaders";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { getWorkspaces, purgeSources, type PurgePreview } from "@/lib/api";

const MATCH = { source: "Source key", path: "Absolute file path" };
const plural = (n: number, noun: string) => `${n.toLocaleString()} ${noun}${n === 1 ? "" : "s"}`;

/** What to purge: a source key or a file path, alone or with what is under it. */
export type PurgeTarget = { target: string; kind: "source" | "path"; recursive: boolean };

/** Purge indexed copies from the active workspace (ADR 0015): preview what
 * matches, then confirm by typing the workspace's name. A purged path stays
 * excluded from ingestion until it is allowed again. Opened with a target,
 * it previews at once. */
export function PurgeDialog({ open, initial, onClose, onPurged }: {
  open: boolean; initial?: PurgeTarget | null; onClose: () => void; onPurged: (result: PurgePreview) => void;
}) {
  return open ? <PurgeDialogContent initial={initial ?? null} onClose={onClose} onPurged={onPurged} /> : null;
}

function PurgeDialogContent({ initial, onClose, onPurged }: {
  initial: PurgeTarget | null; onClose: () => void; onPurged: (result: PurgePreview) => void;
}) {
  const [workspace, setWorkspace] = useState("");
  const [target, setTarget] = useState(initial?.target ?? "");
  const [kind, setKind] = useState<"source" | "path">(initial?.kind ?? "source");
  const [recursive, setRecursive] = useState(initial?.recursive ?? false);
  const [preview, setPreview] = useState<PurgePreview | null>(null);
  const [busy, setBusy] = useState(!!initial);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [typed, setTyped] = useState("");

  useEffect(() => {
    let live = true;
    getWorkspaces().then((ws) => {
      if (!live) return;
      setWorkspace(ws.active);
      if (initial) {
        return purgeSources({ workspace: ws.active, ...initial }).then((result) => { if (live) setPreview(result); });
      }
    }).catch((e) => { if (live) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [initial]);

  const change = () => { setPreview(null); setError(""); };

  async function previewPurge() {
    setBusy(true); setError(""); setPreview(null);
    try { setPreview(await purgeSources({ workspace, target: target.trim(), kind, recursive })); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function applyPurge() {
    if (!preview) return;
    setBusy(true); setError("");
    try {
      const result = await purgeSources({ ...preview, apply: true });
      toast.success(`Removed ${plural(result.chunks, "chunk")} from ${plural(result.sources, "source")}`, {
        description: `${result.facts ? `${plural(result.facts, "graph fact")} too. ` : ""}${result.target} stays out of ${result.workspace} until you allow it again under Sources › Excluded.`,
      });
      setConfirm(false);
      onPurged(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setConfirm(false);
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(next) => { if (!next && !busy) onClose(); }}>
      <DialogContent aria-busy={busy} className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Purge from {workspace || "this workspace"}</DialogTitle>
          <DialogDescription>
            Removes the indexed copies of what matches and keeps it out of later ingests. Original files, backups and
            other workspaces stay as they are.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Label htmlFor="cleanup-match" className="font-normal">Match</Label>
            <Select items={MATCH} value={kind} disabled={busy} onValueChange={(value) => { setKind(value as "path" | "source"); change(); }}>
              <SelectTrigger id="cleanup-match" aria-label="Cleanup match type"><SelectValue /></SelectTrigger>
              <SelectContent>
                {Object.entries(MATCH).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <Input aria-label="Cleanup target" placeholder={kind === "source" ? "project/directory or project/file.py" : "Absolute file or directory path"}
            value={target} disabled={busy} onChange={(e) => { setTarget(e.target.value); change(); }}
            onKeyDown={(e) => { if (e.key === "Enter" && target.trim() && !busy) { e.preventDefault(); void previewPurge(); } }} />
          <Label className="font-normal">
            <Checkbox checked={recursive} disabled={busy} onCheckedChange={(checked) => { setRecursive(checked); change(); }} />
            Include directory descendants
          </Label>
          {preview && (
            <div className="space-y-2 rounded-lg border border-border bg-muted/40 p-3">
              <p role="status">
                {plural(preview.chunks, "chunk")} in {plural(preview.sources, "source")} · {plural(preview.facts || 0, "graph fact")} · workspace {preview.workspace}
              </p>
              <p className="font-mono text-xs break-all">{preview.target}{preview.recursive ? "/**" : ""}</p>
              {preview.sample.length > 0 && (
                <ul className="max-h-32 overflow-auto font-mono text-xs text-muted-foreground">
                  {preview.sample.map((item) => <li key={`${item.project}/${item.source}`} className="truncate">{item.source}</li>)}
                </ul>
              )}
            </div>
          )}
          {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        </div>
        <DialogFooter>
          <Button variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button>
          {preview ? (
            <Button variant="destructive" disabled={busy || (!preview.chunks && !preview.sources)} onClick={() => { setTyped(""); setConfirm(true); }}>
              Purge from {preview.workspace}…
            </Button>
          ) : (
            <Button variant="outline" disabled={busy || !target.trim() || !workspace} onClick={() => void previewPurge()}>
              {busy && <Spinner />} Preview cleanup
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
      <AlertDialog open={confirm && !!preview} onOpenChange={(next) => { if (!next && !busy) setConfirm(false); }}>
        {preview && (
          <AlertDialogContent aria-busy={busy}>
            <AlertDialogHeader>
              <AlertDialogTitle>Purge from {preview.workspace}?</AlertDialogTitle>
              <AlertDialogDescription className="wrap-anywhere">
                Remove the indexed copies of {preview.target} ({plural(preview.chunks, "chunk")}) and keep it out of ingestion until
                it is allowed again. This does not remove backups or original files.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <div className="space-y-1.5">
              <p className="text-xs text-muted-foreground">
                Type <code className="rounded bg-muted px-1 font-mono">{preview.workspace}</code> to confirm:
              </p>
              <Input aria-label={`Type ${preview.workspace} to confirm`} value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus disabled={busy} />
            </div>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
              <Button variant="destructive" disabled={busy || typed !== preview.workspace} onClick={() => void applyPurge()}>
                {busy && <Spinner />} Purge indexed copies
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        )}
      </AlertDialog>
    </Dialog>
  );
}
