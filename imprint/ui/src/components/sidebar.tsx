"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
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
  Bot,
  ChevronRight,
} from "lucide-react";
import { getVersion } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Hint } from "@/components/hint";

declare global {
  interface Window {
    electronAPI?: {
      isElectron: boolean;
      platform: string;
      requestMicrophoneAccess?: () => Promise<{ status: string }>;
      openMicrophoneSettings?: () => Promise<boolean>;
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
      watchCursor?: (callback: (point: { x: number; y: number }) => void) => () => void;
      /** The running job's progress record, which the app reads directly. */
      activity?: () => Promise<{ command: string; phase?: string; processed?: number; total?: number; unit?: string } | null>;
    };
  }
}

type NavItem = { href: string; label: string; icon: typeof LayoutDashboard };

// Everyday pages first, without a heading; the rest in groups that collapse.
const NAV_GROUPS: { title?: string; items: NavItem[] }[] = [
  {
    items: [
      { href: "/", label: "Dashboard", icon: LayoutDashboard },
      { href: "/search", label: "Search", icon: Search },
      { href: "/chat", label: "Chat", icon: MessageSquare },
      { href: "/voice", label: "Voice", icon: AudioLines },
    ],
  },
  {
    title: "Memory",
    items: [
      { href: "/graph", label: "Graph", icon: Network },
      { href: "/topics", label: "Topics", icon: Tags },
      { href: "/projects", label: "Projects", icon: FolderOpen },
      { href: "/sources", label: "Sources", icon: FileCode },
    ],
  },
  {
    title: "Activity",
    items: [
      { href: "/queue", label: "Queue", icon: ListOrdered },
      { href: "/agents", label: "Agents", icon: Bot },
      { href: "/sync", label: "Sync", icon: RefreshCw },
    ],
  },
  {
    title: "System",
    items: [
      { href: "/settings", label: "Settings", icon: Settings },
      { href: "/commands", label: "Commands", icon: Terminal },
    ],
  },
];

// Groups the viewer collapsed, remembered per browser; Memory starts open.
const COLLAPSED_KEY = "imprint.sidebar.collapsed";
const DEFAULT_COLLAPSED = ["Activity", "System"];

// What toggling stored, read back on every render; kept in memory too when
// storage is unavailable (private window, blocked site data).
const collapsedListeners = new Set<() => void>();
let collapsedFallback: string | null = null;

function readCollapsed(): string {
  try {
    return localStorage.getItem(COLLAPSED_KEY) ?? collapsedFallback ?? "";
  } catch {
    return collapsedFallback ?? "";
  }
}

function subscribeCollapsed(notify: () => void) {
  collapsedListeners.add(notify);
  window.addEventListener("storage", notify);
  return () => {
    collapsedListeners.delete(notify);
    window.removeEventListener("storage", notify);
  };
}

function parseCollapsed(raw: string): string[] {
  try {
    const saved = JSON.parse(raw);
    if (Array.isArray(saved)) return saved.filter((title) => typeof title === "string");
  } catch {
    // Nothing stored yet, or not ours: the defaults stand.
  }
  return DEFAULT_COLLAPSED;
}

function toggleCollapsed(title: string) {
  const collapsed = parseCollapsed(readCollapsed());
  const next = JSON.stringify(collapsed.includes(title) ? collapsed.filter((t) => t !== title) : [...collapsed, title]);
  collapsedFallback = next;
  try {
    localStorage.setItem(COLLAPSED_KEY, next);
  } catch {
    // Not remembered across reloads; the group still toggles.
  }
  collapsedListeners.forEach((notify) => notify());
}

function isActive(href: string, pathname: string) {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

function NavLink({ href, label, icon: Icon, active }: NavItem & { active: boolean }) {
  return (
    // The hint names the page while the narrow sidebar hides labels.
    <Hint label={label} side="right" className="sm:hidden">
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
}

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
  const collapsedRaw = useSyncExternalStore(subscribeCollapsed, readCollapsed, () => "");
  const collapsed = useMemo(() => parseCollapsed(collapsedRaw), [collapsedRaw]);

  const isElectron = useSyncExternalStore(() => () => {}, () => !!window.electronAPI?.isElectron, () => false);

  useEffect(() => {
    getVersion()
      .then((d) => setVersion(d.version))
      .catch(() => setVersion(""));
  }, []);

  return (
    <aside className="w-16 sm:w-56 border-r border-border bg-card flex flex-col shrink-0 overflow-y-auto z-30">
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
      <nav aria-label="Pages" className="flex-1 p-2 space-y-0.5">
        {NAV_GROUPS.map(({ title, items }) => {
          const current = items.some(({ href }) => isActive(href, pathname));
          // The open page's group stays open; the narrow sidebar shows every icon.
          const open = !title || current || !collapsed.includes(title);
          const id = title ? `nav-${title.toLowerCase()}` : undefined;
          return (
            <div key={title ?? "main"} className={title ? "pt-2" : undefined}>
              {title && (
                <>
                  <div className="mx-3 mb-1 border-t border-border sm:hidden" aria-hidden="true" />
                  <button
                    type="button"
                    aria-expanded={open}
                    aria-controls={id}
                    disabled={current}
                    onClick={() => toggleCollapsed(title)}
                    className="hidden w-full items-center gap-1 px-3 py-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground disabled:cursor-default disabled:hover:text-muted-foreground sm:flex"
                  >
                    <ChevronRight className={`h-3 w-3 transition-transform ${open ? "rotate-90" : ""}`} aria-hidden="true" />
                    {title}
                  </button>
                </>
              )}
              <div id={id} className={`space-y-0.5 ${open ? "" : "sm:hidden"}`}>
                {items.map((item) => (
                  <NavLink key={item.href} {...item} active={isActive(item.href, pathname)} />
                ))}
              </div>
            </div>
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
