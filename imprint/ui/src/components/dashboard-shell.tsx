"use client";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { Sidebar } from "./sidebar";
import { TopBar } from "./top-bar";
/** The window is a fixed frame: the sidebar and top bar stay put and <main> is
 * the one page scroller. <main> is positioned so that it is also the containing
 * block of the page: absolutely positioned content (sr-only labels, overlays)
 * then scrolls and clips with the page instead of stretching the document into
 * a second scroller. Pages flow inside it; only fixed-size panels (logs,
 * previews, side lists), dialog bodies and full-height pages scroll on their own. */
export function DashboardShell({ children }: { children: React.ReactNode }) {
  const companion = usePathname().replace(/\/$/, "") === "/companion";
  useEffect(() => { document.documentElement.classList.toggle("companion-document", companion);
    return () => document.documentElement.classList.remove("companion-document"); }, [companion]);
  return <>{!companion && <Sidebar/>}<div className="flex h-full min-w-0 flex-1 flex-col">{!companion && <TopBar/>}
    <main id="main-content" tabIndex={-1} className="relative min-h-0 flex-1 overflow-y-auto">{children}</main></div></>;
}
