"use client";

import { useMemo } from "react";
import { getProjects } from "@/lib/api";
import { useCached } from "@/lib/use-cached";

/** The active workspace's project names for pickers and filters, from the
 * cached project list (GET /api/projects, every project, largest first):
 * shown at once from the last answer and fetched again in the background.
 * Until the first answer, `names` is empty and `loaded` false.
 * `enabled: false` waits (a dialog not yet open, say); `order: "name"` sorts A to Z. */
const NONE: string[] = [];

export function useProjectNames({ enabled = true, order = "size" }: { enabled?: boolean; order?: "size" | "name" } = {}) {
  const cached = useCached(enabled ? "projects" : null, getProjects);
  const names = useMemo(() => {
    if (!cached.data) return NONE;
    const list = cached.data.projects.map((p) => p.name).filter(Boolean);
    return order === "name" ? list.sort((a, b) => a.localeCompare(b)) : list;
  }, [cached.data, order]);
  return { names, loaded: cached.data !== undefined, refresh: cached.refresh };
}
