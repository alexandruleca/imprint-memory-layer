"use client";

import { useState, useSyncExternalStore } from "react";
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
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/loaders";
import { changeWorkspaceRoot } from "@/lib/api";
import {
  currentRootApproval,
  settleRootApproval,
  subscribeRootApproval,
  type Pending,
} from "@/lib/root-approval";

/** Offers to approve the root an ingest or refresh needs, wherever the job
 * was started (enqueueCommand waits for the answer). Approval adds the root
 * like the workspace roots editor does, confirming any sharing (ADR 0015). */
export function RootApprovalDialog() {
  const request = useSyncExternalStore(subscribeRootApproval, currentRootApproval, () => null);
  return request ? <Approval key={request.id} request={request} /> : null;
}

function Approval({ request }: { request: Pending }) {
  const { id, workspace, root, overlaps } = request;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function approve() {
    setBusy(true);
    setError("");
    try {
      await changeWorkspaceRoot({ workspace, path: root, action: "add", allow_overlap: overlaps.length > 0 });
      toast.success(`Approved a root of ${workspace}`, { description: root });
      settleRootApproval(id, true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <AlertDialog open onOpenChange={(open) => { if (!open && !busy) settleRootApproval(id, false); }}>
      <AlertDialogContent aria-busy={busy}>
        <AlertDialogHeader>
          <AlertDialogTitle className="wrap-anywhere">
            Approve {root} as a root of workspace {workspace}?
          </AlertDialogTitle>
          <AlertDialogDescription>
            Imprint only ingests files inside a workspace&apos;s approved roots. Approving this folder lets the job
            run now, and later ingests of it too.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {overlaps.length > 0 && (
          <p className="text-sm">
            It is also a root of <span className="font-medium">{overlaps.join(", ")}</span>. Approving shares it:
            these workspaces can ingest the same files, and existing memories stay in their current workspace.
          </p>
        )}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
          <Button onClick={approve} disabled={busy}>
            {busy && <Spinner />}
            {overlaps.length > 0 ? "Share and approve" : "Approve root"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
