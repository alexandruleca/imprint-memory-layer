"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { EVERYWHERE, KindScope } from "@/components/core/core-row";
import type { CoreKind, CoreMemory } from "@/lib/api";

/** Write a guideline: the user's, so active at once. With `covers`, it is
 * made from those recorded core memories and starts as the first one's text
 * and kind. The page remounts it (a new key) to start from other records. */
export function AddCoreDialog({ open, onOpenChange, projects, maxText, covers = [], onAdd }: {
  open: boolean; onOpenChange: (open: boolean) => void; projects: string[]; maxText: number;
  covers?: CoreMemory[];
  /** Resolves true once added, which closes the dialog. */
  onAdd: (text: string, kind: CoreKind, scope: string, covers: string[]) => Promise<boolean>;
}) {
  const first = covers[0];
  const [text, setText] = useState(first?.text ?? "");
  const [kind, setKind] = useState<CoreKind>(first?.kind ?? "preference");
  const [scope, setScope] = useState(first && covers.every((m) => m.scope === first.scope) ? first.scope : EVERYWHERE);
  const [busy, setBusy] = useState(false);
  const making = covers.length > 0;
  async function add() {
    setBusy(true);
    try {
      if (await onAdd(text, kind, scope, covers.map((m) => m.id))) {
        setText("");
        onOpenChange(false);
      }
    } finally { setBusy(false); }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{making ? "Make a guideline" : "Write a guideline"}</DialogTitle>
          <DialogDescription>
            {making
              ? <>Every agent session starts with it, so word it to hold beyond this one case, for example &ldquo;Keep recipes gluten-free&rdquo;. It is active at once.</>
              : <>Every agent session starts with it. Write it as you would tell a new colleague, for example &ldquo;Commit only when I ask&rdquo;. It is active at once.</>}
          </DialogDescription>
        </DialogHeader>
        {making && (
          <div role="group" aria-label="Recorded core memories it generalises" className="space-y-1 rounded-lg border border-border bg-muted/40 p-2 text-xs">
            <p className="font-medium">Generalises {covers.length} recorded core {covers.length === 1 ? "memory" : "memories"}</p>
            <div className="max-h-28 space-y-1 overflow-y-auto text-muted-foreground">
              {covers.map((m) => <p key={m.id} className="border-l-2 border-border pl-2">{m.text}</p>)}
            </div>
          </div>
        )}
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (text.trim()) void add(); }}>
          <Textarea aria-label="New guideline" placeholder="Lead answers with the result, then the reasoning" value={text} autoFocus
            maxLength={maxText} onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim()) { e.preventDefault(); void add(); } }} />
          <KindScope kind={kind} scope={scope} projects={projects} label="the new guideline" onKind={setKind} onScope={setScope} />
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!text.trim() || busy}><Plus /> Add guideline</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
