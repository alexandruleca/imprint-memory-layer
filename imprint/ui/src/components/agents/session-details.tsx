"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, XCircle } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Spinner } from "@/components/loaders";
import { Timestamp, absoluteTime, clockTime, dayLabel, useNow } from "@/components/timestamp";
import { getAgentCalls, type AgentCall, type AgentSession } from "@/lib/api";
import { groupByTime } from "@/lib/time-groups";
import { cn } from "@/lib/utils";
import { clientName, folderName, ms, plural, sessionLength } from "./format";
import { LoadMore } from "./load-more";
import { StatusDot } from "./status";

const PAGE = 100;
const POLL_MS = 3000;

/** A session's details and its calls, newest first, refreshed while it is
 * connected. New calls join the top without dropping older pages. */
export function SessionDetails({ session: initial }: { session: AgentSession }) {
  const now = useNow(1000);
  const [session, setSession] = useState(initial);
  const [calls, setCalls] = useState<AgentCall[] | null>(null);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [olderFailed, setOlderFailed] = useState(false);
  const s = session;

  useEffect(() => {
    let cancelled = false;
    let first = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function refresh() {
      try {
        const { session: fresh, calls: newest } = await getAgentCalls(initial.id, undefined, PAGE);
        if (cancelled) return;
        const oldest = newest.at(-1)?.id ?? Infinity;
        setCalls((prev) => [...newest, ...(prev ?? []).filter((c) => c.id < oldest)]);
        if (first) setMore(newest.length === PAGE);
        first = false;
        setSession(fresh);
        setError(null);
        if (fresh.connected) timer = setTimeout(refresh, POLL_MS);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        timer = setTimeout(refresh, POLL_MS * 3);
      }
    }
    void refresh();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [initial.id]);

  async function older() {
    if (!calls?.length || loading) return;
    setLoading(true);
    try {
      const { calls: page } = await getAgentCalls(s.id, calls[calls.length - 1].id, PAGE);
      setCalls((prev) => [...(prev ?? []), ...page.filter((c) => !prev?.some((p) => p.id === c.id))]);
      setMore(page.length === PAGE);
      setOlderFailed(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setOlderFailed(true);
    } finally {
      setLoading(false);
    }
  }

  const groups = calls ? groupByTime(calls, (c) => c.started_at * 1000, { now }) : [];
  const stats: [string, React.ReactNode][] = [
    ["Calls", s.calls.toLocaleString()],
    ["Errors", <span key="e" className={cn(s.errors > 0 && "text-destructive")}>{s.errors.toLocaleString()}</span>],
    [s.connected ? "Running for" : "Ran for", sessionLength(s, now)],
    [s.connected ? "Last call" : "Ended", <Timestamp key="t" seconds={s.connected ? s.last_seen : (s.ended_at ?? s.last_seen)} now={now} />],
  ];
  return (
    <>
      <SheetHeader className="gap-1 border-b border-border pr-12">
        <SheetTitle className="flex min-w-0 items-center gap-2">
          <StatusDot connected={s.connected} />
          <span className="truncate">{folderName(s.directory)}</span>
        </SheetTitle>
        <SheetDescription>
          {clientName(s.client)}{s.client_version && ` ${s.client_version}`} · {s.connected ? "Running" : "Finished"}
        </SheetDescription>
      </SheetHeader>
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {stats.map(([label, value]) => (
            <div key={label} className="rounded-lg bg-muted/60 px-3 py-2">
              <dt className="text-[11px] font-medium text-muted-foreground">{label}</dt>
              <dd className="text-sm font-semibold tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
          {s.client && <><dt className="text-muted-foreground">Client</dt><dd className="font-mono text-xs leading-5 break-all">{s.client}{s.client_version && ` ${s.client_version}`}</dd></>}
          <dt className="text-muted-foreground">Folder</dt><dd className="font-mono text-xs leading-5 break-all">{s.directory ?? "—"}</dd>
          {s.workspace && <><dt className="text-muted-foreground">Workspace</dt><dd>{s.workspace}</dd></>}
          <dt className="text-muted-foreground">Started</dt><dd className="tabular-nums">{absoluteTime(s.started_at)}</dd>
          {!s.connected && s.ended_at && <><dt className="text-muted-foreground">Ended</dt><dd className="tabular-nums">{absoluteTime(s.ended_at)}</dd></>}
          <dt className="text-muted-foreground">Process</dt><dd className="tabular-nums">{s.pid}</dd>
        </dl>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>Could not load calls: {error}</AlertDescription>
          </Alert>
        )}
        <section aria-labelledby="session-calls" className="flex flex-col gap-1.5">
          <h3 id="session-calls" className="flex items-baseline gap-2 text-xs font-medium text-muted-foreground">
            Calls {s.connected && <span className="font-normal">· updates live</span>}
          </h3>
          <div className="rounded-lg border border-border">
            {calls === null ? <div className="flex items-center gap-2 p-3 text-sm text-muted-foreground"><Spinner /> Loading calls…</div> : calls.length === 0 ? (
              <p className="p-3 text-sm text-muted-foreground">No tool calls yet.</p>
            ) : (
              groups.map((g) => (
                <div key={g.key}>
                  {groups.length > 1 && (
                    <h4 className="sticky top-0 z-10 flex items-baseline justify-between border-y border-border bg-muted px-3 py-1 text-[11px] font-semibold tracking-wider text-muted-foreground uppercase first:border-t-0">
                      {g.label}<span className="font-normal tabular-nums normal-case">{plural(g.items.length, "call")}</span>
                    </h4>
                  )}
                  <ol aria-label={groups.length > 1 ? `Calls, ${g.label}` : "Calls"} className="divide-y divide-border">
                    {g.items.map((c) => <CallRow key={c.id} call={c} group={g.key} now={now} />)}
                  </ol>
                </div>
              ))
            )}
          </div>
          {calls !== null && calls.length > 0 && (
            <LoadMore hasMore={more} loading={loading} failed={olderFailed} onLoad={older} label="Load older calls"
              status={more ? `${calls.length.toLocaleString()} of ${s.calls.toLocaleString()} calls` : `All ${plural(calls.length, "call")} shown`} />
          )}
        </section>
      </div>
    </>
  );
}

function CallRow({ call: c, group, now }: { call: AgentCall; group: string; now: Date }) {
  const ok = c.ok === 1;
  const Icon = ok ? CheckCircle2 : XCircle;
  const recent = group === "today" || group === "yesterday";
  const when = recent ? clockTime(c.started_at) : `${dayLabel(c.started_at, now)} ${clockTime(c.started_at)}`;
  return (
    <li className="space-y-1 px-3 py-2 text-sm" aria-label={`${c.tool}, ${ok ? "succeeded" : "failed"}`}>
      <p className="flex items-center gap-2">
        <Icon className={cn("size-4 shrink-0", ok ? "text-success" : "text-destructive")} aria-hidden />
        <span className="min-w-0 flex-1 truncate font-mono text-xs font-medium" title={c.tool}>{c.tool}</span>
        <span className="shrink-0 text-xs text-muted-foreground tabular-nums" title="Duration">{ms(c.duration_ms)}</span>
        <Timestamp seconds={c.started_at} now={now} text={when} className="shrink-0 text-right text-xs text-muted-foreground tabular-nums" />
      </p>
      {c.arguments && c.arguments !== "{}" && (
        <p className="truncate pl-6 font-mono text-[11px] text-muted-foreground" title={c.arguments}>{c.arguments}</p>
      )}
      {c.error && <p className="pl-6 font-mono text-[11px] break-words text-destructive">{c.error}</p>}
    </li>
  );
}
