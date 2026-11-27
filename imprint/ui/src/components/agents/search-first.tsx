"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { getSearchFirst, type SearchFirst, type SearchFirstClient, type SearchFirstCounts } from "@/lib/api";
import { useCached } from "@/lib/use-cached";
import { plural } from "@/components/agents/format";
import { dayLabel } from "@/components/timestamp";

const NAMES: Record<string, string> = {
  "claude-code": "Claude Code", codex: "Codex", cursor: "Cursor", copilot: "GitHub Copilot", opencode: "OpenCode",
};
const name = (client: string) => NAMES[client] ?? client;
const SEEN_KEY = "imprint.search-first.restored-seen";

function useSearchFirst() {
  return useCached<SearchFirst>("search-first", () => getSearchFirst(), { refreshInterval: 10 * 60_000, persist: false });
}

function counts(side: SearchFirstCounts) {
  return side.share === null ? "no sessions" : `${side.share}% of ${plural(side.sessions, "session")}`;
}

function outcome(row: SearchFirstClient, minSessions: number) {
  const decision = row.decision;
  if (!decision) return { label: "Measuring", tone: "outline" as const, text: `Compared on ${dayLabel(row.decide_at, new Date())}, with at least ${minSessions} sessions on each side.` };
  if (decision.outcome === "restored") return { label: "Full block restored", tone: "destructive" as const, text: `Agents searched first less often, so Imprint gave ${name(row.client)} the full block again on ${dayLabel(decision.at, new Date())}.` };
  if (decision.outcome === "kept") return { label: "Short block kept", tone: "secondary" as const, text: "Agents search first as often as before." };
  return { label: "Short block kept", tone: "secondary" as const, text: `Too few sessions to compare (${minSessions} needed on each side).` };
}

/** How often each client's agents searched Imprint first before and after
 * it got the short rules block, and what Imprint decided (ADR 0214). Shown
 * on the Agents page once a client got the short block. */
export function SearchFirstPanel() {
  const { data } = useSearchFirst();
  const rows = Array.isArray(data?.clients) ? data.clients : [];
  if (!data || rows.length === 0) return null;
  return (
    <section aria-labelledby="search-first" className="space-y-2">
      <div>
        <h2 id="search-first" className="text-sm font-semibold">Searching first</h2>
        <p className="text-xs text-muted-foreground">
          Clients that load skills get a short Imprint rules block. Imprint checks that their agents still search it first, and gives a client the full block again if that falls by more than {data.max_drop} points.
        </p>
      </div>
      <ul className="divide-y divide-border overflow-hidden rounded-xl bg-card ring-1 ring-border">
        {rows.map((row) => {
          const told = outcome(row, data.min_sessions);
          return (
            <li key={row.client} className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-center sm:gap-4">
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                  {name(row.client)} <Badge variant={told.tone}>{told.label}</Badge>
                </p>
                <p className="text-xs text-muted-foreground">{told.text}</p>
              </div>
              <dl className="grid shrink-0 grid-cols-2 gap-x-4 text-xs tabular-nums sm:text-right">
                <dt className="text-muted-foreground">Before</dt><dt className="text-muted-foreground">After</dt>
                <dd>{counts(row.before)}</dd><dd>{counts(row.after)}</dd>
              </dl>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** Tells the user once, on any page, when Imprint gave a client the full
 * rules block again because its agents stopped searching first. */
export function SearchFirstAlerts() {
  const router = useRouter();
  const { data } = useSearchFirst();
  useEffect(() => {
    const rows = Array.isArray(data?.clients) ? data.clients : [];
    let seen: Record<string, number> = {};
    try { seen = JSON.parse(window.localStorage.getItem(SEEN_KEY) ?? "{}") ?? {}; } catch { /* Told again. */ }
    let changed = false;
    for (const row of rows) {
      if (row.decision?.outcome !== "restored" || seen[row.client] === row.decision.at) continue;
      seen[row.client] = row.decision.at;
      changed = true;
      toast(`${name(row.client)} has the full Imprint rules again`, {
        id: `search-first-${row.client}`, duration: 20_000,
        description: `Its agents searched Imprint first in ${row.decision.after ?? 0}% of sessions after the short rules block, down from ${row.decision.before ?? 0}%.`,
        action: { label: "Details", onClick: () => router.push("/agents/#sessions") },
      });
    }
    if (changed) {
      try { window.localStorage.setItem(SEEN_KEY, JSON.stringify(seen)); } catch { /* Told again after a reload. */ }
    }
  }, [data, router]);
  return null;
}
