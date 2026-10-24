"use client";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { Sidebar } from "./sidebar";
import { TopBar } from "./top-bar";
export function DashboardShell({ children }: { children: React.ReactNode }) {
  const companion = usePathname().replace(/\/$/, "") === "/companion";
  useEffect(() => { document.documentElement.classList.toggle("companion-document", companion);
    return () => document.documentElement.classList.remove("companion-document"); }, [companion]);
  return <>{!companion && <Sidebar/>}<div className="flex h-full min-w-0 flex-1 flex-col">{!companion && <TopBar/>}
    <main id="main-content" tabIndex={-1} className="min-h-0 flex-1 overflow-auto">{children}</main></div></>;
}
