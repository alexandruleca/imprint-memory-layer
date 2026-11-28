"use client";

import { useId, useState } from "react";
import {
  Bot, BookOpen, Brain, Clock, Eye, FlaskConical, Globe, Hammer, Hourglass, KanbanSquare, Library, MessageCircleQuestion,
  MessageSquare, MessagesSquare, Moon, PencilLine, RotateCw, Send, Split, Square, Terminal, type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useNow } from "@/components/timestamp";
import { getLive, sendAgentMessage, stopDispatched, type AgentMessage, type LiveAgent, type LiveReport, type LiveTokens } from "@/lib/api";
import { useUiMode } from "@/lib/ui-mode";
import { useCached } from "@/lib/use-cached";
import { cn } from "@/lib/utils";
import { ConversationPanel } from "./conversation";
import { clientName, folderName, messageState } from "./format";
import { ago } from "./board/format";
import { TaskLink } from "./board/links";

const POLL_MS = 3000;
/** An idle agent is dimmed after this long. */
const DIM_AFTER = 30 * 60;

const ACTIVITY_ICONS: Record<string, LucideIcon> = {
  editing: PencilLine, building: Hammer, testing: FlaskConical, running: Terminal, reading: BookOpen, web: Globe,
  imprint: Library, board: KanbanSquare, delegating: Split, asking: MessageCircleQuestion, thinking: Brain,
  waiting: Hourglass, idle: Moon, other: Bot,
};
const STATE_TONE: Record<LiveAgent["state"], string> = {
  needs_answer: "bg-rose-500", waiting: "bg-amber-500", working: "bg-emerald-500", idle: "bg-muted-foreground/40",
};
/** Agents that need your answer first, then those waiting for you, then working, then idle (ADR 0246). */
const STATE_ORDER: Record<LiveAgent["state"], number> = { needs_answer: 0, waiting: 1, working: 2, idle: 3 };
const ROLE_LABELS: Record<NonNullable<LiveAgent["task"]>["role"], string> = {
  working: "Working on", reviewing: "Reviewing", "taking-over": "Taking over", refining: "Refining",
};

/** 1234 → "1.2k", 48_300_000 → "48.3M". */
export function tokens(value: number): string {
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}k`;
  return `${(value / 1_000_000).toFixed(value < 10_000_000 ? 2 : 1)}M`;
}

/** "45 s", "3 min", "2 h 5 min". */
function lasting(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s} s`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
}

/** A rate-limit window in words: "weekly window", "5-hour window". */
function windowName(minutes: number | null): string {
  if (!minutes) return "window";
  if (minutes % 10080 === 0) return minutes === 10080 ? "weekly window" : `${minutes / 10080}-week window`;
  if (minutes % 1440 === 0) return minutes === 1440 ? "daily window" : `${minutes / 1440}-day window`;
  return minutes % 60 === 0 ? `${minutes / 60}-hour window` : `${minutes}-minute window`;
}

/** Idle for over :data:`DIM_AFTER`: a client left open. */
function quietFor(agent: LiveAgent, now: Date): boolean {
  return agent.state === "idle" && now.getTime() / 1000 - agent.activity.since > DIM_AFTER;
}

/** Open a Task in the Board's drawer, from any tab of the page. */
export function openTask(id: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("task", id);
  url.hash = "board";
  window.history.replaceState(null, "", url);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
  window.dispatchEvent(new Event("imprint:board-task"));
}

/** Open a session's entry in History. */
function openHistory(id: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("session", id);
  url.searchParams.delete("task");
  url.hash = "history";
  window.history.replaceState(null, "", url);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
  window.dispatchEvent(new Event("imprint:agents-session"));
}

/** Live: who is working on what on this device right now, from local
 * records and the agents' own transcripts, without any model (ADR 0233). */
