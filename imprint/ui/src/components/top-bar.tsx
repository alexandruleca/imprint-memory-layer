"use client";

import { useSyncExternalStore } from "react";
import { UpdateButton } from "@/components/update-button";
import { WorkspaceSwitcher } from "@/components/workspace-switcher";

/** App-wide controls above every page. In the frameless desktop window the
 * bar is also a drag handle; its controls opt out. */
export function TopBar() {
  const isElectron = useSyncExternalStore(() => () => {}, () => !!window.electronAPI?.isElectron, () => false);
  return (
    <header
      className="flex h-12 shrink-0 items-center justify-end gap-2 px-4 sm:px-6"
      style={isElectron ? ({ WebkitAppRegion: "drag" } as React.CSSProperties) : undefined}
    >
      <UpdateButton />
      <WorkspaceSwitcher />
    </header>
  );
}
