"use client";

import { useEffect, useState } from "react";
import { Check, ChevronDown, FolderOpen, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
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
import { ConfirmDialog } from "@/components/confirm-dialog";
import { MigrateDialog } from "@/components/migrate-dialog";
import { Modal } from "@/components/modal";
import { WorkspaceRoots } from "@/components/workspace-roots";

export type WorkspacesState = { active: string; workspaces: string[] };

export const WORKSPACE_NAME_HINT = "Lowercase letters, digits and hyphens, up to 40 characters.";

/** Make `name` the active workspace. Every page reads workspace-scoped data
 * and the server resets its caches on switch, so the dashboard reloads. */
export async function activateWorkspace(name: string) {
  await switchWorkspace(name);
  window.location.reload();
}

type PendingAction =
  | { kind: "delete"; name: string }
  | { kind: "wipe"; name: string }
  | null;

/** Create, switch, edit (roots), wipe and delete workspaces. */
export function WorkspaceManager() {
  const [state, setState] = useState<WorkspacesState>({ active: "", workspaces: [] });
  const [scopes, setScopes] = useState<Record<string, WorkspaceScope>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState<PendingAction>(null);
  const [busy, setBusy] = useState(false);
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
    try {
      await createWorkspace(name);
      setNewName("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function confirmAction() {
    if (!pending) return;
    const run = pending.kind === "delete"
      ? (cb: (ev: Record<string, unknown>) => void) => deleteWorkspace(pending.name, cb)
      : (cb: (ev: Record<string, unknown>) => void) => wipeWorkspace(pending.name, false, cb);
    setBusy(true);
    setLog([]);
    run((ev) => {
      if (ev.type === "output" && typeof ev.text === "string") {
        setLog((l) => [...l, ev.text as string]);
      } else if (ev.type === "done") {
        setBusy(false);
        setPending(null);
        refresh().catch((e) => setError(String(e)));
      } else if (ev.type === "error") {
        setLog((l) => [...l, `ERROR: ${ev.error}`]);
        setBusy(false);
      }
    });
  }

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
            <Button type="submit" disabled={!newName.trim()}>
              <Plus aria-hidden /> Create
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
                        <span className="inline-flex items-center gap-1 rounded-full border border-foreground/15 bg-muted px-2 py-0.5 text-[11px] font-medium">
                          <Check className="size-3" aria-hidden /> Active
                        </span>
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
                  <div className="flex gap-1.5">
                    {!isActive && (
                      <Button variant="outline" size="sm" onClick={() => activateWorkspace(ws)}>
                        Switch
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
                      onClick={() => setPending({ kind: "wipe", name: ws })}
                    >
                      Wipe
                    </Button>
                    <Button
                      variant="destructive"
                      size="sm"
                      aria-label={`Delete ${ws}`}
                      onClick={() => setPending({ kind: "delete", name: ws })}
                      disabled={isDefault || isActive}
                      title={isDefault ? "The default workspace cannot be deleted" : isActive ? "Switch to another workspace first" : undefined}
                    >
                      Delete
                    </Button>
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

      <ConfirmDialog
        open={pending?.kind === "delete"}
        title={`Delete workspace '${pending?.kind === "delete" ? pending.name : ""}'?`}
        description="This permanently removes the workspace's Qdrant collection, SQLite graph, and WAL. Cannot be undone."
        confirmText={pending?.kind === "delete" ? pending.name : undefined}
        confirmLabel="Delete"
        destructive
        busy={busy}
        onCancel={() => !busy && setPending(null)}
        onConfirm={confirmAction}
      >
        {busy && log.length > 0 && (
          <pre className="max-h-32 overflow-auto bg-muted rounded p-2 text-xs font-mono whitespace-pre-wrap">
            {log.join("")}
          </pre>
        )}
      </ConfirmDialog>

      <ConfirmDialog
        open={pending?.kind === "wipe"}
        title={`Wipe workspace '${pending?.kind === "wipe" ? pending.name : ""}'?`}
        description="Deletes all vectors + facts in this workspace. The workspace itself stays (can be re-used after). Cannot be undone."
        confirmText="wipe"
        confirmLabel="Wipe"
        destructive
        busy={busy}
        onCancel={() => !busy && setPending(null)}
        onConfirm={confirmAction}
      >
        {busy && log.length > 0 && (
          <pre className="max-h-32 overflow-auto bg-muted rounded p-2 text-xs font-mono whitespace-pre-wrap">
            {log.join("")}
          </pre>
        )}
      </ConfirmDialog>

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

/** The workspace manager as a modal, opened from the top bar. */
export function WorkspaceDialog({ onClose }: { onClose: () => void }) {
  return (
    <Modal label="Manage workspaces" onClose={onClose} className="max-w-2xl p-0">
      <div className="flex items-start justify-between gap-4 border-b border-border px-6 py-4">
        <div>
          <h2 className="text-base font-semibold">Workspaces</h2>
          <p className="text-sm text-muted-foreground">Isolated memory spaces. Switching reloads the dashboard.</p>
        </div>
        <Button variant="ghost" size="sm" onClick={onClose}>Done</Button>
      </div>
      <div className="max-h-[70dvh] overflow-y-auto px-6 py-5">
        <WorkspaceManager />
      </div>
    </Modal>
  );
}
