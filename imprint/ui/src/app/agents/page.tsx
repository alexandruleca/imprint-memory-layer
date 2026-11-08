"use client";

import { useEffect, useRef, useState } from "react";
import { Bot, ChevronRight, RotateCw, Search, X } from "lucide-react";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/split-page";
import { Timestamp, useNow } from "@/components/timestamp";
import { clientName, folderName, plural, sessionLength, whenInGroup } from "@/components/agents/format";
import { LoadMore } from "@/components/agents/load-more";
import { SessionDetails } from "@/components/agents/session-details";
import { StatusDot } from "@/components/agents/status";
import { getAgents, type AgentClient, type AgentSession, type AgentsPage } from "@/lib/api";
import { groupByTime } from "@/lib/time-groups";
import { useCached } from "@/lib/use-cached";
import { cn } from "@/lib/utils";

const DESCRIPTION = "Every AI client that uses Imprint starts its own MCP server: each one is a session here, with the tools it called. Calls are kept for 30 days, with their arguments shortened and secrets redacted.";
const POLL_MS = 3000;
const PAGE = 50;

type Sessions = AgentsPage & { olderLoaded: boolean };
type Filters = { client: string | null; q: string };

const olderThan = (s: AgentSession, edge: AgentSession) =>
  s.last_seen < edge.last_seen || (s.last_seen === edge.last_seen && s.id < edge.id);

/** A fresh first page over what is loaded: the pages the user loaded below
 * it stay, so a poll never shortens the list or moves the reader's place;
 * a session that started or ended meanwhile joins at the top. */
function mergeHead(prev: Sessions, head: AgentsPage): Sessions {
  const edge = head.sessions.at(-1);
  if (!prev.olderLoaded || !head.next || !edge) return { ...head, olderLoaded: false };
  const seen = new Set([...head.running, ...head.sessions].map((s) => s.id));
  const rest = prev.sessions.filter((s) => !seen.has(s.id) && olderThan(s, edge));
  return { ...head, sessions: [...head.sessions, ...rest], next: prev.next, olderLoaded: true };
}

/** The first page, polled while the page is visible, and older pages on
 * demand. The first page is cached (ADR 0152), so a return to Agents shows
 * the last one at once; each poll merges into what is loaded. */
function useAgentSessions({ client, q }: Filters) {
  const key = JSON.stringify([client, q]);
  const head = useCached(`agents:${key}`, () => getAgents({ limit: PAGE, client: client ?? undefined, q: q || undefined }),
    { refreshInterval: POLL_MS, revalidateOnJobs: false, dedupeMs: 0 });
  const [data, setData] = useState<Sessions | null>(null);
  const [merged, setMerged] = useState<{ key: string; head: AgentsPage } | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderFailed, setOlderFailed] = useState(false);
  const [olderError, setOlderError] = useState<string | null>(null);
  const filterKey = useRef(key);
  useEffect(() => { filterKey.current = key; }, [key]);

  // Each new first page (another filter's replaces what is loaded).
  if (head.data && merged?.head !== head.data) {
    const page = head.data;
    const replace = merged?.key !== key;
    setMerged({ key, head: page });
    setData((prev) => (replace || !prev ? { ...page, olderLoaded: false } : mergeHead(prev, page)));
    if (replace) { setOlderFailed(false); setOlderError(null); }
  }
  const error = olderError ?? (head.error ? head.error.message : null);

  async function loadOlder() {
    if (!data?.next || loadingOlder) return;
    const key = filterKey.current;
    setLoadingOlder(true);
    try {
      const page = await getAgents({ limit: PAGE, before: data.next, client: client ?? undefined, q: q || undefined });
      if (filterKey.current !== key) return;
      setData((prev) => {
        if (!prev) return prev;
        const have = new Set(prev.sessions.map((s) => s.id));
        return { ...prev, sessions: [...prev.sessions, ...page.sessions.filter((s) => !have.has(s.id))], next: page.next, olderLoaded: true };
      });
      setOlderFailed(false);
      setOlderError(null);
    } catch (e) {
      setOlderFailed(true);
      setOlderError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingOlder(false);
    }
  }

  const { refresh } = head;
  return { data, error, loadingOlder, olderFailed, loadOlder, retry: () => { setOlderError(null); void refresh(); } };
}

