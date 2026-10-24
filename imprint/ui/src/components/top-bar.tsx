"use client";

import { useSyncExternalStore } from "react";
import { UpdateButton } from "@/components/update-button";
import { WorkspaceSwitcher } from "@/components/workspace-switcher";

/** App-wide controls above every page. In the frameless desktop window the
 * bar's empty space is a drag handle (globals.css). The handle is an empty
 * element, not the bar itself: app-region is inherited, so a draggable bar
 * would also make its controls draggable. */
export function TopBar() {
  const isElectron = useSyncExternalStore(() => () => {}, () => !!window.electronAPI?.isElectron, () => false);
  return (
    <header className="flex h-12 shrink-0 items-center justify-end gap-2 px-4 sm:px-6">
      {isElectron && <div className="app-drag h-full flex-1" />}
      <UpdateButton />
      <WorkspaceSwitcher />
    </header>
  );
}
