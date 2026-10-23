"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { changeWorkspaceRoot, getWorkspaceScope, getWorkspaces, purgeSources, type WorkspaceScope, type PurgePreview } from "@/lib/api";

export function WorkspaceSources({ selectedSource, onPurged }: { selectedSource: string | null; onPurged: () => Promise<void> }) {
  const [scope, setScope] = useState<WorkspaceScope | null>(null);
  const [root, setRoot] = useState("");
  const [overlap, setOverlap] = useState("");
  const [target, setTarget] = useState("");
  const [kind, setKind] = useState<"source" | "path">("source");
  const [recursive, setRecursive] = useState(false);
  const [preview, setPreview] = useState<PurgePreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirm, setConfirm] = useState(false);

  useEffect(() => {
    getWorkspaces().then(ws => getWorkspaceScope(ws.active)).then(setScope).catch(e => setError(String(e)));
  }, []);

  async function changeRoot(action: "add" | "remove" | "restore", path: string, allow_overlap = false, restoreKind?: "path" | "source") {
    if (!scope) return;
    setBusy(true); setError("");
    try {
      await changeWorkspaceRoot({ workspace: scope.workspace, path, action, allow_overlap, kind: restoreKind });
      setScope(await getWorkspaceScope(scope.workspace));
      setRoot(""); setOverlap("");
    } catch (e) {
      const message = String(e);
      if (action === "add" && message.includes("Root overlaps")) setOverlap(message);
      else setError(message);
    } finally { setBusy(false); }
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
      setNotice(`Removed ${result.chunks} chunks from ${result.sources} sources in ${result.workspace}. Re-ingestion is excluded until restored.`);
      setPreview(null); setConfirm(false);
      setScope(await getWorkspaceScope(result.workspace));
      await onPurged();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  return <details className="rounded-lg border p-4 space-y-3">
    <summary className="cursor-pointer font-medium">Workspace roots and cleanup{scope ? ` · ${scope.workspace}` : ""}</summary>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {scope && <div className="space-y-4 pt-3 text-sm">
      <p>Ingestion stays within these roots. The first ingest binds a root; adding shared roots requires confirmation.</p>
      <ul className="space-y-1">{scope.roots.map(path => <li key={path} className="flex gap-2 items-center">
        <code className="break-all flex-1">{path}</code><Button size="sm" variant="ghost" disabled={busy} onClick={() => changeRoot("remove", path)}>Remove root</Button>
      </li>)}</ul>
      {!scope.roots.length && <p className="text-muted-foreground">No roots approved yet.</p>}
      <div className="flex gap-2 flex-wrap">
        <Input aria-label="Workspace root directory" placeholder="Absolute directory path" value={root} disabled={busy} className="flex-1 min-w-48" onChange={e => { setRoot(e.target.value); setOverlap(""); }} />
        <Button disabled={busy || !root.trim()} onClick={() => changeRoot("add", root)}>Add root</Button>
      </div>
      <div className="border-t pt-4 space-y-3">
        <h3 className="font-medium">Purge indexed files or directories</h3>
        <p className="text-muted-foreground">Preview matching chunks in {scope.workspace}. Purge removes indexed copies and blocks re-ingestion. Original files and other workspaces stay intact.</p>
        <div className="flex gap-2 flex-wrap items-center">
          <label>Match <select aria-label="Cleanup match type" className="bg-background border rounded p-2" value={kind} disabled={busy} onChange={e => { setKind(e.target.value as "path" | "source"); setPreview(null); }}>
            <option value="source">Source key</option><option value="path">Absolute file path</option>
          </select></label>
          <Button variant="outline" size="sm" disabled={busy || !selectedSource} onClick={() => { setTarget(selectedSource || ""); setKind("source"); setRecursive(false); setPreview(null); }}>Use selected source</Button>
        </div>
        <Input aria-label="Cleanup target" placeholder={kind === "source" ? "project/directory or project/file.py" : "Absolute file or directory path"} value={target} disabled={busy} onChange={e => { setTarget(e.target.value); setPreview(null); }} />
        <label className="flex gap-2 items-center"><input type="checkbox" checked={recursive} disabled={busy} onChange={e => { setRecursive(e.target.checked); setPreview(null); }} />Include directory descendants</label>
        <Button variant="outline" disabled={busy || !target.trim()} onClick={previewPurge}>Preview cleanup</Button>
        {preview && <div className="space-y-2">
          <p role="status">{preview.chunks} chunks in {preview.sources} sources · workspace {preview.workspace}</p>
          <p className="font-mono break-all">{preview.target}{preview.recursive ? "/**" : ""}</p>
          <ul>{preview.sample.map(item => <li key={`${item.project}/${item.source}`} className="font-mono text-xs break-all">{item.source}</li>)}</ul>
          <Button variant="destructive" disabled={busy} onClick={() => setConfirm(true)}>Purge from {preview.workspace}…</Button>
        </div>}
      </div>
      {scope.exclusions.length > 0 && <div className="border-t pt-3 space-y-2"><h3 className="font-medium">Excluded after purge</h3>
        {scope.exclusions.map(item => <div key={`${item.kind}/${item.path}`} className="flex gap-2 items-center">
          <code className="break-all flex-1">{item.path}{item.recursive ? "/**" : ""}</code>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => changeRoot("restore", item.path, false, item.kind)}>Allow re-ingestion</Button>
        </div>)}
      </div>}
    </div>}
    <ConfirmDialog open={!!overlap} title="Share this root across workspaces?" description={`${root}\n${overlap}\nConfirming allows these workspaces to ingest the same files. Existing memories stay in their current workspace.`} confirmLabel="Allow overlap and add root" busy={busy} onCancel={() => setOverlap("")} onConfirm={() => changeRoot("add", root, true)} />
    <ConfirmDialog open={confirm && !!preview} title={`Purge from ${preview?.workspace}?`} description={`Remove indexed copies of ${preview?.target} (${preview?.chunks} chunks) and exclude re-ingestion until restored. This does not remove backups or original files.`} confirmText={preview?.workspace} confirmLabel="Purge indexed copies" destructive busy={busy} onCancel={() => setConfirm(false)} onConfirm={applyPurge} />
  </details>;
}
