"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";
import { LogoMark } from "./logo-mark";
import {
  LayoutDashboard,
  Network,
  Search,
  MessageSquare,
  AudioLines,
  Tags,
  FolderOpen,
  FileCode,
  RefreshCw,
  Settings,
  Terminal,
  ListOrdered,
  Power,
} from "lucide-react";
import { getVersion } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Hint } from "@/components/hint";

declare global {
  interface Window {
    electronAPI?: {
      isElectron: boolean;
      platform: string;
      close: () => void;
      quit: () => void;
      minimize: () => void;
      maximize: () => void;
      openCompanion: () => void;
      closeCompanion: () => void;
      pinCompanion: (pinned: boolean) => void;
      recoverCompanion?: () => void;
      beginCompanionDrag?: (point: { x: number; y: number }) => void;
      moveCompanionDrag?: (point: { x: number; y: number }) => void;
      endCompanionDrag?: () => void;
      openDashboard?: () => void;
      launcherMenu?: () => void;
      beginLauncherDrag?: (point: { x: number; y: number }) => void;
      moveLauncherDrag?: (point: { x: number; y: number }) => void;
      endLauncherDrag?: () => void;
      getCompanionState?: () => Promise<{ open: boolean; pinned: boolean; shortcutAvailable?: boolean } | null>;
      setCompanionVisible?: (visible: boolean) => Promise<{ open: boolean; pinned: boolean; shortcutAvailable?: boolean } | null>;
      onCompanionState?: (callback: (state: { open: boolean; pinned: boolean }) => void) => () => void;
    };
  }
}

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/graph", label: "Graph", icon: Network },
  { href: "/search", label: "Search", icon: Search },
  { href: "/chat", label: "Chat", icon: MessageSquare },
  { href: "/voice", label: "Voice", icon: AudioLines },
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
    { color: "#ff5f57", label: "×", title: "Hide window (keep Imprint running)", action: () => window.electronAPI?.close() },
    { color: "#febc2e", label: "−", title: "Minimize", action: () => window.electronAPI?.minimize() },
    { color: "#28c840", label: "+", title: "Maximize", action: () => window.electronAPI?.maximize() },
  ];

  return (
    <div className="app-no-drag mb-3 flex gap-1.5">
      {buttons.map(({ color, label, title, action }) => (
        <Hint key={title} label={title} side="bottom">
          <Button
            aria-label={title}
            onClick={action}
            className="group size-3 rounded-full border-0 p-0 transition-opacity hover:opacity-80"
            style={{ background: color }}
          >
            <span className="text-[7px] font-bold leading-none text-black/0 group-hover:text-black/50 select-none">
              {label}
            </span>
          </Button>
        </Hint>
      ))}
    </div>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const [version, setVersion] = useState<string>("");
  const isElectron = useSyncExternalStore(() => () => {}, () => !!window.electronAPI?.isElectron, () => false);

  useEffect(() => {
    getVersion()
      .then((d) => setVersion(d.version))
      .catch(() => setVersion(""));
  }, []);

  return (
    <aside className="w-16 sm:w-56 border-r border-border bg-card flex flex-col shrink-0 h-screen sticky top-0 overflow-y-auto z-30">
      <div className={`p-4 border-b border-border ${isElectron ? "app-drag" : ""}`}>
        {isElectron && <TrafficLights />}
        <div className="flex items-center gap-2.5">
          <LogoMark className="size-7 shrink-0" />
          <div className="hidden sm:block">
            <h1 className="text-lg font-bold leading-tight tracking-tight">Imprint</h1>
            <p className="text-xs text-muted-foreground">AI Memory Dashboard</p>
          </div>
        </div>
      </div>
      <nav className="flex-1 p-2 space-y-0.5">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active =
            href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            // The hint names the page while the narrow sidebar hides labels.
            <Hint key={href} label={label} side="right" className="sm:hidden">
              <Link
                aria-label={label}
                aria-current={active ? "page" : undefined}
                href={href}
                className={`flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors ${
                  active
                    ? "bg-accent text-accent-foreground font-medium"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                }`}
              >
                <Icon className="w-4 h-4" />
                <span className="hidden sm:inline">{label}</span>
              </Link>
            </Hint>
          );
        })}
      </nav>
      {isElectron && (
        <div className="p-2 border-t border-border">
          <Hint label="Quit Imprint and stop its background service" side="right">
            <Button
              variant="ghost"
              onClick={() => window.electronAPI?.quit()}
              aria-label="Quit Imprint"
              className="h-auto w-full justify-start gap-2.5 px-3 py-2 font-normal text-muted-foreground"
            >
              <Power className="w-4 h-4 shrink-0" aria-hidden="true" />
              <span className="hidden sm:inline">Quit Imprint</span>
            </Button>
          </Hint>
        </div>
      )}
      <div className="p-3 border-t border-border text-xs text-muted-foreground">
        Imprint {version ? version : "…"}
      </div>
    </aside>
  );
}
