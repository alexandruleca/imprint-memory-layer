"use client";

import { useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Spinner } from "@/components/loaders";
import { WorkspaceRoots } from "@/components/workspace-roots";
import { changeWorkspaceRoot, getWorkspaceScope, getWorkspaces, purgeSources, type WorkspaceScope, type PurgePreview } from "@/lib/api";

const MATCH = { source: "Source key", path: "Absolute file path" };

export function WorkspaceSources({ selectedSource, onPurged }: { selectedSource: string | null; onPurged: () => Promise<void> }) {
  const [scope, setScope] = useState<WorkspaceScope | null>(null);
  const [target, setTarget] = useState("");
  const [kind, setKind] = useState<"source" | "path">("source");
  const [recursive, setRecursive] = useState(false);
  const [preview, setPreview] = useState<PurgePreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [typed, setTyped] = useState("");

  useEffect(() => {
    getWorkspaces().then(ws => getWorkspaceScope(ws.active)).then(setScope).catch(e => setError(String(e)));
  }, []);

  async function restore(path: string, kind: "path" | "source") {
    if (!scope) return;
    setBusy(true); setError("");
    try {
      await changeWorkspaceRoot({ workspace: scope.workspace, path, action: "restore", kind });
      setScope(await getWorkspaceScope(scope.workspace));
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  async function previewPurge() {
    if (!scope) return;
    setBusy(true); setError(""); setNotice(""); setPreview(null);
    try { setPreview(await purgeSources({ workspace: scope.workspace, target, kind, recursive })); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  async function applyPurge() {
    if (!preview) return;
    setBusy(true); setError("");
    try {
      const result = await purgeSources({ ...preview, apply: true });
      setNotice(`Removed ${result.chunks} chunks and ${result.facts || 0} graph facts from ${result.sources} sources in ${result.workspace}. Re-ingestion is excluded until restored.`);
      setPreview(null); setConfirm(false);
      setScope(await getWorkspaceScope(result.workspace));
      await onPurged();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  return <Collapsible className="rounded-lg border p-4">
    <CollapsibleTrigger className="group flex w-full cursor-pointer items-center gap-1.5 text-left font-medium">
      <ChevronRight className="size-4 transition-transform group-data-[panel-open]:rotate-90" aria-hidden />
      Workspace roots and cleanup{scope ? ` · ${scope.workspace}` : ""}
    </CollapsibleTrigger>
    <CollapsibleContent className="space-y-3">
      {error && <p role="alert" className="pt-3 text-sm text-destructive">{error}</p>}
      {notice && <p role="status" className="pt-3 text-sm">{notice}</p>}
      {scope && <div className="space-y-4 pt-3 text-sm">
        <WorkspaceRoots scope={scope} onChange={setScope} />
        <div className="border-t pt-4 space-y-3">
          <h3 className="font-medium">Purge indexed files or directories</h3>
          <p className="text-muted-foreground">Preview matching chunks in {scope.workspace}. Purge removes indexed copies and blocks re-ingestion. Original files and other workspaces stay intact.</p>
          <div className="flex gap-2 flex-wrap items-center">
            <Label htmlFor="cleanup-match" className="font-normal">Match</Label>
            <Select items={MATCH} value={kind} disabled={busy} onValueChange={(value) => { setKind(value as "path" | "source"); setPreview(null); }}>
              <SelectTrigger id="cleanup-match" aria-label="Cleanup match type"><SelectValue /></SelectTrigger>
              <SelectContent>
                {Object.entries(MATCH).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}
              </SelectContent>
            </Select>
            <Button variant="outline" size="sm" disabled={busy || !selectedSource} onClick={() => { setTarget(selectedSource || ""); setKind("source"); setRecursive(false); setPreview(null); }}>Use selected source</Button>
          </div>
          <Input aria-label="Cleanup target" placeholder={kind === "source" ? "project/directory or project/file.py" : "Absolute file or directory path"} value={target} disabled={busy} onChange={e => { setTarget(e.target.value); setPreview(null); }} />
          <Label className="font-normal">
            <Checkbox checked={recursive} disabled={busy} onCheckedChange={(checked) => { setRecursive(checked); setPreview(null); }} />
            Include directory descendants
          </Label>
          <Button variant="outline" disabled={busy || !target.trim()} onClick={previewPurge}>{busy && !preview && <Spinner />} Preview cleanup</Button>
          {preview && <div className="space-y-2">
            <p role="status">{preview.chunks} chunks in {preview.sources} sources · {preview.facts || 0} graph facts · workspace {preview.workspace}</p>
            <p className="font-mono break-all">{preview.target}{preview.recursive ? "/**" : ""}</p>
            <ul>{preview.sample.map(item => <li key={`${item.project}/${item.source}`} className="font-mono text-xs break-all">{item.source}</li>)}</ul>
            <Button variant="destructive" disabled={busy} onClick={() => { setTyped(""); setConfirm(true); }}>Purge from {preview.workspace}…</Button>
          </div>}
        </div>
        {scope.exclusions.length > 0 && <div className="border-t pt-3 space-y-2"><h3 className="font-medium">Excluded after purge</h3>
          {scope.exclusions.map(item => <div key={`${item.kind}/${item.path}`} className="flex gap-2 items-center">
            <code className="break-all flex-1">{item.path}{item.recursive ? "/**" : ""}</code>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => restore(item.path, item.kind)}>Allow re-ingestion</Button>
          </div>)}
        </div>}
      </div>}
    </CollapsibleContent>
    <AlertDialog open={confirm && !!preview} onOpenChange={(open) => { if (!open && !busy) setConfirm(false); }}>
      {preview && <AlertDialogContent aria-busy={busy}>
        <AlertDialogHeader>
          <AlertDialogTitle>Purge from {preview.workspace}?</AlertDialogTitle>
          <AlertDialogDescription className="wrap-anywhere">
            Remove indexed copies of {preview.target} ({preview.chunks} chunks) and exclude re-ingestion until restored. This does not remove backups or original files.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-1.5">
          <p className="text-xs text-muted-foreground">
            Type <code className="rounded bg-muted px-1 font-mono">{preview.workspace}</code> to confirm:
          </p>
          <Input aria-label={`Type ${preview.workspace} to confirm`} value={typed} onChange={e => setTyped(e.target.value)} autoFocus disabled={busy} />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
          <Button variant="destructive" disabled={busy || typed !== preview.workspace} onClick={applyPurge}>
            {busy && <Spinner />} Purge indexed copies
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>}
    </AlertDialog>
  </Collapsible>;
}
