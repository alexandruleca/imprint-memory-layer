"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { RefreshCw } from "lucide-react";
import { SourceReader, type ReaderTarget } from "@/components/source-reader";
import { ListSkeleton } from "@/components/loaders";
import { PlacementsPanel, inTab, type PlacementTab, type PlacementsContext } from "@/components/organize/placements-panel";
import { ProfilesPanel } from "@/components/organize/profiles-panel";
import { parseKey, proposalKey, proposalName, originLabel, type OrganizeKind, type OrganizeStatus } from "@/components/organize/proposal-row";
import { RunButton } from "@/components/organize/run-button";
import { StatusStrip, coreCounts, lastRunText, organizeJob } from "@/components/organize/status-strip";
import { PAGE_SIZES } from "@/components/review/pager";
import { CountTab, ReviewHeader } from "@/components/review/review-header";
import { Announcer, RESET, reviewTab, type ReviewTabState } from "@/components/review/review-list";
import { ShortcutsButton, ShortcutsDialog, type Shortcut } from "@/components/review/shortcuts-dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList } from "@/components/ui/tabs";
import {
  decideOrganize, enqueueCommand, getCoreMemories, getOrganize, reopenOrganize, undoOrganize,
  type OrganizeProposal, type OrganizeReview,
} from "@/lib/api";
import { useQueue } from "@/lib/queue";
import { useHashTab } from "@/lib/use-hash-tab";
import { useReviewShortcuts } from "@/lib/use-review-shortcuts";
import { useStoredNumber, useStoredString } from "@/lib/use-stored";
import { useDecisions } from "@/lib/write-queue";

const TABS = ["review", "moved", "decided", "profiles"] as const;
type Tab = (typeof TABS)[number];
const UNDO_TOAST = "organize-undo";

const INFO = (
  <div className="space-y-2">
    <p className="font-medium">How Organize decides</p>
    <p>Organize finds the project each memory in a catch-all belongs to (no project, Desktop, unassigned, URLs, desktop conversations), with the evidence.</p>
    <p><span className="font-medium">Moves by itself</span> when a source names only one project&apos;s folder, or only one project&apos;s distinctive name at least twice, in its opening 50,000 characters. Each can be moved back.</p>
    <p><span className="font-medium">Waits for you</span> otherwise, including every local-model placement. It also writes project profiles, records core memories and drafts guidelines from them, which you approve on Core memories.</p>
    <p className="text-muted-foreground">Moves hold through refreshes and reach paired devices.</p>
  </div>
);

const SHORTCUTS: Shortcut[] = [
  [["j", "↓"], "Next row"], [["k", "↑"], "Previous row"], [["J", "K"], "Next / previous project in the rail"],
  [["x"], "Select the row"], [["a"], "Move"], [["r"], "Keep here"], [["u"], "Move back · Reopen"],
  [["Enter"], "Show or hide details"], [["o"], "Read the source"], [["z", "⌘Z"], "Undo the last action"],
  [["[", "]"], "Previous / next page"], [["/"], "Search"], [["1", "4"], "Switch tab"],
  [["Esc"], "Clear the selection, close details"], [["?"], "This list"],
];

const RESULT: Record<OrganizeKind, OrganizeStatus> = { move: "accepted", keep: "rejected", back: "rejected", reopen: "pending" };

/** How a decision is taken back. A Move back is reopened and moved again,
 * so it ends as the user's move: Organize's own move cannot be restored. */
function inverse(kind: OrganizeKind, from: OrganizeStatus): OrganizeKind[] {
  if (kind === "move" || kind === "keep") return ["reopen"];
  if (kind === "back") return ["reopen", "move"];
  return from === "accepted" ? ["move"] : ["keep"];
}

const VERB: Record<OrganizeKind, string> = { move: "move", keep: "be kept", back: "move back", reopen: "reopen" };

function decisionText(kind: OrganizeKind, items: OrganizeProposal[]) {
  const one = items.length === 1;
  const what = one ? proposalName(items[0]) : `${items.length} sources`;
  const targets = new Set(items.map((p) => p.target));
  switch (kind) {
    case "move": return `Moved ${what} to ${targets.size === 1 ? items[0].target : "their projects"}`;
    case "keep": return `Kept ${what} where ${one ? "it is" : "they are"}`;
    case "back": return `Moved ${what} back to ${one ? originLabel(items[0].origin) : "where they were"}`;
    case "reopen": return `Reopened ${what} for review`;
  }
}

