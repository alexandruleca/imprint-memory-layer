"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Bot, ChevronRight, CircleAlert, Send, User } from "lucide-react";
import { toast } from "sonner";
import { Markdown } from "@/components/markdown";
import { Spinner } from "@/components/loaders";
import { Timestamp } from "@/components/timestamp";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import {
  getConversation, sendAgentMessage, type AgentMessage, type ConversationEntry, type LiveAgent,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { clientName, messageState } from "./format";

/** How often an open panel asks for what was written since. */
const POLL_MS = 2500;

type Row =
  | { type: "entry"; at: number; entry: ConversationEntry; result?: ConversationEntry }
  | { type: "message"; at: number; message: AgentMessage };

/** The conversation's entries and the person's messages, in time order; each
 * tool call carries its result, and results are not shown on their own. */
function rows(entries: ConversationEntry[], messages: AgentMessage[]): Row[] {
  const results = new Map(entries.filter((e) => e.kind === "result" && e.call).map((e) => [e.call!, e]));
  const shown: Row[] = [];
  let last = 0;
  for (const entry of entries) {
    if (entry.kind === "result") continue;
    last = entry.at ?? last;
    shown.push({ type: "entry", at: last, entry, result: entry.kind === "tool" && entry.call ? results.get(entry.call) : undefined });
  }
  for (const message of messages) shown.push({ type: "message", at: message.sent_at, message });
  // Stable: entries keep their order; a message goes after what was written before it was sent.
  return shown.map((row, i) => ({ row, i })).sort((a, b) => a.row.at - b.row.at || a.i - b.i).map(({ row }) => row);
}

/** One agent's whole conversation, read from its transcript while this panel
 * is open, with the person's messages and how far each got (ADR 0244). */
export function ConversationPanel({ agent, now }: { agent: LiveAgent; now: Date }) {
  const [entries, setEntries] = useState<ConversationEntry[]>([]);
  const [messages, setMessages] = useState<AgentMessage[]>(agent.messages ?? []);
  const [loaded, setLoaded] = useState(false);
  const [transcript, setTranscript] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [earlier, setEarlier] = useState<{ start: number; more: boolean }>({ start: 0, more: false });
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const next = useRef<number | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const name = agent.name || clientName(agent.client);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const add = (found: ConversationEntry[]) =>
      setEntries((current) => {
        const known = new Set(current.map((e) => e.id));
        return [...current, ...found.filter((e) => !known.has(e.id))];
      });
    async function poll() {
      try {
        const part = await getConversation(agent.id, next.current === null ? {} : { after: next.current });
        if (!alive) return;
        if (next.current === null) setEarlier({ start: part.start, more: part.more_before });
        next.current = part.next;
        setTranscript(part.transcript);
        setMessages(part.messages);
        add(part.entries);
        setLoaded(true);
        setError(null);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
      if (alive) timer = setTimeout(poll, POLL_MS);
    }
    void poll();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [agent.id]);

  // Stay at the bottom while the reader is there; leave them where they scrolled to otherwise.
  useEffect(() => {
    const box = scroller.current;
    if (box && pinned.current) box.scrollTop = box.scrollHeight;
  }, [entries, messages]);

  async function loadEarlier() {
    setLoadingEarlier(true);
    try {
      const part = await getConversation(agent.id, { before: earlier.start });
      setEntries((current) => {
        const known = new Set(current.map((e) => e.id));
        return [...part.entries.filter((e) => !known.has(e.id)), ...current];
      });
      setEarlier({ start: part.start, more: part.more_before });
    } catch (e) {
      toast.error("Could not load earlier", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setLoadingEarlier(false);
    }
  }

  return (
    <>
      <SheetHeader className="gap-1 border-b border-border pr-12">
        <SheetTitle>{name}</SheetTitle>
        <SheetDescription className="text-xs">
          {clientName(agent.client)}{agent.task ? ` · ${agent.task.id} ${agent.task.title}` : ""}. Read from its transcript on this device while
          this is open; thinking is left out and secrets are redacted.
        </SheetDescription>
      </SheetHeader>
      <div ref={scroller} className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4" aria-label={`Conversation with ${name}`}
        role="log" onScroll={(e) => {
          const box = e.currentTarget;
          pinned.current = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
        }}>
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        {!loaded && !error && <div className="flex justify-center py-8"><Spinner /></div>}
        {loaded && !transcript && (
          <p className="text-sm text-muted-foreground">Its transcript is not found yet, so only your messages show.</p>
        )}
        {earlier.more && (
          <Button variant="ghost" size="sm" className="self-center text-muted-foreground" disabled={loadingEarlier} onClick={() => void loadEarlier()}>
            Load earlier
          </Button>
        )}
        {rows(entries, messages).map((row) => row.type === "message"
          ? <MessageRow key={`m-${row.message.id}`} message={row.message} now={now} />
          : <EntryRow key={row.entry.id} entry={row.entry} result={row.result} now={now} />)}
      </div>
      <Composer agent={agent} name={name} onSent={(message) => { pinned.current = true; setMessages((m) => [...m, message]); }} />
    </>
  );
}

function When({ at, now }: { at: number | null; now: Date }) {
  return at ? <Timestamp seconds={at} now={now} className="text-[11px] text-muted-foreground" /> : null;
}

function EntryRow({ entry, result, now }: { entry: ConversationEntry; result?: ConversationEntry; now: Date }) {
  if (entry.kind === "prompt") {
    return (
      <article className="ml-6 grid gap-1 self-end rounded-xl bg-primary/10 px-3 py-2 sm:ml-16" aria-label="Prompt">
        <p className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground"><User className="size-3" aria-hidden /> Prompt <When at={entry.at} now={now} /></p>
        <p className="text-sm break-words whitespace-pre-wrap">{entry.text}</p>
      </article>
    );
  }
  if (entry.kind === "reply") {
    return (
      <article className="mr-6 grid gap-1 sm:mr-16" aria-label="Reply">
        <p className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground"><Bot className="size-3" aria-hidden /> Reply <When at={entry.at} now={now} /></p>
        <Markdown text={entry.text ?? ""} className="text-sm break-words" />
      </article>
    );
  }
  if (entry.kind === "context") {
    return (
      <Folded label={entry.origin === "peer" ? "A message delivered to its inbox" : "Context its client added"} now={now} at={entry.at}>
        <pre className="text-xs break-words whitespace-pre-wrap">{entry.text}</pre>
      </Folded>
    );
  }
  return (
    <Folded label={entry.label ?? entry.tool ?? "Tool"} detail={entry.detail} now={now} at={entry.at}
      state={result ? (result.error ? "error" : "done") : "running"}>
      {entry.input && <><p className="text-[11px] font-medium text-muted-foreground">{entry.tool}</p>
        <pre className="text-xs break-words whitespace-pre-wrap">{entry.input}</pre></>}
      {result?.text && <><p className="mt-2 text-[11px] font-medium text-muted-foreground">Result</p>
        <pre className="text-xs break-words whitespace-pre-wrap">{result.text}</pre></>}
    </Folded>
  );
}

/** A tool call or added context, one line until opened. */
function Folded({ label, detail, state, now, at, children }: {
  label: string; detail?: string; state?: "running" | "done" | "error"; now: Date; at: number | null; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className="rounded-lg border border-border bg-muted/40 text-xs">
      <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}
        className="flex w-full min-w-0 items-center gap-1.5 px-2.5 py-1.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/50">
        <ChevronRight className={cn("size-3 shrink-0 transition-transform", open && "rotate-90")} aria-hidden />
        <span className="shrink-0 font-medium">{label}</span>
        {detail && <span className="min-w-0 truncate font-mono text-muted-foreground">{detail}</span>}
        <span className="ml-auto flex shrink-0 items-center gap-1.5 pl-2">
          {state === "error" && <CircleAlert className="size-3 text-destructive" aria-label="Failed" />}
          {state === "running" && <span className="text-amber-700 dark:text-amber-300">running</span>}
          <When at={at} now={now} />
        </span>
      </button>
      {open && <div id={id} className="max-h-80 overflow-y-auto border-t border-border px-2.5 py-2">{children}</div>}
    </div>
  );
}

function MessageRow({ message, now }: { message: AgentMessage; now: Date }) {
  const warn = message.state === "held" || message.state === "refused" || message.state === "failed";
  return (
    <article className="ml-6 grid gap-1 self-end rounded-xl border border-primary/30 bg-card px-3 py-2 sm:ml-16" aria-label="Your message">
      <p className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
        <Send className="size-3" aria-hidden /> You, from the dashboard <When at={message.sent_at} now={now} />
      </p>
      <p className="text-sm break-words whitespace-pre-wrap">{message.text}</p>
      <p className={cn("text-[11px] text-muted-foreground", warn && "text-amber-700 dark:text-amber-300")} data-testid="message-state">
        {messageState(message)}{message.seen_at ? " · seen" : ""}{message.task ? ` · also on ${message.task}` : ""}
      </p>
    </article>
  );
}

function Composer({ agent, name, onSent }: { agent: LiveAgent; name: string; onSent: (message: AgentMessage) => void }) {
  const id = useId();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  async function send() {
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      const { message } = await sendAgentMessage(agent.id, text);
      onSent(message);
      setText("");
    } catch (e) {
      toast.error("Not sent", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="grid gap-2 border-t border-border p-3" onSubmit={(e) => { e.preventDefault(); void send(); }}>
      <label htmlFor={id} className="sr-only">Message to {name}</label>
      <Textarea id={id} value={text} onChange={(e) => setText(e.target.value)} rows={2} maxLength={4000} placeholder={`Message ${name}`}
        onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void send(); } }} />
      <div className="flex items-center gap-2">
        <p className="text-[11px] text-muted-foreground">It hears it on its next Imprint call. Ctrl+Enter sends.</p>
        <Button type="submit" size="sm" className="ml-auto" disabled={busy || !text.trim()}><Send aria-hidden /> Send</Button>
      </div>
    </form>
  );
}
