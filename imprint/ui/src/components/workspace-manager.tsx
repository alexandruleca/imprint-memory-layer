"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  createWorkspace,
  deleteWorkspace,
  getWorkspaces,
  switchWorkspace,
  wipeWorkspace,
} from "@/lib/api";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { MigrateDialog } from "@/components/migrate-dialog";

type WorkspacesState = { active: string; workspaces: string[] };

type PendingAction =
  | { kind: "delete"; name: string }
  | { kind: "wipe"; name: string }
  | null;

export function WorkspaceManager() {
  const [state, setState] = useState<WorkspacesState>({
    active: "",
    workspaces: [],
  });
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [createError, setCreateError] = useState("");
  const [pending, setPending] = useState<PendingAction>(null);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const [migrateOpen, setMigrateOpen] = useState(false);

  async function refresh() {
    const ws = await getWorkspaces();
    setState(ws);
  }

  useEffect(() => {
    refresh().catch(() => {});
  }, []);

  async function doSwitch(name: string) {
    if (name === state.active) return;
    await switchWorkspace(name);
    // Full reload — all pages pull workspace-scoped data from the API,
    // and the server resets its caches on switch.
    window.location.reload();
  }

  async function doCreate() {
    setCreateError("");
    const trimmed = newName.trim();
    if (!trimmed) return;
    try {
      const r = (await createWorkspace(trimmed)) as unknown as {
        error?: string;
      };
      if (r.error) {
        setCreateError(r.error);
        return;
      }
      setNewName("");
      setCreating(false);
      await refresh();
    } catch (e) {
      setCreateError(String(e));
    }
  }

  function runStream(
    action: (cb: (ev: Record<string, unknown>) => void) => void,
    onDone: () => void,
  ) {
    setBusy(true);
    setLog([]);
    action((ev) => {
      if (ev.type === "output" && typeof ev.text === "string") {
        setLog((l) => [...l, ev.text as string]);
      } else if (ev.type === "done") {
        setBusy(false);
        onDone();
      } else if (ev.type === "error") {
        setLog((l) => [...l, `ERROR: ${ev.error}`]);
        setBusy(false);
      }
    });
  }

  function confirmAction() {
    if (!pending) return;
    if (pending.kind === "delete") {
      runStream(
        (cb) => deleteWorkspace(pending.name, cb),
        async () => {
          await refresh();
          setPending(null);
        },
      );
    } else if (pending.kind === "wipe") {
      runStream(
        (cb) => wipeWorkspace(pending.name, false, cb),
        async () => {
          await refresh();
          setPending(null);
        },
      );
    }
  }

  return (
    <>
      <div className="space-y-2">
        <div className="rounded border border-border divide-y divide-border">
          {state.workspaces.map((ws) => {
            const isActive = ws === state.active;
            const isDefault = ws === "default";
            return (
              <div
                key={ws}
                className="flex items-center justify-between px-3 py-2 gap-2"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-mono text-sm truncate">{ws}</span>
                  {isActive && (
                    <span className="text-xs px-1.5 py-0.5 rounded bg-primary text-primary-foreground">
                      active
                    </span>
                  )}
                </div>
                <div className="flex gap-1.5">
                  <Button
                    variant="outline"
                    size="xs"
                    onClick={() => doSwitch(ws)}
                    disabled={isActive}
                  >
                    Switch
                  </Button>
                  <Button
                    variant="outline"
                    size="xs"
                    onClick={() => setPending({ kind: "wipe", name: ws })}
                  >
                    Wipe
                  </Button>
                  <Button
                    variant="destructive"
                    size="xs"
                    onClick={() => setPending({ kind: "delete", name: ws })}
                    disabled={isDefault || isActive}
                    title={
                      isDefault
                        ? "cannot delete default"
                        : isActive
                          ? "switch away first"
                          : undefined
                    }
                  >
                    Delete
                  </Button>
                </div>
              </div>
            );
          })}
        </div>

        <div className="flex items-center gap-2">
          {creating ? (
            <>
              <Input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="workspace-name"
                className="h-8 text-sm max-w-xs"
                autoFocus
                onKeyDown={(e) => e.key === "Enter" && doCreate()}
              />
              <Button size="xs" onClick={doCreate}>
                Create
              </Button>
              <Button
                size="xs"
                variant="ghost"
                onClick={() => {
                  setCreating(false);
                  setNewName("");
                  setCreateError("");
                }}
              >
                Cancel
              </Button>
              {createError && (
                <span className="text-xs text-destructive">{createError}</span>
              )}
            </>
          ) : (
            <>
              <Button
                size="xs"
                variant="outline"
                onClick={() => setCreating(true)}
              >
                + New workspace
              </Button>
              <Button
                size="xs"
                variant="outline"
                onClick={() => setMigrateOpen(true)}
                disabled={state.workspaces.length < 2}
              >
                Migrate…
              </Button>
            </>
          )}
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
        onDone={() => {
          refresh().catch(() => {});
        }}
      />
    </>
  );
}
