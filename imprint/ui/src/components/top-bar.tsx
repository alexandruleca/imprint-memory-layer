"use client";

import { useSyncExternalStore } from "react";
import { UpdateButton } from "@/components/update-button";
import { WorkspaceSwitcher } from "@/components/workspace-switcher";

/** App-wide controls above every page. In the frameless desktop window the
 * bar's empty space is a drag handle. The handle is an empty element, not the
 * bar itself: app-region is inherited, so a draggable bar would also make the
 * dialogs and popover internals its controls render in place draggable. */
export function TopBar() {
  const isElectron = useSyncExternalStore(() => () => {}, () => !!window.electronAPI?.isElectron, () => false);
  return (
    <header className="flex h-12 shrink-0 items-center justify-end gap-2 px-4 sm:px-6">
      {isElectron && <div className="h-full flex-1" style={{ WebkitAppRegion: "drag" } as React.CSSProperties} />}
      <UpdateButton />
      <WorkspaceSwitcher />
    </header>
  );
}
