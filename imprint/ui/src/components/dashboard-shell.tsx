"use client";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { Sidebar } from "./sidebar";
import { TopBar } from "./top-bar";
export function DashboardShell({ children, overlays }: { children: React.ReactNode; overlays?: React.ReactNode }) {
  const route = usePathname().replace(/\/$/, "");
  const companion = route === "/companion";
  const launcher = route === "/launcher";
  const floating = companion || launcher;
  useEffect(() => { document.documentElement.classList.toggle("companion-document", companion);
    document.documentElement.classList.toggle("launcher-document", launcher);
    return () => document.documentElement.classList.remove("companion-document", "launcher-document"); }, [companion, launcher]);
  return <>{!floating && <><a href="#main-content" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:bg-background focus:p-3">Skip to content</a><Sidebar/></>}<div className="flex h-full min-w-0 flex-1 flex-col">{!floating && <TopBar/>}
    <main id="main-content" tabIndex={-1} className={`min-h-0 flex-1 ${floating ? "overflow-hidden" : "overflow-auto"}`}>{children}</main></div>{!floating && overlays}</>;
}
