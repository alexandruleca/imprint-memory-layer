"use client";

import { useState } from "react";
import { Modal } from "./modal";
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

export function ConfirmDialog(props: Props) {
  return props.open ? <ConfirmDialogContent {...props} /> : null;
}

function ConfirmDialogContent({
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

  const confirmDisabled =
    busy || (confirmText !== undefined && typed !== confirmText);

  return (
    <Modal label={title} onClose={onCancel} busy={busy} className="max-w-md p-5 space-y-3">
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
              aria-label={`Type ${confirmText} to confirm`}
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
    </Modal>
  );
}
