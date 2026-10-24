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
export function DashboardShell({ children, overlays }: { children: React.ReactNode; overlays?: React.ReactNode }) {
  const route = usePathname().replace(/\/$/, "");
  const companion = route === "/companion";
  const launcher = route === "/launcher";
  const floating = companion || launcher;
  useEffect(() => { document.documentElement.classList.toggle("companion-document", companion);
    document.documentElement.classList.toggle("launcher-document", launcher);
    return () => document.documentElement.classList.remove("companion-document", "launcher-document"); }, [companion, launcher]);
  return <>{!floating && <><a href="#main-content" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:bg-background focus:p-3">Skip to content</a><Sidebar/></>}<div className="flex h-full min-w-0 flex-1 flex-col">{!floating && <TopBar/>}
    <main id="main-content" tabIndex={-1} className={`relative min-h-0 flex-1 ${floating ? "overflow-hidden" : "overflow-y-auto"}`}>{children}</main></div>{!floating && overlays}</>;
}
