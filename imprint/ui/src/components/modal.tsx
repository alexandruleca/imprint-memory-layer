"use client";

import { useEffect, useRef } from "react";

/** Native modal supplies focus trapping, inert background, Escape and focus restoration. */
export function Modal({ children, label, onClose, busy = false, className = "" }: {
  children: React.ReactNode; label: string; onClose: () => void; busy?: boolean; className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    const previousFocus = document.activeElement as HTMLElement | null;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);
  return <dialog ref={ref} aria-label={label} aria-busy={busy}
    onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}
    onClick={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}
    className={`m-auto max-h-[90dvh] w-[calc(100%-2rem)] rounded-lg border border-border bg-background text-foreground shadow-xl backdrop:bg-black/30 backdrop:backdrop-blur-sm ${className}`}>
    {children}
  </dialog>;
}
