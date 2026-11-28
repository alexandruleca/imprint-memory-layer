"use client";

import { useRef, useState } from "react";
import { Bot, ChevronDown, Square, TerminalSquare } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Timestamp } from "@/components/timestamp";
import { getDispatch, setConfigValue, setDispatch, stopDispatched, type DispatchRole, type DispatchRun, type DispatchStatus } from "@/lib/api";
import { useCached } from "@/lib/use-cached";
import { cn } from "@/lib/utils";

const OUTCOMES: Record<string, { label: string; tone: string }> = {
  review: { label: "reached Review", tone: "text-emerald-700 dark:text-emerald-300" },
  passed: { label: "review passed: Done", tone: "text-emerald-700 dark:text-emerald-300" },
  returned: { label: "review sent it back", tone: "text-amber-700 dark:text-amber-300" },
  refined: { label: "refined: still in Backlog", tone: "text-emerald-700 dark:text-emerald-300" },
  failed: { label: "failed", tone: "text-destructive" },
  stopped: { label: "stopped", tone: "text-muted-foreground" },
  lost: { label: "another device took it", tone: "text-muted-foreground" },
  refused: { label: "not started", tone: "text-muted-foreground" },
};

/** The roles a device's Dispatcher can take on, each its own checkbox (ADR 0237), in the order a pass takes them. */
const ROLES: { role: DispatchRole; label: string; doing: string; hint: string }[] = [
  { role: "review", label: "Review", doing: "reviews tasks in Review",
    hint: "Reviews tasks in Review, with another client than the one that did the work where one is available." },
  { role: "work", label: "Work", doing: "takes over Stale tasks and starts Ready ones",
    hint: "Takes over Stale tasks and starts Ready ones while nobody works on them, each in a worktree of its own." },
  { role: "refine", label: "Refine Backlog", doing: "refines Backlog tasks when nothing else waits",
    hint: "When nothing waits for review or work, an agent adds acceptance criteria and detail to Backlog tasks that have none. They stay in Backlog for you to move." },
];

/** The roles on, from an API that may predate them (ADR 0237): its on switch meant Work and Review. */
function rolesOf(data: DispatchStatus): DispatchRole[] {
  if (data.roles) return data.roles;
  return [...(data.on ? (["work", "review"] as DispatchRole[]) : []), ...(data.refine ? (["refine"] as DispatchRole[]) : [])];
}

/** What the roles on make this device do, in a sentence. */
function summary(roles: DispatchRole[], autonomy: DispatchStatus["autonomy"]): string {
  if (roles.length === 0) return "Off. Tick what this device should do while nobody works on the board's tasks.";
  const doing = ROLES.filter((r) => roles.includes(r.role)).map((r) => r.doing);
  const said = doing.length > 1 ? `${doing.slice(0, -1).join(", ")} and ${doing[doing.length - 1]}` : doing[0];
  const note = roles.includes("work") && autonomy === "off" ? " Agents take nothing unasked in this workspace, so Work starts nothing." : "";
  return `On for this device: ${said}, one at a time, each in a worktree of its own.${note}`;
}

/**
 * This device's Dispatcher for the workspace on show (ADR 0220): its roles (ADR 0237),
 * the Dispatched sessions running with a Stop each, what it would take next,
 * the other devices whose Dispatcher is on, and the last results. Only the
 * person switches it; agents have no tool for it.
 */
