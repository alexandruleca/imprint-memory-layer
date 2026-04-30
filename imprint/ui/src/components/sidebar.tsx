"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
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
  ListOrdered,
} from "lucide-react";
import { getVersion } from "@/lib/api";

declare global {
  interface Window {
    electronAPI?: {
      isElectron: boolean;
      platform: string;
      close: () => void;
      minimize: () => void;
      maximize: () => void;
    };
  }
}

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
  { href: "/queue", label: "Queue", icon: ListOrdered },
];

function TrafficLights() {
  const buttons = [
    { color: "#ff5f57", label: "×", title: "Close",    action: () => window.electronAPI?.close() },
    { color: "#febc2e", label: "−", title: "Minimize", action: () => window.electronAPI?.minimize() },
    { color: "#28c840", label: "+", title: "Maximize", action: () => window.electronAPI?.maximize() },
  ];

  return (
    <div
      className="flex gap-1.5 mb-3"
      style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
    >
      {buttons.map(({ color, label, title, action }) => (
        <button
          key={title}
          title={title}
          onClick={action}
          className="group w-3 h-3 rounded-full flex items-center justify-center transition-opacity hover:opacity-80"
          style={{ background: color, WebkitAppRegion: "no-drag" } as React.CSSProperties}
        >
          <span className="text-[7px] font-bold leading-none text-black/0 group-hover:text-black/50 select-none">
            {label}
          </span>
        </button>
      ))}
    </div>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const [version, setVersion] = useState<string>("");
  const [isElectron, setIsElectron] = useState(false);

  useEffect(() => {
    getVersion()
      .then((d) => setVersion(d.version))
      .catch(() => setVersion(""));
    setIsElectron(!!window.electronAPI?.isElectron);
  }, []);

  return (
    <aside className="w-56 border-r border-border bg-card flex flex-col shrink-0 h-screen sticky top-0 overflow-y-auto z-30">
      <div
        className="p-4 border-b border-border"
        style={isElectron ? ({ WebkitAppRegion: "drag" } as React.CSSProperties) : undefined}
      >
        {isElectron && <TrafficLights />}
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
        Imprint {version ? version : "…"}
      </div>
    </aside>
  );
}
