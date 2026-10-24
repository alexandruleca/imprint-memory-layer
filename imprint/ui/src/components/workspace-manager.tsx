"use client";

import { useEffect, useState } from "react";
import { Check, ChevronDown, FolderOpen, Plus } from "lucide-react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  createWorkspace,
  deleteWorkspace,
  getWorkspaces,
  getWorkspaceScope,
  switchWorkspace,
  wipeWorkspace,
  type WorkspaceScope,
} from "@/lib/api";
import { Hint } from "@/components/hint";
import { Spinner } from "@/components/loaders";
import { MigrateDialog } from "@/components/migrate-dialog";
import { WorkspaceRoots } from "@/components/workspace-roots";

export type WorkspacesState = { active: string; workspaces: string[] };

export const WORKSPACE_NAME_HINT = "Lowercase letters, digits and hyphens, up to 40 characters.";

/** Make `name` the active workspace. Every page reads workspace-scoped data
 * and the server resets its caches on switch, so the dashboard reloads. */
export async function activateWorkspace(name: string) {
  await switchWorkspace(name);
  window.location.reload();
}

type PendingAction = { kind: "delete" | "wipe"; name: string };

/** What each destructive action asks before it runs; the user types `confirmText` to enable it. */
const CONFIRM = {
  delete: {
    title: (name: string) => `Delete workspace '${name}'?`,
    description: "This permanently removes the workspace's Qdrant collection, SQLite graph, and WAL. Cannot be undone.",
    confirmText: (name: string) => name,
    label: "Delete",
    done: (name: string) => `Deleted workspace '${name}'`,
  },
  wipe: {
    title: (name: string) => `Wipe workspace '${name}'?`,
    description: "Deletes all vectors + facts in this workspace. The workspace itself stays (can be re-used after). Cannot be undone.",
    confirmText: () => "wipe",
    label: "Wipe",
    done: (name: string) => `Wiped workspace '${name}'`,
  },
};

