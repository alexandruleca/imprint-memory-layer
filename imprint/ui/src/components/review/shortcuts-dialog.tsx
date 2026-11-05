"use client";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/kbd";
import { Switch } from "@/components/ui/switch";
import { useShortcutsSwitch } from "@/lib/use-review-shortcuts";

/** A shortcut and what it does on this page. */
export type Shortcut = [keys: string[], action: string];

/** The `?` dialog: every key the page knows, and the per-browser switch for
 * single-key shortcuts (WCAG 2.1.4). */
export function ShortcutsDialog({ open, onOpenChange, shortcuts }: {
  open: boolean; onOpenChange: (open: boolean) => void; shortcuts: Shortcut[];
}) {
  const [enabled, setEnabled] = useShortcutsSwitch();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>
            With rows selected, the keys that decide act on every selected row. Shortcuts are off while you type in a field.
          </DialogDescription>
        </DialogHeader>
        <table className="w-full text-sm">
          <tbody className="divide-y divide-border">
            {shortcuts.map(([keys, action]) => (
              <tr key={action}>
                <td className="py-1.5 pr-4 align-top whitespace-nowrap">
                  {keys.map((key, i) => <span key={key}>{i > 0 && <span className="px-1 text-muted-foreground">or</span>}<Kbd>{key}</Kbd></span>)}
                </td>
                <td className="py-1.5">{action}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <label className="flex items-center gap-2 text-sm">
          <Switch checked={enabled} onCheckedChange={setEnabled} aria-label="Single-key shortcuts" />
          Single-key shortcuts
        </label>
        <DialogFooter>
          <Button onClick={() => onOpenChange(false)}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** The tab bar's button for the dialog: "? Shortcuts", just "?" when narrow. */
export function ShortcutsButton({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="ghost" size="sm" aria-label="Keyboard shortcuts" onClick={onClick} className="shrink-0 text-muted-foreground">
      <Kbd>?</Kbd><span className="@max-[620px]/page:hidden">Shortcuts</span>
    </Button>
  );
}
