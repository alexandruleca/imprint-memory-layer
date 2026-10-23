"use client";

import { useEffect, useState } from "react";
import { Popover } from "@base-ui/react/popover";
import { Check, ChevronsUpDown, Layers, Plus, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/loaders";
import { createWorkspace, getWorkspaces } from "@/lib/api";
import {
  activateWorkspace,
  WorkspaceDialog,
  WORKSPACE_NAME_HINT,
  type WorkspacesState,
} from "@/components/workspace-manager";

const ITEM =
  "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm outline-none transition-colors hover:bg-muted focus-visible:bg-muted disabled:pointer-events-none disabled:opacity-50";

/** Top-bar control: shows the active workspace, switches to another one,
 * creates one, and opens the full workspace manager. */
export function WorkspaceSwitcher() {
  const [state, setState] = useState<WorkspacesState | null>(null);
  const [open, setOpen] = useState(false);
  const [managing, setManaging] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [switching, setSwitching] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getWorkspaces().then(setState).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  async function switchTo(workspace: string) {
    if (workspace === state?.active) return setOpen(false);
    setSwitching(workspace);
    setError("");
    try {
      await activateWorkspace(workspace);
    } catch (e) {
      setSwitching(null);
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function create() {
    const workspace = name.trim();
    if (!workspace) return;
    setError("");
    try {
      await createWorkspace(workspace);
      await switchTo(workspace);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function onOpenChange(next: boolean) {
    setOpen(next);
    // Other views create and delete workspaces too; list what exists now.
    if (next) getWorkspaces().then(setState).catch(() => {});
    else { setCreating(false); setName(""); setError(""); }
  }

  const active = state?.active ?? "";
  return (
    <>
      <Popover.Root open={open} onOpenChange={onOpenChange}>
        <Popover.Trigger
          aria-label={active ? `Workspace: ${active}` : "Workspace"}
          className="inline-flex h-8 max-w-64 items-center gap-2 rounded-lg border border-border bg-card px-2.5 text-sm shadow-xs outline-none transition-colors hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 data-[popup-open]:bg-muted/60"
          style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
        >
          <Layers className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <span className="hidden text-muted-foreground md:inline">Workspace</span>
          <span className="truncate font-medium">{active || "…"}</span>
          <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Positioner side="bottom" align="end" sideOffset={6} className="z-50">
            <Popover.Popup className="w-72 rounded-xl border border-border bg-popover text-popover-foreground shadow-2xl outline-none transition-[opacity,transform] data-[ending-style]:scale-95 data-[ending-style]:opacity-0 data-[starting-style]:scale-95 data-[starting-style]:opacity-0">
              <Popover.Title className="px-3 pb-1 pt-3 text-xs font-medium text-muted-foreground">
                Switch workspace
              </Popover.Title>
              <ul className="max-h-64 overflow-y-auto p-1">
                {(state?.workspaces ?? []).map((ws) => (
                  <li key={ws}>
                    <button
                      type="button"
                      className={ITEM}
                      aria-current={ws === active ? "true" : undefined}
                      disabled={switching !== null}
                      onClick={() => switchTo(ws)}
                    >
                      {switching === ws ? (
                        <Spinner className="size-4" />
                      ) : (
                        <Check className={`size-4 ${ws === active ? "" : "invisible"}`} aria-hidden />
                      )}
                      <span className="truncate">{ws}</span>
                    </button>
                  </li>
                ))}
              </ul>
              <div className="border-t border-border p-1">
                {creating ? (
                  <form className="space-y-1.5 p-1.5" onSubmit={(e) => { e.preventDefault(); create(); }}>
                    <div className="flex gap-1.5">
                      <Input
                        autoFocus
                        aria-label="New workspace name"
                        placeholder="new-workspace"
                        value={name}
                        onChange={(e) => { setName(e.target.value); setError(""); }}
                        className="h-8 flex-1 text-sm"
                      />
                      <Button type="submit" size="sm" className="h-8" disabled={!name.trim() || switching !== null}>
                        Create
                      </Button>
                    </div>
                    <p className="text-[11px] text-muted-foreground">{WORKSPACE_NAME_HINT}</p>
                  </form>
                ) : (
                  <button type="button" className={ITEM} onClick={() => setCreating(true)}>
                    <Plus className="size-4" aria-hidden /> New workspace
                  </button>
                )}
                <button
                  type="button"
                  className={ITEM}
                  onClick={() => { onOpenChange(false); setManaging(true); }}
                >
                  <Settings2 className="size-4" aria-hidden /> Manage workspaces…
                </button>
              </div>
              {error && (
                <p role="alert" className="border-t border-border px-3 py-2 text-xs text-destructive">{error}</p>
              )}
            </Popover.Popup>
          </Popover.Positioner>
        </Popover.Portal>
      </Popover.Root>
      {managing && (
        <WorkspaceDialog
          onClose={() => setManaging(false)}
        />
      )}
    </>
  );
}
