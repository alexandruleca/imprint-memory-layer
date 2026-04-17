"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Props = {
  open: boolean;
  title: string;
  description?: string;
  /** If set, requires the user to type this exact string before confirm enables. */
  confirmText?: string;
  confirmLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
  children?: React.ReactNode;
};

export function ConfirmDialog({
  open,
  title,
  description,
  confirmText,
  confirmLabel = "Confirm",
  destructive = false,
  onConfirm,
  onCancel,
  busy = false,
  children,
}: Props) {
  const [typed, setTyped] = useState("");

  useEffect(() => {
    if (!open) setTyped("");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onCancel]);

  if (!open) return null;

  const confirmDisabled =
    busy || (confirmText !== undefined && typed !== confirmText);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/60"
        onClick={() => !busy && onCancel()}
      />
      <div className="relative z-10 w-full max-w-md rounded-lg border border-border bg-background p-5 shadow-lg space-y-3">
        <h3 className="text-base font-semibold">{title}</h3>
        {description && (
          <p className="text-sm text-muted-foreground whitespace-pre-wrap">
            {description}
          </p>
        )}
        {children}
        {confirmText !== undefined && (
          <div className="space-y-1.5">
            <p className="text-xs text-muted-foreground">
              Type <code className="font-mono bg-muted px-1 rounded">{confirmText}</code> to confirm:
            </p>
            <Input
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoFocus
              disabled={busy}
              className="h-8 text-sm"
            />
          </div>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant={destructive ? "destructive" : "default"}
            size="sm"
            onClick={onConfirm}
            disabled={confirmDisabled}
          >
            {busy ? "Working…" : confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