export default function AgentsPage() {
  const [client, setClient] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setQ(text.trim()), 250);
    return () => clearTimeout(timer);
  }, [text]);
  const { data, error, loadingOlder, olderFailed, loadOlder, retry } = useAgentSessions({ client, q });
  const now = useNow(15_000);
  const [open, setOpen] = useState<AgentSession | null>(null);

  if (!data) return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Agents" description={DESCRIPTION} />
      <div className="space-y-4 px-4 sm:px-8" aria-busy={!error}>
        {error ? (
          <Alert variant="destructive">
            <AlertTitle>Could not load agents</AlertTitle>
            <AlertDescription>{error}. The dashboard keeps trying every few seconds.</AlertDescription>
            <AlertAction><Button variant="outline" size="sm" onClick={retry}><RotateCw aria-hidden /> Retry</Button></AlertAction>
          </Alert>
        ) : (
          <>
            <div className="flex gap-3 overflow-hidden">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16 w-56 shrink-0 rounded-xl" />)}</div>
            <Skeleton className="h-9 w-full max-w-sm rounded-lg" />
            {[0, 1].map((i) => <Skeleton key={i} className="h-40 w-full rounded-xl" />)}
          </>
        )}
      </div>
    </div>
  );

  const filtered = client !== null || q !== "";
  const nothingYet = !filtered && data.running.length === 0 && data.sessions.length === 0;
  const groups = groupByTime(data.sessions, (s) => s.last_seen * 1000, { now });
  const clearFilters = () => { setClient(null); setText(""); setQ(""); };

  return (
    <div className="mx-auto max-w-5xl pb-10">
      <PageHeader title="Agents" description={DESCRIPTION} />
      <div className="space-y-6 px-4 sm:px-8">
        {nothingYet ? (
          <Empty className="border border-border bg-card">
            <EmptyHeader>
              <EmptyMedia variant="icon"><Bot aria-hidden /></EmptyMedia>
              <EmptyTitle>No agent has used Imprint yet.</EmptyTitle>
              <EmptyDescription>
                Sessions appear here when a connected client, such as Claude Code or Cursor, starts Imprint&apos;s MCP server.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <>
            <ClientFilter clients={data.clients} selected={client} onSelect={setClient} now={now} />
            <div className="flex flex-wrap items-center gap-2">
              <InputGroup className="max-w-sm min-w-0 flex-1 bg-card">
                <InputGroupAddon><Search aria-hidden /></InputGroupAddon>
                <InputGroupInput aria-label="Filter sessions by folder, client or workspace" placeholder="Filter by folder, client, workspace"
                  value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape" && text) { e.preventDefault(); setText(""); } }} />
                {text && (
                  <InputGroupAddon align="inline-end">
                    <InputGroupButton size="icon-xs" aria-label="Clear the filter" onClick={() => setText("")}><X aria-hidden /></InputGroupButton>
                  </InputGroupAddon>
                )}
              </InputGroup>
              {filtered && <Button variant="ghost" size="sm" onClick={clearFilters}>Clear filters</Button>}
            </div>
            {error && (
              <Alert variant="destructive">
                <AlertDescription>Could not refresh: {error}. Showing what was loaded last.</AlertDescription>
                <AlertAction><Button variant="outline" size="sm" onClick={retry}><RotateCw aria-hidden /> Retry</Button></AlertAction>
              </Alert>
            )}

            {data.running.length > 0 && (
              <section aria-labelledby="agents-running" className="space-y-2">
                <h2 id="agents-running" className="flex items-center gap-2 text-sm font-semibold">
                  <StatusDot connected decorative />
                  Running now <span className="font-normal text-muted-foreground tabular-nums">{data.running.length}</span>
                </h2>
                <ul className="divide-y divide-border overflow-hidden rounded-xl bg-card ring-1 ring-success/30">
                  {data.running.map((s) => <SessionRow key={s.id} session={s} now={now} onOpen={() => setOpen(s)} />)}
                </ul>
              </section>
            )}

            <section aria-labelledby="agents-history" className="space-y-2">
              <h2 id="agents-history" className="flex items-baseline justify-between gap-2 text-sm font-semibold">
                <span>History</span>
                <span className="text-xs font-normal text-muted-foreground tabular-nums">
                  {data.total === 0 ? "" : data.sessions.length < data.total
                    ? `${data.sessions.length.toLocaleString()} of ${plural(data.total, "session")}`
                    : plural(data.total, "session")}
                </span>
              </h2>
              {data.sessions.length === 0 ? (
                <Empty className="border border-dashed border-border py-8">
                  <EmptyHeader>
                    <EmptyTitle>{filtered ? "No finished sessions match" : "No finished sessions yet"}</EmptyTitle>
                    <EmptyDescription>
                      {filtered ? "Try another folder or client name." : "A session lands here when its client closes it."}
                    </EmptyDescription>
                  </EmptyHeader>
                  {filtered && <EmptyContent><Button variant="outline" size="sm" onClick={clearFilters}>Clear filters</Button></EmptyContent>}
                </Empty>
              ) : (
                <div className="space-y-2">
                  {groups.map((g, i) => {
                    const id = `agents-group-${g.key}`;
                    const partial = i === groups.length - 1 && !!data.next;
                    return (
                      <section key={g.key} aria-labelledby={id}>
                        <h3 id={id} className="sticky top-0 z-10 -mx-1 flex items-baseline gap-2 bg-background px-1 pt-2 pb-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                          {g.label}
                          <span className="font-normal tabular-nums normal-case" aria-label={partial ? `${g.items.length} or more sessions` : plural(g.items.length, "session")}>
                            {g.items.length.toLocaleString()}{partial && "+"}
                          </span>
                        </h3>
                        <ul className="divide-y divide-border overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10">
                          {g.items.map((s) => <SessionRow key={s.id} session={s} group={g.key} now={now} onOpen={() => setOpen(s)} />)}
                        </ul>
                      </section>
                    );
                  })}
                  <LoadMore hasMore={!!data.next} loading={loadingOlder} failed={olderFailed} onLoad={loadOlder} label="Load older sessions"
                    status={data.next ? `Showing ${data.sessions.length.toLocaleString()} of ${data.total.toLocaleString()}` : "That is every recorded session. Sessions are kept for 30 days."} />
                </div>
              )}
            </section>
          </>
        )}
      </div>
      <Sheet open={!!open} onOpenChange={(o) => { if (!o) setOpen(null); }}>
        <SheetContent className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-2xl">
          {open && <SessionDetails key={open.id} session={open} />}
        </SheetContent>
      </Sheet>
    </div>
  );
}

