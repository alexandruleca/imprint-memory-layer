"use client";

import { useEffect, useState } from "react";
import { Bot, CheckCircle2, ChevronRight, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/loaders";
import { PageHeader } from "@/components/split-page";
import { getAgentCalls, getAgents, type AgentCall, type AgentClient, type AgentSession } from "@/lib/api";
import { ago, formatDuration } from "@/lib/queue";
import { cn } from "@/lib/utils";

const DESCRIPTION = "Every AI client that uses Imprint starts its own MCP server: each one is a session here, with the tools it called. Calls are kept for 30 days, with their arguments shortened and secrets redacted.";
const POLL_MS = 3000;

// The names clients give themselves in the MCP handshake.
const CLIENT_NAMES: Record<string, string> = {
  "claude-code": "Claude Code",
  "claude-ai": "Claude Desktop",
  "cursor-vscode": "Cursor",
  "codex-mcp-client": "Codex",
};
const clientName = (client: string | null) => (client ? CLIENT_NAMES[client] ?? client : "Unknown client");

const folderName = (dir: string | null) => (dir ? dir.split(/[\\/]/).filter(Boolean).pop() ?? dir : "No folder");
const plural = (n: number, word: string) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
const ms = (value: number) => (value < 1000 ? `${Math.round(value)} ms` : formatDuration(value / 1000));

/** Polls /api/agents while the page is open. */
function useAgents() {
  const [data, setData] = useState<{ sessions: AgentSession[]; clients: AgentClient[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const poll = () => getAgents().then(
      (d) => { if (live) { setData(d); setError(null); } },
      (e) => { if (live) setError(e instanceof Error ? e.message : String(e)); },
    );
    void poll();
    const timer = setInterval(poll, POLL_MS);
    return () => { live = false; clearInterval(timer); };
  }, []);
  return { data, error };
}

export default function AgentsPage() {
  const { data, error } = useAgents();
  const [open, setOpen] = useState<AgentSession | null>(null);
  const shown = open && (data?.sessions.find((s) => s.id === open.id) ?? open);

  if (!data) return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Agents" description={DESCRIPTION} />
      <div className="space-y-4 px-4 sm:px-8" aria-busy={!error}>
        {error ? <p role="alert" className="text-sm text-destructive">Could not load agents: {error}</p> : (
          <><Skeleton className="h-24 w-full rounded-xl" /><Skeleton className="h-48 w-full rounded-xl" /></>
        )}
      </div>
    </div>
  );

  const connected = data.sessions.filter((s) => s.connected).length;
  return (
    <div className="mx-auto max-w-5xl pb-8">
      <PageHeader title="Agents" count={connected} description={DESCRIPTION} />
      <div className="space-y-8 px-4 sm:px-8">
        {data.sessions.length === 0 ? (
          <p className="rounded-xl border border-dashed border-border px-4 py-3 text-sm text-muted-foreground">
            No agent has used Imprint yet. Sessions appear here when a connected client, such as Claude Code or Cursor, starts Imprint&apos;s MCP server.
          </p>
        ) : (
          <>
            <section className="space-y-2" aria-labelledby="agent-clients">
              <h2 id="agent-clients" className="text-sm font-semibold">Clients</h2>
              <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {data.clients.map((c) => <ClientCard key={c.client} client={c} />)}
              </ul>
            </section>
            <section className="space-y-2" aria-labelledby="agent-sessions">
              <h2 id="agent-sessions" className="flex items-baseline gap-2 text-sm font-semibold">
                Sessions <span className="font-normal text-muted-foreground tabular-nums">{data.sessions.length}</span>
              </h2>
              <Card size="sm" className="gap-0 py-0">
                <ul className="divide-y divide-border">
                  {data.sessions.map((s) => <SessionRow key={s.id} session={s} onOpen={() => setOpen(s)} />)}
                </ul>
              </Card>
            </section>
          </>
        )}
      </div>
      <Sheet open={!!shown} onOpenChange={(o) => { if (!o) setOpen(null); }}>
        <SheetContent className="w-full gap-0 sm:max-w-2xl">
          {shown && <SessionDetails key={shown.id} session={shown} />}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function Connected({ connected }: { connected: boolean }) {
  return (
    <span className={cn("inline-block size-2 shrink-0 rounded-full", connected ? "bg-emerald-500" : "bg-muted-foreground/40")}
      role="img" aria-label={connected ? "Connected" : "Disconnected"} />
  );
}

function ClientCard({ client }: { client: AgentClient }) {
  return (
    <li className="space-y-1 rounded-xl bg-card px-4 py-3 ring-1 ring-foreground/10" aria-label={clientName(client.client)}>
      <p className="flex items-center gap-2 text-sm font-medium">
        <Bot className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <span className="min-w-0 flex-1 truncate">{clientName(client.client)}</span>
        {client.connected > 0 && <Badge variant="secondary" className="text-[10px]">{client.connected} connected</Badge>}
      </p>
      <p className="text-xs text-muted-foreground tabular-nums">
        {plural(client.sessions, "session")} · {plural(client.calls, "call")}
        {client.errors > 0 && <span className="text-destructive"> · {plural(client.errors, "error")}</span>}
      </p>
      <p className="text-xs text-muted-foreground">Last active {ago(client.last_seen)}</p>
    </li>
  );
}

function SessionRow({ session: s, onOpen }: { session: AgentSession; onOpen: () => void }) {
  const name = clientName(s.client);
  return (
    <li>
      <button type="button" onClick={onOpen}
        aria-label={`${name} in ${folderName(s.directory)}, ${s.connected ? "connected" : "disconnected"}, ${plural(s.calls, "call")}. Show history`}
        className="flex w-full min-w-0 items-center gap-3 px-4 py-2.5 text-left text-sm outline-none hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset">
        <Connected connected={s.connected} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate font-medium">{name}</span>
            {s.client_version && <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">{s.client_version}</span>}
          </span>
          <span className="block truncate text-xs text-muted-foreground" title={s.directory ?? undefined}>
            {folderName(s.directory)}{s.last_tool && ` · last: ${s.last_tool}`}
          </span>
        </span>
        {s.workspace && <Badge variant="outline" className="hidden shrink-0 text-[10px] md:inline-flex">{s.workspace}</Badge>}
        <span className="w-20 shrink-0 text-right text-xs text-muted-foreground tabular-nums">
          {plural(s.calls, "call")}
          {s.errors > 0 && <span className="block text-destructive">{plural(s.errors, "error")}</span>}
        </span>
        <span className="hidden w-20 shrink-0 text-right text-xs text-muted-foreground sm:inline">{ago(s.last_seen)}</span>
        <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      </button>
    </li>
  );
}

/** A session's details and its calls, newest first, refreshed while it is connected. */
function SessionDetails({ session: s }: { session: AgentSession }) {
  const [calls, setCalls] = useState<AgentCall[] | null>(null);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const PAGE = 100;

  // The newest page, merged over older pages already loaded.
  useEffect(() => {
    let cancelled = false;
    let first = true;
    async function refresh() {
      try {
        const { calls: newest } = await getAgentCalls(s.id, undefined, PAGE);
        if (cancelled) return;
        const oldest = newest.at(-1)?.id ?? Infinity;
        setCalls((prev) => [...newest, ...(prev ?? []).filter((c) => c.id < oldest)]);
        if (first) setMore(newest.length === PAGE);
        first = false;
        setError(null);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    }
    void refresh();
    const timer = s.connected ? setInterval(refresh, POLL_MS) : undefined;
    return () => { cancelled = true; clearInterval(timer); };
  }, [s.id, s.connected]);

  async function older() {
    if (!calls?.length) return;
    setLoading(true);
    try {
      const { calls: page } = await getAgentCalls(s.id, calls[calls.length - 1].id, PAGE);
      setCalls([...calls, ...page]);
      setMore(page.length === PAGE);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  const time = (ts: number | null) => (ts ? new Date(ts * 1000).toLocaleString() : "—");
  return (
    <>
      <SheetHeader className="gap-1 border-b border-border pr-12">
        <SheetTitle className="flex items-center gap-2"><Connected connected={s.connected} />{clientName(s.client)}</SheetTitle>
        <SheetDescription>{s.connected ? "Connected" : `Disconnected ${ago(s.ended_at ?? s.last_seen)}`} · {plural(s.calls, "call")}</SheetDescription>
      </SheetHeader>
      <div className="flex min-h-0 flex-1 flex-col gap-4 p-4">
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
          {s.client && <><dt className="text-muted-foreground">Client</dt><dd className="font-mono text-xs leading-5">{s.client}{s.client_version && ` ${s.client_version}`}</dd></>}
          <dt className="text-muted-foreground">Folder</dt><dd className="font-mono text-xs leading-5 break-all">{s.directory ?? "—"}</dd>
          {s.workspace && <><dt className="text-muted-foreground">Workspace</dt><dd>{s.workspace}</dd></>}
          <dt className="text-muted-foreground">Started</dt><dd className="tabular-nums">{time(s.started_at)}</dd>
          <dt className="text-muted-foreground">{s.connected ? "Last call" : "Ended"}</dt><dd className="tabular-nums">{time(s.connected ? s.last_seen : s.ended_at)}</dd>
          <dt className="text-muted-foreground">Process</dt><dd className="tabular-nums">{s.pid}</dd>
        </dl>
        {error && <p role="alert" className="text-sm text-destructive">Could not load calls: {error}</p>}
        <div className="flex min-h-0 flex-1 flex-col gap-1.5">
          <h3 className="text-xs font-medium text-muted-foreground">Calls</h3>
          <ScrollArea className="min-h-40 flex-1 rounded-lg border border-border">
            {calls === null ? <div className="p-3"><Spinner /></div> : calls.length === 0 ? (
              <p className="p-3 text-sm text-muted-foreground">No tool calls yet.</p>
            ) : (
              <ol className="divide-y divide-border" aria-label="Calls">
                {calls.map((c) => <CallRow key={c.id} call={c} />)}
              </ol>
            )}
            {more && (
              <div className="p-2">
                <Button variant="ghost" size="sm" onClick={older} disabled={loading}>{loading && <Spinner />} Older calls</Button>
              </div>
            )}
          </ScrollArea>
        </div>
      </div>
    </>
  );
}

function CallRow({ call: c }: { call: AgentCall }) {
  const ok = c.ok === 1;
  const Icon = ok ? CheckCircle2 : XCircle;
  return (
    <li className="space-y-1 px-3 py-2 text-sm" aria-label={`${c.tool}, ${ok ? "succeeded" : "failed"}`}>
      <p className="flex items-center gap-2">
        <Icon className={cn("size-4 shrink-0", ok ? "text-emerald-600" : "text-destructive")} aria-hidden />
        <span className="min-w-0 flex-1 truncate font-mono text-xs font-medium">{c.tool}</span>
        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{ms(c.duration_ms)}</span>
        <span className="w-28 shrink-0 text-right text-xs text-muted-foreground tabular-nums" title={new Date(c.started_at * 1000).toLocaleString()}>
          {new Date(c.started_at * 1000).toLocaleTimeString()}
        </span>
      </p>
      {c.arguments && c.arguments !== "{}" && (
        <p className="truncate pl-6 font-mono text-[11px] text-muted-foreground" title={c.arguments}>{c.arguments}</p>
      )}
      {c.error && <p className="pl-6 font-mono text-[11px] break-words text-destructive">{c.error}</p>}
    </li>
  );
}