/** Create, switch, edit (roots), wipe and delete workspaces. */
export function WorkspaceManager() {
  const [state, setState] = useState<WorkspacesState>({ active: "", workspaces: [] });
  const [scopes, setScopes] = useState<Record<string, WorkspaceScope>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [switching, setSwitching] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [migrateOpen, setMigrateOpen] = useState(false);

  async function refresh() {
    const ws = await getWorkspaces();
    setState(ws);
    // Roots are a per-row summary; a workspace whose scope fails to load
    // simply shows none.
    const loaded = await Promise.all(ws.workspaces.map((name) =>
      getWorkspaceScope(name).then((scope) => [name, scope] as const, () => null)));
    setScopes(Object.fromEntries(loaded.filter((entry) => entry !== null)));
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial load
    refresh().catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  async function doCreate() {
    const name = newName.trim();
    if (!name) return;
    setError("");
    setCreating(true);
    try {
      await createWorkspace(name);
      setNewName("");
      toast.success(`Created workspace '${name}'`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  }

  async function switchTo(name: string) {
    setSwitching(name);
    try {
      await activateWorkspace(name);
    } catch (e) {
      setSwitching(null);
      toast.error(`Could not switch to ${name}`, { description: e instanceof Error ? e.message : String(e) });
    }
  }

  function ask(action: PendingAction) {
    setTyped("");
    setLog([]);
    setPending(action);
  }

  function confirmAction() {
    if (!pending) return;
    const { kind, name } = pending;
    const run = kind === "delete"
      ? (cb: (ev: Record<string, unknown>) => void) => deleteWorkspace(name, cb)
      : (cb: (ev: Record<string, unknown>) => void) => wipeWorkspace(name, false, cb);
    setBusy(true);
    setLog([]);
    run((ev) => {
      if (ev.type === "output" && typeof ev.text === "string") {
        setLog((l) => [...l, ev.text as string]);
      } else if (ev.type === "done") {
        setBusy(false);
        // The log stays open when the command fails, so its output explains why.
        if (ev.status === "failed" || ev.status === "cancelled") {
          setLog((l) => [...l, `\nThe command ${ev.status} (exit code ${ev.exit_code ?? "unknown"}).`]);
          return;
        }
        setPending(null);
        toast.success(CONFIRM[kind].done(name));
        refresh().catch((e) => setError(String(e)));
      } else if (ev.type === "error") {
        setLog((l) => [...l, `ERROR: ${ev.error}`]);
        setBusy(false);
      }
    });
  }

  const confirm = pending && CONFIRM[pending.kind];
  const confirmText = pending && confirm ? confirm.confirmText(pending.name) : "";

  return (
    <>
      <div className="space-y-4">
        <form
          className="space-y-1.5"
          onSubmit={(e) => { e.preventDefault(); doCreate(); }}
        >
          <div className="flex gap-2">
            <Input
              aria-label="New workspace name"
              value={newName}
              onChange={(e) => { setNewName(e.target.value); setError(""); }}
              placeholder="new-workspace"
              className="h-9 flex-1 text-sm"
            />
            <Button type="submit" disabled={!newName.trim() || creating}>
              {creating ? <Spinner /> : <Plus aria-hidden />} Create
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">{WORKSPACE_NAME_HINT}</p>
          {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
        </form>

        <ul aria-label="Workspaces" className="divide-y divide-border rounded-xl border border-border">
          {state.workspaces.map((ws) => {
            const isActive = ws === state.active;
            const isDefault = ws === "default";
            const scope = scopes[ws];
            const roots = scope?.roots ?? [];
            const isEditing = editing === ws;
            return (
              <li key={ws} className="px-4 py-3">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <div className="min-w-0 flex-1 basis-56">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">{ws}</span>
                      {isActive && (
                        <Badge variant="outline" className="bg-muted">
                          <Check aria-hidden /> Active
                        </Badge>
                      )}
                    </div>
                    <p className="mt-0.5 flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
                      <FolderOpen className="size-3 shrink-0" aria-hidden />
                      <span className="truncate">
                        {roots.length === 0
                          ? "No roots yet — the first ingest adds one"
                          : `${roots[0]}${roots.length > 1 ? ` +${roots.length - 1} more` : ""}`}
                      </span>
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {!isActive && (
                      <Button variant="outline" size="sm" disabled={switching !== null} onClick={() => switchTo(ws)}>
                        {switching === ws && <Spinner />} Switch
                      </Button>
                    )}
                    <Button
                      variant="outline"
                      size="sm"
                      aria-expanded={isEditing}
                      aria-label={`Edit ${ws}`}
                      disabled={!scope}
                      onClick={() => setEditing(isEditing ? null : ws)}
                    >
                      Edit
                      <ChevronDown className={`transition-transform ${isEditing ? "rotate-180" : ""}`} aria-hidden />
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      aria-label={`Wipe ${ws}`}
                      onClick={() => ask({ kind: "wipe", name: ws })}
                    >
                      Wipe
                    </Button>
                    {isDefault || isActive ? (
                      // A disabled button takes no pointer events; the hint sits on a wrapper.
                      <Hint label={isDefault ? "The default workspace cannot be deleted" : "Switch to another workspace first"}>
                        <span className="inline-flex">
                          <Button variant="destructive" size="sm" aria-label={`Delete ${ws}`} disabled>Delete</Button>
                        </span>
                      </Hint>
                    ) : (
                      <Button variant="destructive" size="sm" aria-label={`Delete ${ws}`} onClick={() => ask({ kind: "delete", name: ws })}>
                        Delete
                      </Button>
                    )}
                  </div>
                </div>
                {isEditing && scope && (
                  <div className="mt-3 rounded-lg bg-muted/40 p-3">
                    <WorkspaceRoots
                      scope={scope}
                      onChange={(next) => setScopes((s) => ({ ...s, [ws]: next }))}
                    />
                  </div>
                )}
              </li>
            );
          })}
        </ul>

        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            Each workspace keeps its own memories, graph and roots.
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setMigrateOpen(true)}
            disabled={state.workspaces.length < 2}
          >
            Copy / move memories…
          </Button>
        </div>
      </div>

      <AlertDialog open={pending !== null} onOpenChange={(open) => { if (!open && !busy) setPending(null); }}>
        {pending && confirm && (
          <AlertDialogContent aria-busy={busy}>
            <AlertDialogHeader>
              <AlertDialogTitle>{confirm.title(pending.name)}</AlertDialogTitle>
              <AlertDialogDescription>{confirm.description}</AlertDialogDescription>
            </AlertDialogHeader>
            <div className="space-y-1.5">
              <p className="text-xs text-muted-foreground">
                Type <code className="rounded bg-muted px-1 font-mono">{confirmText}</code> to confirm:
              </p>
              <Input
                aria-label={`Type ${confirmText} to confirm`}
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                autoFocus
                disabled={busy}
              />
            </div>
            {log.length > 0 && (
              <pre className="max-h-32 overflow-auto rounded bg-muted p-2 font-mono text-xs whitespace-pre-wrap">
                {log.join("")}
              </pre>
            )}
            <AlertDialogFooter>
              <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
              <Button variant="destructive" onClick={confirmAction} disabled={busy || typed !== confirmText}>
                {busy && <Spinner />} {confirm.label}
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        )}
      </AlertDialog>

      <MigrateDialog
        open={migrateOpen}
        workspaces={state.workspaces}
        activeWorkspace={state.active}
        onClose={() => setMigrateOpen(false)}
        onDone={() => { refresh().catch((e) => setError(String(e))); }}
      />
    </>
  );
}

/** The workspace manager as a dialog, opened from the top bar. */
export function WorkspaceDialog({ onClose }: { onClose: () => void }) {
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent showCloseButton={false} className="gap-0 p-0 sm:max-w-2xl">
        <div className="flex items-start justify-between gap-4 border-b border-border px-6 py-4">
          <DialogHeader className="gap-1">
            <DialogTitle>Manage workspaces</DialogTitle>
            <DialogDescription>Isolated memory spaces. Switching reloads the dashboard.</DialogDescription>
          </DialogHeader>
          <Button variant="ghost" size="sm" onClick={onClose}>Done</Button>
        </div>
        <div className="max-h-[70dvh] overflow-y-auto px-6 py-5">
          <WorkspaceManager />
        </div>
      </DialogContent>
    </Dialog>
  );
}
