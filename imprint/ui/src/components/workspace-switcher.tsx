"use client";

import { useEffect, useState } from "react";
import { ChevronsUpDown, Layers, Plus, Settings2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/loaders";
import { createWorkspace, getWorkspaces } from "@/lib/api";
import {
  activateWorkspace,
  WorkspaceDialog,
  WORKSPACE_NAME_HINT,
  type WorkspacesState,
} from "@/components/workspace-manager";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Top-bar control: shows the active workspace, switches to another one,
 * creates one, and opens the full workspace manager. */
export function WorkspaceSwitcher() {
  const [state, setState] = useState<WorkspacesState | null>(null);
  const [managing, setManaging] = useState(false);
  const [creating, setCreating] = useState(false);
  const [switching, setSwitching] = useState<string | null>(null);

  useEffect(() => {
    getWorkspaces().then(setState).catch((e) => toast.error("Workspaces could not load", { description: message(e) }));
  }, []);

  async function switchTo(workspace: string) {
    if (workspace === state?.active) return;
    setSwitching(workspace);
    try {
      await activateWorkspace(workspace);
    } catch (e) {
      setSwitching(null);
      toast.error(`Could not switch to ${workspace}`, { description: message(e) });
    }
  }

  const active = state?.active ?? "";
  return (
    <>
      <DropdownMenu
        onOpenChange={(open) => {
          // Other views create and delete workspaces too; list what exists now.
          if (open) getWorkspaces().then(setState).catch(() => {});
        }}
      >
        <DropdownMenuTrigger
          render={<Button variant="outline" className="max-w-64 bg-card shadow-xs" />}
          aria-label={active ? `Workspace: ${active}` : "Workspace"}
        >
          <Layers className="text-muted-foreground" aria-hidden />
          <span className="hidden font-normal text-muted-foreground md:inline">Workspace</span>
          <span className="truncate">{active || "…"}</span>
          <ChevronsUpDown className="size-3.5 text-muted-foreground" aria-hidden />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-72">
          <DropdownMenuGroup>
            <DropdownMenuLabel>Switch workspace</DropdownMenuLabel>
            <DropdownMenuRadioGroup value={active} onValueChange={(workspace: string) => switchTo(workspace)}>
              {(state?.workspaces ?? []).map((ws) => (
                <DropdownMenuRadioItem key={ws} value={ws} disabled={switching !== null}>
                  <span className="truncate">{ws}</span>
                  {switching === ws && <Spinner className="ml-auto" />}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => setCreating(true)}>
            <Plus aria-hidden /> New workspace…
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => setManaging(true)}>
            <Settings2 aria-hidden /> Manage workspaces…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <CreateWorkspaceDialog open={creating} onOpenChange={setCreating} onCreated={switchTo} />
      {managing && <WorkspaceDialog onClose={() => setManaging(false)} />}
    </>
  );
}

/** Names a new workspace, then switches to it. */
function CreateWorkspaceDialog({ open, onOpenChange, onCreated }: {
  open: boolean; onOpenChange: (open: boolean) => void; onCreated: (workspace: string) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function create() {
    const workspace = name.trim();
    if (!workspace) return;
    setBusy(true);
    setError("");
    try {
      await createWorkspace(workspace);
      await onCreated(workspace);
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return;
        onOpenChange(next);
        if (!next) { setName(""); setError(""); }
      }}
    >
      <DialogContent>
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); create(); }}>
          <DialogHeader>
            <DialogTitle>New workspace</DialogTitle>
            <DialogDescription>An isolated memory space. Imprint switches to it once it exists.</DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Input
              autoFocus
              aria-label="New workspace name"
              placeholder="new-workspace"
              value={name}
              disabled={busy}
              onChange={(e) => { setName(e.target.value); setError(""); }}
            />
            <p className="text-xs text-muted-foreground">{WORKSPACE_NAME_HINT}</p>
            {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
          </div>
          <DialogFooter>
            <Button type="submit" disabled={!name.trim() || busy}>
              {busy && <Spinner />} Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
