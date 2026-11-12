/** The dashboard's pages (ADR 0182). A page with a `simple` name is part of
 * the simple dashboard, under that name; the others show in the advanced
 * dashboard only and stay reachable by their address in both. This file
 * imports nothing, so tests read it too. */
export type PageGroup = "everyday" | "memory" | "activity" | "system";
export type Page = {
  href: string;
  /** Its name in the advanced dashboard. */
  label: string;
  /** Its name in the simple dashboard; absent when it is advanced only. */
  simple?: string;
  group: PageGroup;
  /** Where it sits in the simple sidebar. */
  simpleGroup?: "everyday" | "memory" | "device";
};

export const PAGES: Page[] = [
  { href: "/", label: "Dashboard", simple: "Home", group: "everyday", simpleGroup: "everyday" },
  { href: "/search", label: "Search", simple: "Search", group: "everyday", simpleGroup: "everyday" },
  { href: "/chat", label: "Chat", simple: "Chat", group: "everyday", simpleGroup: "everyday" },
  { href: "/voice", label: "Voice", simple: "Echo", group: "everyday", simpleGroup: "everyday" },
  { href: "/recordings", label: "Recordings", simple: "Recordings", group: "everyday", simpleGroup: "everyday" },
  { href: "/reminders", label: "Reminders", simple: "Reminders", group: "everyday", simpleGroup: "everyday" },
  { href: "/core", label: "Core memories", simple: "About you", group: "memory", simpleGroup: "memory" },
  { href: "/skills", label: "Skills", simple: "Skills", group: "memory", simpleGroup: "memory" },
  { href: "/organize", label: "Organize", group: "memory" },
  { href: "/graph", label: "Graph", group: "memory" },
  { href: "/topics", label: "Topics", group: "memory" },
  { href: "/projects", label: "Projects", simple: "Library", group: "memory", simpleGroup: "memory" },
  { href: "/sources", label: "Sources", group: "memory" },
  { href: "/queue", label: "Queue", group: "activity" },
  { href: "/agents", label: "Agents", group: "activity" },
  { href: "/sync", label: "Sync", simple: "Devices", group: "activity", simpleGroup: "device" },
  { href: "/settings", label: "Settings", simple: "Settings", group: "system", simpleGroup: "device" },
  { href: "/commands", label: "Commands", group: "system" },
];

/** The page a path belongs to: "/projects/" and "/projects" are Projects. */
export function pageAt(pathname: string): Page | undefined {
  const path = pathname.replace(/\/+$/, "") || "/";
  return PAGES.find((page) => (page.href === "/" ? path === "/" : path === page.href || path.startsWith(`${page.href}/`)));
}

/** A page's name in a mode: its simple name there, when it has one. */
export function pageName(page: Page, simple: boolean): string {
  return simple && page.simple ? page.simple : page.label;
}