/** The clients as filter buttons with their totals; on a phone they scroll sideways. */
function ClientFilter({ clients, selected, onSelect, now }: {
  clients: AgentClient[]; selected: string | null; onSelect: (client: string | null) => void; now: Date;
}) {
  if (!clients.length) return null;
  return (
    <section aria-labelledby="agent-clients" className="space-y-2">
      <h2 id="agent-clients" className="text-sm font-semibold">Clients</h2>
      <ul className="-mx-4 flex snap-x scroll-px-4 gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:grid sm:grid-cols-2 sm:overflow-visible sm:px-0 sm:pb-0 lg:grid-cols-3">
        {clients.map((c) => {
          const name = clientName(c.client);
          const pressed = selected === c.client;
          return (
            <li key={c.client} className="w-60 shrink-0 snap-start sm:w-auto" aria-label={name}>
              <button type="button" aria-pressed={pressed} onClick={() => onSelect(pressed ? null : c.client)}
                title={pressed ? "Show every client" : `Show only ${name}`}
                className={cn(
                  "flex h-full w-full flex-col gap-1 rounded-xl bg-card px-3.5 py-2.5 text-left ring-1 ring-foreground/10 transition-colors outline-none",
                  "hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50",
                  pressed && "bg-muted ring-2 ring-foreground/60 hover:bg-muted",
                )}>
                <span className="flex w-full min-w-0 items-center gap-2 text-sm font-medium">
                  <Bot className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="min-w-0 flex-1 truncate" title={name}>{name}</span>
                  {c.connected > 0 && (
                    <Badge variant="secondary" className="gap-1.5 text-[10px] text-success">
                      <span aria-hidden className="size-1.5 rounded-full bg-success" />{c.connected} running
                    </Badge>
                  )}
                </span>
                <span className="text-xs text-muted-foreground tabular-nums">
                  {plural(c.sessions, "session")} · {plural(c.calls, "call")}
                  {c.errors > 0 && <span className="text-destructive"> · {plural(c.errors, "error")}</span>}
                </span>
                <span className="text-xs text-muted-foreground">Last active <Timestamp seconds={c.last_seen} now={now} /></span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function SessionRow({ session: s, group, now, onOpen }: { session: AgentSession; group?: string; now: Date; onOpen: () => void }) {
  const name = clientName(s.client);
  const folder = folderName(s.directory);
  const errors = s.errors > 0 ? `, ${plural(s.errors, "error")}` : "";
  return (
    <li>
      <button type="button" onClick={onOpen}
        aria-label={`${folder}, ${name}, ${s.connected ? "running" : "finished"}, ${plural(s.calls, "call")}${errors}. Show calls`}
        className="group flex w-full min-w-0 items-center gap-3 px-4 py-2.5 text-left text-sm outline-none hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset">
        {s.connected && <StatusDot connected decorative />}
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-baseline gap-2">
            <span className="truncate font-medium" title={s.directory ?? undefined}>{folder}</span>
            {s.workspace && s.workspace !== "default" && (
              <Badge variant="outline" className="hidden shrink-0 text-[10px] sm:inline-flex">{s.workspace}</Badge>
            )}
          </span>
          <span className="block truncate text-xs text-muted-foreground tabular-nums">
            {name}{s.client_version && <span className="hidden sm:inline"> {s.client_version}</span>}
            {" · "}{plural(s.calls, "call")}
            {s.errors > 0 && <span className="text-destructive">{" · "}{plural(s.errors, "error")}</span>}
            {" · "}<span title={s.connected ? "Running for" : "Ran for"}>{sessionLength(s, now)}</span>
            {s.last_tool && <span className="hidden md:inline">{" · last "}<span className="font-mono">{s.last_tool}</span></span>}
          </span>
        </span>
        <span className="shrink-0 text-right text-xs text-muted-foreground tabular-nums">
          {s.connected
            ? <><span className="hidden sm:inline">active </span><Timestamp seconds={s.last_seen} now={now} /></>
            : <Timestamp seconds={s.last_seen} now={now} text={whenInGroup(s.last_seen, group ?? "", now)} />}
        </span>
        <ChevronRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" aria-hidden />
      </button>
    </li>
  );
}
