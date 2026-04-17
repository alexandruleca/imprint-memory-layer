"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Network,
  Search,
  MessageSquare,
  Tags,
  FolderOpen,
  FileCode,
  RefreshCw,
  Settings,
  Terminal,
} from "lucide-react";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/graph", label: "Graph", icon: Network },
  { href: "/search", label: "Search", icon: Search },
  { href: "/chat", label: "Chat", icon: MessageSquare },
  { href: "/topics", label: "Topics", icon: Tags },
  { href: "/projects", label: "Projects", icon: FolderOpen },
  { href: "/sources", label: "Sources", icon: FileCode },
  { href: "/sync", label: "Sync", icon: RefreshCw },
  { href: "/settings", label: "Settings", icon: Settings },
  { href: "/commands", label: "Commands", icon: Terminal },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-56 border-r border-border bg-card flex flex-col shrink-0 h-screen sticky top-0 overflow-y-auto z-30">
      <div className="p-4 border-b border-border">
        <h1 className="text-lg font-bold tracking-tight">Imprint</h1>
        <p className="text-xs text-muted-foreground">AI Memory Dashboard</p>
      </div>
      <nav className="flex-1 p-2 space-y-0.5">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active =
            href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors ${
                active
                  ? "bg-accent text-accent-foreground font-medium"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
              }`}
            >
              <Icon className="w-4 h-4" />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="p-3 border-t border-border text-xs text-muted-foreground">
        Imprint v1.0
      </div>
    </aside>
  );
}
