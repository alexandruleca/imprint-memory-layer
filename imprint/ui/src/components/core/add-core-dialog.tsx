"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { EVERYWHERE, KindScope } from "@/components/core/core-row";
import type { CoreKind } from "@/lib/api";

/** Add a core memory: written by the user, so active at once. */
export function AddCoreDialog({ open, onOpenChange, projects, maxText, onAdd }: {
  open: boolean; onOpenChange: (open: boolean) => void; projects: string[]; maxText: number;
  /** Resolves true once added, which closes the dialog. */
  onAdd: (text: string, kind: CoreKind, scope: string) => Promise<boolean>;
}) {
  const [text, setText] = useState("");
  const [kind, setKind] = useState<CoreKind>("preference");
  const [scope, setScope] = useState(EVERYWHERE);
  const [busy, setBusy] = useState(false);
  async function add() {
    setBusy(true);
    try {
      if (await onAdd(text, kind, scope)) {
        setText("");
        onOpenChange(false);
      }
    } finally { setBusy(false); }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add a core memory</DialogTitle>
          <DialogDescription>
            Write it as you would tell a new colleague, for example &ldquo;Commit only when I ask&rdquo;. It is active at once.
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (text.trim()) void add(); }}>
          <Textarea aria-label="New core memory" placeholder="Prefers answers that lead with the result" value={text} autoFocus
            maxLength={maxText} onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim()) { e.preventDefault(); void add(); } }} />
          <KindScope kind={kind} scope={scope} projects={projects} label="the new core memory" onKind={setKind} onScope={setScope} />
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!text.trim() || busy}><Plus /> Add</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
