"use client";

import { useState } from "react";
import { FolderOpen, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { PathBrowserDialog } from "@/components/path-browser-dialog";
import { changeWorkspaceRoot, getWorkspaceScope, type WorkspaceScope } from "@/lib/api";

/** The directories a workspace may ingest from. Adding a root another
 * workspace already covers needs an explicit confirmation. */
export function WorkspaceRoots({ scope, onChange }: {
  scope: WorkspaceScope; onChange: (scope: WorkspaceScope) => void;
}) {
  const [root, setRoot] = useState("");
  const [overlap, setOverlap] = useState("");
  const [browsing, setBrowsing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function change(action: "add" | "remove", path: string, allow_overlap = false) {
    setBusy(true); setError("");
    try {
      await changeWorkspaceRoot({ workspace: scope.workspace, path, action, allow_overlap });
      onChange(await getWorkspaceScope(scope.workspace));
      setRoot(""); setOverlap("");
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      if (action === "add" && message.includes("Root overlaps")) setOverlap(message);
      else setError(message);
    } finally { setBusy(false); }
  }

  return <div className="space-y-3 text-sm">
    <p className="text-muted-foreground">Ingestion stays within these roots. The first ingest binds a root; adding shared roots requires confirmation.</p>
    {error && <p role="alert" className="text-destructive">{error}</p>}
    {scope.roots.length > 0 ? <ul className="divide-y divide-border rounded-lg border border-border">
      {scope.roots.map(path => <li key={path} className="flex items-center gap-2 px-3 py-1.5">
        <FolderOpen className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <code className="flex-1 break-all text-xs">{path}</code>
        <Button size="icon-xs" variant="ghost" disabled={busy} onClick={() => change("remove", path)}
          aria-label={`Remove root ${path}`} title="Remove root"><X /></Button>
      </li>)}
    </ul> : <p className="text-muted-foreground">No roots approved yet.</p>}
    <form className="flex flex-wrap gap-2" onSubmit={e => { e.preventDefault(); if (root.trim()) change("add", root.trim()); }}>
      <Input aria-label="Workspace root directory" placeholder="Absolute directory path" value={root} disabled={busy}
        className="h-8 min-w-48 flex-1 text-sm" onChange={e => { setRoot(e.target.value); setOverlap(""); }} />
      <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => setBrowsing(true)}>Pick folder</Button>
      <Button type="submit" size="sm" disabled={busy || !root.trim()}>Add root</Button>
    </form>
    <PathBrowserDialog open={browsing} mode="dir" title="Pick a root folder" initialPath={root}
      onCancel={() => setBrowsing(false)} onSelect={path => { setRoot(path); setOverlap(""); setBrowsing(false); }} />
    <ConfirmDialog open={!!overlap} title="Share this root across workspaces?"
      description={`${root}\n${overlap}\nConfirming allows these workspaces to ingest the same files. Existing memories stay in their current workspace.`}
      confirmLabel="Allow overlap and add root" busy={busy} onCancel={() => setOverlap("")} onConfirm={() => change("add", root.trim(), true)} />
  </div>;
}