/** The clients the Dispatcher may start here: tick or untick each, and choose the default (ADR 0222). */
function ClientsPicker({ data, onChanged }: { data: DispatchStatus; onChanged: () => void }) {
  // Ticks and the default are per client, wherever it was found; the list shows each place.
  const allowed = [...new Map(data.clients.filter((c) => c.allowed).map((c) => [c.client, c])).values()];
  const chosen = data.clients.find((c) => c.allowed && c.default && !c.where) ?? allowed.find((c) => c.default) ?? allowed[0];
  const places = [...new Set(data.clients.map((c) => c.where))];
  async function save(key: string, value: string, done: string) {
    try { await setConfigValue(key, value); onChanged(); toast.success(done); }
    catch (e) { toast.error("Not changed", { description: e instanceof Error ? e.message : String(e) }); }
  }
  function tick(client: string, on: boolean) {
    // From the saved list, so unticking one client keeps the exclusion of another this device cannot start right now.
    const off = new Set(data.excluded ?? data.clients.filter((c) => c.available && !c.allowed).map((c) => c.client));
    if (on) off.delete(client); else off.add(client);
    void save("dispatch.excluded_clients", [...off].join(","), on ? "Client allowed" : "Client unticked");
  }
  return (
    <Popover>
      <PopoverTrigger render={<Button variant="outline" size="xs" aria-label="Clients the Dispatcher may start" />}>
        <TerminalSquare aria-hidden /> {allowed.length ? allowed.map((c) => c.label).join(", ") : "No client"}
      </PopoverTrigger>
      <PopoverContent align="start" className="grid w-96 max-w-[calc(100vw-2rem)] gap-3 text-xs">
        <p className="text-muted-foreground">
          Clients this device can start unattended: each runs in its own sandbox or checks each action, never with its
          safety switched off. A task assigned to a client goes to that client.
        </p>
        {places.map((where) => (
          <div key={where ?? "here"} className="grid gap-2">
            {places.length > 1 && <div className="font-medium text-muted-foreground">{where ? `In WSL: ${where}` : "On this system"}</div>}
            <ul className="grid gap-2" aria-label={where ? `Clients in ${where}` : "Clients on this system"}>
              {data.clients.filter((c) => c.where === where).map((c) => (
                <li key={`${where}:${c.client}`} className="flex items-start gap-2">
                  <Checkbox aria-label={`Allow ${c.label}${where ? ` in ${where}` : ""}`} checked={c.allowed} disabled={!c.available}
                    onCheckedChange={(on) => tick(c.client, on === true)} />
                  <div className="min-w-0">
                    <div className="font-medium">{c.label}{c.version && <span className="ml-1 font-normal text-muted-foreground">{c.version}</span>}</div>
                    {!c.available && c.reason && <div className="text-muted-foreground">{c.reason}</div>}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ))}
        {allowed.length > 1 && (
          <label className="flex flex-wrap items-center gap-2">
            <span>For tasks assigned to no client:</span>
            <Select items={allowed.map((c) => ({ value: c.client, label: c.label }))} value={chosen?.client ?? null}
              onValueChange={(next) => { if (next && next !== chosen?.client) void save("dispatch.default_client", next, "Default client changed"); }}>
              <SelectTrigger size="sm" className="h-7 text-xs" aria-label="Default client"><SelectValue /></SelectTrigger>
              <SelectContent>{allowed.map((c) => <SelectItem key={c.client} value={c.client}>{c.label}</SelectItem>)}</SelectContent>
            </Select>
          </label>
        )}
      </PopoverContent>
    </Popover>
  );
}

export function DispatcherStrip({ now, onOpen }: { now: Date; onOpen: (task: string) => void }) {
  // The clients are checked again when the strip first opens, then hourly by the server (ADR 0222).
  const checked = useRef(false);
  const status = useCached<DispatchStatus>("tasks-dispatch", () => {
    const refresh = !checked.current;
    checked.current = true;
    return getDispatch(refresh);
  }, { refreshInterval: 5000 });
  const [saving, setSaving] = useState(false);
  const data = status.data;
  if (!data) return null;

  const roles = rolesOf(data);

  async function toggleRole(role: DispatchRole, on: boolean) {
    setSaving(true);
    const next = on ? [...roles.filter((r) => r !== role), role] : roles.filter((r) => r !== role);
    const label = ROLES.find((r) => r.role === role)?.label ?? role;
    try {
      await setDispatch({ roles: next });
      await status.refresh();
      toast.success(on ? `${label} on for this device` : `${label} off${role === "refine" ? "" : ": running sessions finish"}`);
    } catch (e) {
      toast.error("Not changed", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  }

  async function stop(run: DispatchRun) {
    try {
      await stopDispatched(run.id);
      void status.refresh();
      toast.success(`Stopped the session on ${run.task}`);
    } catch (e) {
      toast.error("Not stopped", { description: e instanceof Error ? e.message : String(e) });
    }
  }

  const ended = data.recent.filter((run) => run.ended_at !== null);
  return (
    <section aria-labelledby="dispatcher-title" data-tour="board-dispatcher"
      className="mx-4 grid gap-2 rounded-lg border bg-card px-3 py-2 text-sm sm:mx-8">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Bot aria-hidden className="size-4 text-muted-foreground" />
        <span id="dispatcher-title" className="font-medium">Dispatcher</span>
        <div role="group" aria-labelledby="dispatcher-title" className="inline-flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          {ROLES.map((r) => (
            <label key={r.role} className="inline-flex items-center gap-1.5" title={r.hint}>
              <Checkbox aria-label={r.label} checked={roles.includes(r.role)} disabled={saving}
                onCheckedChange={(on) => void toggleRole(r.role, on === true)} />
              {r.label}
            </label>
          ))}
        </div>
        <span className="text-xs text-muted-foreground">{summary(roles, data.autonomy)}</span>
        <ClientsPicker data={data} onChanged={() => void status.refresh()} />
        {data.devices.length > 0 && (
          <span className="text-xs text-muted-foreground">
            Also on: {data.devices.map((d) => d.roles?.length ? `${d.name} (${d.roles.join(", ")})` : d.name).join("; ")}
          </span>
        )}
      </div>

      {data.running.map((run) => (
        <div key={run.id} className="flex flex-wrap items-center gap-2 text-xs">
          <span className="relative flex size-2"><span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-500 opacity-60" /><span className="relative inline-flex size-2 rounded-full bg-emerald-500" /></span>
          <Button variant="link" size="sm" className="h-auto px-0 text-xs font-mono" onClick={() => onOpen(run.task)}>{run.task}</Button>
          <span className="min-w-0 truncate">{run.title}</span>
          <Badge variant="secondary" className="font-normal">{run.role === "work" ? run.client : `${run.client} · ${run.role}`}</Badge>
          <span className="text-muted-foreground">since <Timestamp seconds={run.started_at} now={now} /></span>
          <Button variant="outline" size="xs" className="ml-auto" onClick={() => void stop(run)} aria-label={`Stop the session on ${run.task}`}>
            <Square aria-hidden /> Stop
          </Button>
        </div>
      ))}

      {roles.length > 0 && data.held && (
        <p className="text-xs text-amber-700 dark:text-amber-300">Starting nothing now: {data.held}.</p>
      )}

      {roles.length > 0 && !data.held && data.running.length === 0 && (
        <p className="text-xs text-muted-foreground">
          {data.next.length > 0
            ? <>Next: <Button variant="link" size="sm" className="h-auto px-0 text-xs font-mono" onClick={() => onOpen(data.next[0].id)}>{data.next[0].id}</Button> {data.next[0].title}, {data.next[0].role === "refine" ? "to refine with" : "for"} {data.clients.find((c) => c.client === data.next[0].client)?.label ?? data.next[0].client}</>
            : "Nothing to take for its roles right now."}
        </p>
      )}

      {ended.length > 0 && (
        <Collapsible>
          <CollapsibleTrigger render={<Button variant="link" size="sm" className="h-auto gap-1 px-0 text-xs text-muted-foreground" />}>
            <ChevronDown aria-hidden className="size-3" /> Recent results ({ended.length})
          </CollapsibleTrigger>
          <CollapsibleContent>
            <ul className="mt-1 grid gap-1 text-xs" aria-label="Recent Dispatched sessions">
              {ended.map((run) => {
                const outcome = OUTCOMES[run.outcome ?? ""] ?? { label: run.outcome ?? "ended", tone: "" };
                return (
                  <li key={run.id} className="flex flex-wrap items-center gap-2">
                    <Button variant="link" size="sm" className="h-auto px-0 text-xs font-mono" onClick={() => onOpen(run.task)}>{run.task}</Button>
                    <span className="min-w-0 truncate">{run.title}</span>
                    <Badge variant="outline" className="font-normal">{run.client}</Badge>
                    <span className={cn("font-medium", outcome.tone)} title={run.detail ?? undefined}>{outcome.label}</span>
                    <Timestamp seconds={run.ended_at ?? run.started_at} now={now} className="text-muted-foreground" />
                  </li>
                );
              })}
            </ul>
          </CollapsibleContent>
        </Collapsible>
      )}
    </section>
  );
}