const largestGroup = (proposals: OrganizeProposal[]) => {
  const counts = new Map<string, number>();
  proposals.filter((p) => p.status === "pending").forEach((p) => counts.set(p.target, (counts.get(p.target) ?? 0) + 1));
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0]?.[0] ?? "";
};

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const seenKey = (workspace: string) => `imprint.organize.seen.${workspace}`;

/** When this browser last marked the workspace's automatic moves as seen. */
function lastSeenIn(workspace: string) {
  try { return Number(window.localStorage.getItem(seenKey(workspace))) || 0; } catch { return 0; }
}

export default function OrganizePage() {
  const root = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<OrganizeReview | null>(null);
  const [error, setError] = useState("");
  const [order, setOrder] = useState<ReadonlyMap<string, number>>(() => new Map());
  const [arrived, setArrived] = useState(0);
  const [stamps, setStamps] = useState<ReadonlyMap<string, number>>(() => new Map());
  const [core, setCore] = useState<{ proposals: number; conflicts: number } | null>(null);
  const [tabs, setTabs] = useState<Record<PlacementTab, ReviewTabState>>(() => ({
    review: reviewTab({ group: null }), moved: reviewTab(), decided: reviewTab(),
  }));
  const [profileView, setProfileView] = useState({ query: "", project: "" });
  const [reading, setReading] = useState<ReaderTarget | null>(null);
  const [help, setHelp] = useState(false);
  const [queuing, setQueuing] = useState(false);
  const [linked, openTab] = useHashTab(TABS);
  const [size, setSize] = useStoredNumber("imprint.review.page-size", 20, PAGE_SIZES);
  const [seen, setSeen] = useStoredString(seenKey(data?.workspace ?? ""));
  // The tab a link without a hash opens, chosen once from the first load so
  // deciding the last proposal does not switch tabs under the cursor.
  const [fallback, setFallback] = useState<Tab | null>(null);
  const lastSeen = Number(seen) || 0;
  const queue = useQueue();
  const known = useRef<Set<string> | null>(null);
  const workspace = useRef("");
  const reload = useRef<() => void>(() => {});

  const engine = useDecisions<OrganizeKind, OrganizeStatus>({
    send: async (kind, keys, keepalive) => {
      const items = keys.map(parseKey);
      const init = keepalive ? { keepalive } : undefined;
      const result = kind === "move" || kind === "keep" ? await decideOrganize(workspace.current, items, kind === "move", init)
        : kind === "back" ? await undoOrganize(workspace.current, items, init)
          : await reopenOrganize(workspace.current, items, init);
      return result.failed.map((failure) => ({ key: proposalKey(failure), error: failure.error }));
    },
    result: (kind) => RESULT[kind],
    inverse,
    onDrain: () => reload.current(),
    onFailed: (kind, failed) => toast.error(`${failed.length} could not ${VERB[kind]}`, { description: failed[0].error }),
  });
  const prune = engine.prune;

  const absorb = useCallback((next: OrganizeReview, finished: boolean) => {
    workspace.current = next.workspace;
    const keys = next.proposals.map(proposalKey);
    const before = known.current;
    const fresh = before ? next.proposals.filter((p) => p.status === "pending" && !before.has(proposalKey(p))).length : 0;
    known.current = new Set([...(before ?? []), ...keys]);
    setOrder((prev) => {
      if (keys.every((key) => prev.has(key))) return prev;
      const map = new Map(prev);
      keys.forEach((key) => { if (!map.has(key)) map.set(key, map.size); });
      return map;
    });
    setData(next);
    setError("");
    if (fresh) setArrived((n) => n + fresh);
    setTabs((prev) => (prev.review.group !== null ? prev : { ...prev, review: { ...prev.review, group: largestGroup(next.proposals) } }));
    const seenAt = lastSeenIn(next.workspace);
    const waiting = next.proposals.some((p) => p.status === "pending");
    const freshMoves = next.proposals.some((p) => p.status === "auto" && (p.decided_at ?? 0) > seenAt);
    setFallback((prev) => prev ?? (waiting || !freshMoves ? "review" : "moved"));
    prune();
    if (finished) toast.success(`Organize finished: ${fresh} new to review`);
  }, [prune]);

  const load = useCallback(async (finished = false) => {
    try { absorb(await getOrganize(), finished); }
    catch (e) { setError(message(e)); }
    getCoreMemories().then((c) => {
      setCore(Array.isArray(c?.memories) ? coreCounts(c.memories) : null);
    }).catch(() => {});
  }, [absorb]);
  useEffect(() => { reload.current = () => void load(); }, [load]);
  useEffect(() => {
    let live = true;
    getOrganize().then((next) => { if (live) absorb(next, false); }, (e) => { if (live) setError(message(e)); });
    getCoreMemories().then((c) => {
      if (live) setCore(Array.isArray(c?.memories) ? coreCounts(c.memories) : null);
    }).catch(() => {});
    return () => { live = false; };
  }, [absorb]);

  // A run that ends brings its proposals in by itself.
  const job = organizeJob(queue);
  const running = job !== null;
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) void load(true);
    wasRunning.current = running;
  }, [running, load]);
  const lock = queue?.active ? queue.active.label ?? queue.active.command : null;

  const proposals = useMemo(() => (data?.proposals ?? []).map((p) => {
    const status = engine.overrides.get(proposalKey(p));
    if (!status || status === p.status) return p;
    return { ...p, status, decided_at: status === "pending" ? null : stamps.get(proposalKey(p)) ?? p.decided_at };
  }), [data, engine.overrides, stamps]);
  const byKey = useMemo(() => new Map(proposals.map((p) => [proposalKey(p), p])), [proposals]);
  const profiles = useMemo(() => new Map((data?.profiles ?? []).map((p) => [p.project, p])), [data]);

  const count = (tab: PlacementTab) => proposals.filter((p) => inTab(tab, p.status)).length;
  const autoFresh = proposals.filter((p) => p.status === "auto" && (p.decided_at ?? 0) > lastSeen).length;
  const pending = count("review");
  const tab: Tab = linked ?? fallback ?? "review";

  const patchTab = useCallback((which: PlacementTab, patch: Partial<ReviewTabState>) => {
    setTabs((prev) => ({ ...prev, [which]: { ...prev[which], ...patch } }));
  }, []);
  const openWith = (which: PlacementTab, patch: Partial<ReviewTabState> = {}) => {
    patchTab(which, { ...RESET, ...patch });
    openTab(which);
  };

  function undo() {
    const undone = engine.undo();
    if (!undone) { toast("Nothing to undo", { id: UNDO_TOAST }); return; }
    const now = Date.now() / 1000;
    setStamps((prev) => new Map([...prev, ...undone.keys.map((key): [string, number] => [key, now])]));
    if (tab !== "profiles") {
      // Functional: the toast's Undo may run from an older render.
      setTabs((prev) => ({ ...prev, [tab]: { ...prev[tab], focus: undone.keys[0], reveal: true, selected: new Set(),
                                              focusToken: prev[tab].focusToken + 1 } }));
    }
    const target = byKey.get(undone.keys[0])?.target;
    toast(`Undone: ${undone.note.charAt(0).toLowerCase()}${undone.note.slice(1)}`, {
      id: UNDO_TOAST, duration: 6000,
      description: undone.kind === "back" ? `${undone.keys.length === 1 ? "It is" : "They are"} in ${target} again, as moved by you.` : undefined,
    });
  }

  function act(kind: OrganizeKind, keys: string[]) {
    const items = keys.map((key) => byKey.get(key)).filter((p): p is OrganizeProposal => !!p);
    if (!items.length) return;
    const note = decisionText(kind, items);
    const now = Date.now() / 1000;
    setStamps((prev) => new Map([...prev, ...keys.map((key): [string, number] => [key, now])]));
    engine.decide(kind, items.map(proposalKey), new Map(items.map((p) => [proposalKey(p), p.status])), note);
    toast(note, { id: UNDO_TOAST, duration: 8000, action: { label: "Undo", onClick: () => undo() } });
  }

  async function run(withoutModel: boolean) {
    setQueuing(true);
    try {
      await enqueueCommand("organize", withoutModel ? { no_model: true } : {});
      toast.success("Organize queued", { description: "Its progress shows above; new proposals arrive when it ends." });
    } catch (e) {
      toast.error("Could not queue Organize", { description: message(e) });
    } finally { setQueuing(false); }
  }

  useReviewShortcuts(root, {
    "1": () => openTab("review"), "2": () => openTab("moved"), "3": () => openTab("decided"), "4": () => openTab("profiles"),
    "?": () => setHelp(true), z: undo, "mod+z": undo,
  });

  const ctx: PlacementsContext = {
    proposals, order, profiles, autoMove: data?.background?.auto_move ?? true, lock, saving: engine.pending,
    errors: engine.errors, lastSeen, neverRan: !!data && !data.last_run && !data.background?.last_pass && !data.proposals.length,
    size, onSize: (next) => { setSize(next); setTabs((prev) => ({ review: { ...prev.review, page: 0, reveal: false }, moved: { ...prev.moved, page: 0, reveal: false }, decided: { ...prev.decided, page: 0, reveal: false } })); },
    root, act,
    retry: (key) => { const p = byKey.get(key); if (p) engine.retry(key, p.status); },
    read: (p) => setReading({ source: p.source, project: p.status === "auto" || p.status === "accepted" ? p.target : undefined, title: p.title || p.source }),
    openTab: openWith,
    markSeen: () => {
      setSeen(String(Date.now() / 1000));
      patchTab("moved", { ...RESET, view: "all" });
      toast("Marked all as seen");
    },
    run: () => void run(false),
  };

  const header = (
    <ReviewHeader title="Organize" info={<>{INFO}{data && <p className="mt-2 text-xs text-muted-foreground">{lastRunText(data)}</p>}</>}
      infoLabel="How Organize decides" actions={<RunButton disabled={queuing || running} onRun={(withoutModel) => void run(withoutModel)} />} />
  );

  return (
    <Announcer>
      <div ref={root} className="@container/page flex min-h-full flex-col lg:h-full">
        {header}
        {error && (
          <div className="px-4 pb-3 sm:px-8">
            <Alert variant="destructive">
              <AlertTitle>Could not load Organize: {error}</AlertTitle>
              <AlertDescription>
                <Button variant="outline" size="sm" className="mt-1" onClick={() => void load()}><RefreshCw /> Try again</Button>
              </AlertDescription>
            </Alert>
          </div>
        )}
        {!data ? (!error && (
          <div className="space-y-3 px-4 sm:px-8" aria-busy="true">
            <Skeleton className="h-16 w-full rounded-xl" />
            <p className="flex gap-6 border-b border-border pb-2 text-sm text-muted-foreground"><span>To review</span><span>Moved automatically</span><span>Decided</span><span>Profiles</span></p>
            <ListSkeleton count={6} />
          </div>
        )) : (
          <>
            <StatusStrip review={data} running={job} lock={lock} core={core?.proposals ?? null} conflicts={core?.conflicts ?? 0} arrived={arrived} />
            <Tabs value={tab} onValueChange={(value) => { openTab(value as Tab); setArrived(0); }} className="flex min-h-0 flex-1 flex-col gap-0">
              <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-border bg-background px-4 sm:px-8 lg:static">
                <TabsList variant="line" data-tour="organize-tabs" className="h-10 min-w-0 justify-start overflow-x-auto">
                  <CountTab value="review" name="To review" short="Review" count={pending} pill />
                  <CountTab value="moved" name="Moved automatically" short="Moved" count={count("moved")} fresh={autoFresh} />
                  <CountTab value="decided" name="Decided" count={count("decided")} />
                  <CountTab value="profiles" name="Profiles" count={data.profiles.length} />
                </TabsList>
                <span className="flex-1" />
                <ShortcutsButton onClick={() => setHelp(true)} />
              </div>
              {(["review", "moved", "decided"] as const).map((which) => (
                <TabsContent key={which} value={which} className="flex min-h-0 flex-1 flex-col px-4 pt-3 pb-4 sm:px-8">
                  <PlacementsPanel tab={which} state={tabs[which]} onState={(patch) => patchTab(which, patch)} ctx={ctx} />
                </TabsContent>
              ))}
              <TabsContent value="profiles" className="flex min-h-0 flex-1 flex-col px-4 pt-3 pb-4 sm:px-8">
                <ProfilesPanel profiles={data.profiles} proposals={proposals} query={profileView.query} project={profileView.project}
                  onChange={(patch) => setProfileView((prev) => ({ ...prev, ...patch }))} onOpen={openWith} />
              </TabsContent>
            </Tabs>
          </>
        )}
      </div>
      <SourceReader target={reading} onClose={() => setReading(null)} />
      <ShortcutsDialog open={help} onOpenChange={setHelp} shortcuts={SHORTCUTS} />
    </Announcer>
  );
}