export function LiveView() {
  const { simple } = useUiMode();
  const now = useNow(1000);
  const live = useCached<LiveReport>("agents-live", getLive, { refreshInterval: POLL_MS, persist: false });
  // The session whose conversation is open in the side panel (ADR 0244).
  const [talking, setTalking] = useState<string | null>(null);
  if (!live.data) {
    return (
      <div className="space-y-4 px-4 sm:px-8" aria-busy={!live.error}>
        {live.error ? (
          <Alert variant="destructive">
            <AlertTitle>Could not read the running agents</AlertTitle>
            <AlertDescription>{live.error.message}. The dashboard keeps trying every few seconds.</AlertDescription>
            <AlertAction><Button variant="outline" size="sm" onClick={() => void live.refresh()}><RotateCw aria-hidden /> Retry</Button></AlertAction>
          </Alert>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-56 rounded-xl" />)}</div>
        )}
      </div>
    );
  }
  // A partial answer (an older server, a proxy) shows as nothing running rather than failing the page.
  const data: LiveReport = { ...live.data, agents: live.data.agents ?? [], others: live.data.others ?? [], today: live.data.today ?? [],
    counts: { working: 0, waiting: 0, idle: 0, others: 0, ...(live.data.counts as Partial<LiveReport["counts"]> | undefined) } };
  const projects = [...new Set(data.agents.map((a) => a.project || ""))];
  const open = talking ? data.agents.find((a) => a.id === talking) ?? null : null;
  return (
    <div className="space-y-5 px-4 pb-10 sm:px-8">
      <Summary data={data} simple={simple} />
      {data.agents.length === 0 ? (
        <Empty className="border border-border bg-card">
          <EmptyHeader>
            <EmptyMedia variant="icon"><Bot aria-hidden /></EmptyMedia>
            <EmptyTitle>No agents are running on this device.</EmptyTitle>
            <EmptyDescription>
              An agent shows here while its client is open with Imprint connected: what it is doing, its Task and its tokens, read from
              its own transcript. Nothing here asks a model.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : projects.map((project) => {
        const agents = data.agents.filter((a) => (a.project || "") === project)
          .sort((a, b) => STATE_ORDER[a.state] - STATE_ORDER[b.state] || b.activity.since - a.activity.since);
        // Agents idle for over 30 minutes (a client left open) fold away, so those at work come first.
        const quiet = agents.filter((a) => quietFor(a, now));
        const active = agents.filter((a) => !quietFor(a, now));
        const card = (agent: LiveAgent) => <AgentCard key={agent.id} agent={agent} now={now} simple={simple} onChanged={() => void live.refresh()}
          onConversation={() => setTalking(agent.id)} />;
        return (
          <section key={project || "none"} aria-label={`${project || "No project"}, ${agents.length} ${agents.length === 1 ? "agent" : "agents"}`} className="space-y-2">
            <h2 className="flex items-baseline gap-2 text-sm font-semibold">
              {project || "No project"}
              <span className="text-xs font-normal text-muted-foreground tabular-nums">{agents.length}</span>
            </h2>
            {active.length > 0 && <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{active.map(card)}</ul>}
            {quiet.length > 0 && (
              <Collapsible>
                <CollapsibleTrigger render={<Button variant="ghost" size="sm" className="-ml-2 text-muted-foreground" />}>
                  <Moon aria-hidden /> {quiet.length} idle for over 30 minutes
                </CollapsibleTrigger>
                <CollapsibleContent><ul className="mt-2 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{quiet.map(card)}</ul></CollapsibleContent>
              </Collapsible>
            )}
          </section>
        );
      })}
      {data.others.length > 0 && <Others data={data} now={now} />}
      <Sheet open={!!open} onOpenChange={(o) => { if (!o) setTalking(null); }}>
        <SheetContent className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-2xl">
          {open && <ConversationPanel key={open.id} agent={open} now={now} />}
        </SheetContent>
      </Sheet>
      <p className="text-xs text-muted-foreground">
        Read from each agent&apos;s own transcript while this page is open; nothing is sent anywhere, and no model is asked. Commands and
        files are shortened and redacted.
      </p>
    </div>
  );
}

function Summary({ data, simple }: { data: LiveReport; simple: boolean }) {
  const chips: { key: string; count: number; label: string; tone: string }[] = [
    { key: "needs_answer", count: data.counts.needs_answer ?? 0, label: "need your answer", tone: "text-rose-700 dark:text-rose-300" },
    { key: "waiting", count: data.counts.waiting, label: "waiting for you", tone: "text-amber-700 dark:text-amber-300" },
    { key: "working", count: data.counts.working, label: "working", tone: "text-emerald-700 dark:text-emerald-300" },
    { key: "idle", count: data.counts.idle, label: "idle", tone: "text-muted-foreground" },
    { key: "others", count: data.counts.others, label: "on other devices", tone: "text-muted-foreground" },
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2" role="group" aria-label="Agents right now" data-tour="live-summary">
      <div className="flex flex-wrap gap-1.5">
        {chips.map((c) => (
          <span key={c.key} className={cn("rounded-md border border-border bg-card px-2 py-1 text-xs", c.tone, !c.count && "opacity-60")}>
            <span className="font-semibold tabular-nums">{c.count}</span> {c.label}
          </span>
        ))}
      </div>
      {!simple && data.today.length > 0 && (
        <p className="text-xs text-muted-foreground sm:ml-auto" aria-label="Tokens today on this device">
          Today:{" "}
          {data.today.map((t, i) => {
            const given = t.new_input + t.cached_input;
            return (
              <span key={t.client}>
                {i > 0 && " · "}
                {clientName(t.client)} <span className="font-medium text-foreground/80 tabular-nums">{tokens(given + t.output)}</span>
                {given > 0 && <span className="tabular-nums"> ({Math.round((100 * t.cached_input) / given)}% cached)</span>}
              </span>
            );
          })}
        </p>
      )}
    </div>
  );
}

function AgentCard({ agent, now, simple, onChanged, onConversation }: {
  agent: LiveAgent; now: Date; simple: boolean; onChanged: () => void; onConversation: () => void;
}) {
  const seconds = now.getTime() / 1000;
  const Icon = ACTIVITY_ICONS[agent.activity.kind] ?? Bot;
  const name = agent.name || clientName(agent.client);
  const dim = quietFor(agent, now);
  const [messaging, setMessaging] = useState(false);
  const [stopping, setStopping] = useState(false);
  return (
    <li data-agent={agent.id} aria-label={`${name}, ${agent.activity.label}`}
      className={cn("flex min-w-0 flex-col gap-2.5 rounded-xl border border-border bg-card p-3.5 shadow-xs transition-opacity",
        agent.state === "waiting" && "border-amber-500/40", agent.state === "needs_answer" && "border-rose-500/50", dim && "opacity-60")}>
      <div className="flex min-w-0 items-start gap-2">
        <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", STATE_TONE[agent.state], agent.state === "working" && "animate-pulse")} aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">{name}</p>
          <p className="truncate text-xs text-muted-foreground" title={agent.directory ?? undefined}>
            {agent.name ? clientName(agent.client) : folderName(agent.directory)}{agent.model ? ` · ${agent.model}` : ""}
          </p>
        </div>
        <span className="shrink-0 text-xs text-muted-foreground tabular-nums" title="Running for">
          <Clock className="mr-0.5 inline size-3" aria-hidden />{lasting(seconds - agent.started_at)}
        </span>
      </div>

      <div className={cn("flex min-w-0 items-start gap-2 rounded-lg px-2.5 py-2 text-sm",
        agent.state === "needs_answer" ? "bg-rose-500/10" : agent.state === "waiting" ? "bg-amber-500/10"
          : agent.state === "working" ? "bg-emerald-500/10" : "bg-muted/60")}>
        <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="font-medium">
            {agent.activity.label}
            <span className="font-normal text-muted-foreground">
              {agent.state === "idle" ? ` ${lasting(seconds - agent.activity.since)}` : ` · ${lasting(seconds - agent.activity.since)}`}
            </span>
          </p>
          {agent.state === "needs_answer" ? <NeedsAnswer activity={agent.activity} /> : <>
            {agent.activity.detail && <p className="truncate font-mono text-xs text-muted-foreground" title={agent.activity.detail}>{agent.activity.detail}</p>}
            {agent.activity.long && <p className="text-xs text-amber-800 dark:text-amber-200">No result for a while: it may be waiting for your approval.</p>}
          </>}
        </div>
      </div>

      {agent.task ? (
        <div className="grid min-w-0 gap-1 text-sm">
          <p className="flex min-w-0 items-center gap-1.5">
            <Badge variant="outline" className={cn("h-5 shrink-0 px-1.5 text-[11px]", agent.task.role === "reviewing" && "border-sky-500/40 text-sky-700 dark:text-sky-300")}>
              {agent.task.role === "reviewing" && <Eye aria-hidden />}{ROLE_LABELS[agent.task.role]}
            </Badge>
            <TaskLink id={agent.task.id} onOpen={openTask} className="shrink-0 text-xs" />
            <span className="min-w-0 truncate" title={agent.task.title}>{agent.task.title}</span>
          </p>
          {agent.task.next && <p className="line-clamp-2 text-xs text-muted-foreground">Next: {agent.task.next}</p>}
          {agent.task.stalling && <p className="text-xs text-amber-800 dark:text-amber-200">Stalling: no checkpoint for a while.</p>}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">No Task on the Board.</p>
      )}

      {agent.tokens && (simple ? (
        <Collapsible>
          <CollapsibleTrigger render={<Button variant="ghost" size="xs" className="-ml-2 text-muted-foreground" />}>Details</CollapsibleTrigger>
          <CollapsibleContent><TokenFigures figures={agent.tokens} /></CollapsibleContent>
        </Collapsible>
      ) : <TokenFigures figures={agent.tokens} />)}

      {agent.actions.length > 0 && (
        <ol className="grid gap-0.5 text-xs text-muted-foreground" aria-label="Last actions">
          {agent.actions.map((action, i) => {
            const ActionIcon = ACTIVITY_ICONS[action.kind] ?? Bot;
            return (
              <li key={i} className="flex min-w-0 items-center gap-1.5">
                <ActionIcon className="size-3 shrink-0" aria-hidden />
                <span className={cn("shrink-0", action.running && "text-foreground")}>{action.label}</span>
                {action.detail && <span className="min-w-0 truncate font-mono" title={action.detail}>{action.detail}</span>}
                {action.at && <span className="ml-auto shrink-0 tabular-nums">{action.running ? "now" : ago(action.at, now)}</span>}
              </li>
            );
          })}
        </ol>
      )}
      {!agent.transcript && (
        <p className="text-xs text-muted-foreground">Its transcript is not found yet, so only its Imprint calls show.</p>
      )}

      <div className="mt-auto flex flex-wrap items-center gap-1 border-t border-border pt-2">
        {agent.task && <Button variant="ghost" size="xs" onClick={() => openTask(agent.task!.id)}><KanbanSquare aria-hidden /> Open Task</Button>}
        <Button variant="ghost" size="xs" onClick={() => openHistory(agent.id)}><Clock aria-hidden /> History</Button>
        <Button variant="ghost" size="xs" onClick={onConversation}><MessagesSquare aria-hidden /> Conversation</Button>
        <Button variant="ghost" size="xs" onClick={() => setMessaging((m) => !m)} aria-expanded={messaging}>
          <MessageSquare aria-hidden /> Message
        </Button>
        {agent.dispatched && (
          <Button variant="ghost" size="xs" className="ml-auto text-destructive hover:text-destructive" onClick={() => setStopping(true)}>
            <Square aria-hidden /> Stop
          </Button>
        )}
      </div>
      {agent.messages && agent.messages.length > 0 && <SentMessages messages={agent.messages} now={now} />}
      {messaging && (
        <MessageForm agent={agent} onSent={() => { setMessaging(false); onChanged(); }} onCancel={() => setMessaging(false)} />
      )}
      {agent.dispatched && (
        <AlertDialog open={stopping} onOpenChange={setStopping}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Stop {name}?</AlertDialogTitle>
              <AlertDialogDescription>
                The Dispatcher stops this session and keeps its worktree{agent.task ? `; ${agent.task.id} goes back to the Board` : ""}.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Keep it running</AlertDialogCancel>
              <AlertDialogAction variant="destructive" onClick={async () => {
                setStopping(false);
                try { await stopDispatched(agent.dispatched!.run); toast.success(`Stopping ${name}`); onChanged(); }
                catch (e) { toast.error("Not stopped", { description: e instanceof Error ? e.message : String(e) }); }
              }}>Stop</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}
    </li>
  );
}

/** What an agent asks the person, and where to answer (ADR 0246). */
function NeedsAnswer({ activity }: { activity: LiveAgent["activity"] }) {
  return (
    <div className="grid gap-1" data-testid="needs-answer">
      {activity.question && <p className="text-sm break-words whitespace-pre-wrap">{activity.question}</p>}
      {activity.options && activity.options.length > 0 && (
        <ul className="flex flex-wrap gap-1" aria-label="Its choices">
          {activity.options.map((option) => <li key={option}><Badge variant="outline" className="h-5 font-normal">{option}</Badge></li>)}
        </ul>
      )}
      {activity.rule !== "question" && activity.hint && <p className="text-xs text-rose-800 dark:text-rose-200">{activity.hint}.</p>}
      {activity.rule === "question" && <p className="text-xs text-muted-foreground">Answer it in its terminal, or send it a message.</p>}
    </div>
  );
}

function TokenFigures({ figures }: { figures: LiveTokens }) {
  const share = figures.window ? Math.min(1, figures.context / figures.window) : null;
  return (
    <div className="grid gap-1.5 text-xs text-muted-foreground">
      <div className="flex items-center justify-between gap-2">
        <span>Context</span>
        <span className="tabular-nums text-foreground/80">
          {tokens(figures.context)}{figures.window ? ` of ${tokens(figures.window)}` : ""}
        </span>
      </div>
      {share !== null && (
        <div role="meter" aria-label="Context in use" aria-valuemin={0} aria-valuemax={figures.window ?? 0} aria-valuenow={figures.context}
          className="h-1.5 overflow-hidden rounded-full bg-muted">
          <div className={cn("h-full rounded-full", share > 0.85 ? "bg-amber-500" : "bg-primary")} style={{ width: `${share * 100}%` }} />
        </div>
      )}
      <p className="tabular-nums">
        In {tokens(figures.new_input)} new · {tokens(figures.cached_input)} cached
        {figures.cache_share !== null && ` (${Math.round(figures.cache_share * 100)}%)`} · Out {tokens(figures.output)}
        {figures.thinking > 0 && ` (${tokens(figures.thinking)} thinking)`}
        {figures.partial && <span title="The transcript is large; only its recent part was read"> · recent part</span>}
      </p>
      {figures.rate_limit && (
        <p className="tabular-nums">Rate limit: {Math.round(figures.rate_limit.used_percent)}% of the {windowName(figures.rate_limit.window_minutes)} used</p>
      )}
    </div>
  );
}

/** The person's last messages to this agent, each with how far it got. */
function SentMessages({ messages, now }: { messages: AgentMessage[]; now: Date }) {
  return (
    <ul className="grid gap-1 border-t border-border pt-2 text-xs" aria-label="Your messages">
      {messages.map((m) => (
        <li key={m.id} className="grid gap-0.5">
          <span className="line-clamp-2 break-words" title={m.text}>{m.text}</span>
          <span className={cn("text-[11px] text-muted-foreground", m.state !== "delivered" && m.state !== "waiting" && "text-amber-700 dark:text-amber-300")}>
            {messageState(m)} · {ago(m.delivered_at ?? m.sent_at, now)}{m.task && ` · also on ${m.task}`}
          </span>
        </li>
      ))}
    </ul>
  );
}

function MessageForm({ agent, onSent, onCancel }: { agent: LiveAgent; onSent: () => void; onCancel: () => void }) {
  const id = useId();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const name = agent.name ?? "the agent";
  return (
    <form className="flex gap-2" onSubmit={async (e) => {
      e.preventDefault();
      setBusy(true);
      try {
        const { message } = await sendAgentMessage(agent.id, text);
        toast.success(`Sent to ${name}`, { description: message.task ? `Also on ${message.task}. ${messageState(message)}.` : `${messageState(message)}.` });
        onSent();
      } catch (err) {
        toast.error("Not sent", { description: err instanceof Error ? err.message : String(err) });
      } finally {
        setBusy(false);
      }
    }}>
      <label htmlFor={id} className="sr-only">Message to {name}</label>
      <Input id={id} value={text} onChange={(e) => setText(e.target.value)} placeholder={`To ${name}`} maxLength={4000}
        className="h-8 text-xs" autoFocus onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); onCancel(); } }} />
      <Button type="submit" size="sm" disabled={busy || !text.trim()}><Send aria-hidden /> Send</Button>
    </form>
  );
}

function Others({ data, now }: { data: LiveReport; now: Date }) {
  return (
    <section aria-label="On other devices" className="space-y-2">
      <h2 className="text-sm font-semibold">On other devices <span className="text-xs font-normal text-muted-foreground">from the Board</span></h2>
      <ul className="divide-y divide-border rounded-xl border border-border bg-card text-sm">
        {data.others.map((other) => (
          <li key={`${other.device}-${other.task}`} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 px-3 py-2">
            <span className="font-medium">{other.name || clientName(other.client)}</span>
            <span className="text-xs text-muted-foreground">{clientName(other.client)} · {other.device}</span>
            <TaskLink id={other.task} onOpen={openTask} className="text-xs" />
            <span className="min-w-0 flex-1 truncate" title={other.title}>{other.title}</span>
            {other.progress_at && <span className="text-xs text-muted-foreground tabular-nums">{ago(other.progress_at, now)}</span>}
            {other.next && <p className="basis-full truncate text-xs text-muted-foreground">Next: {other.next}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}
