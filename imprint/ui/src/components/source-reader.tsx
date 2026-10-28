"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Brain, ChevronDown, ChevronRight, ChevronUp, FileText, Info, MessagesSquare, TriangleAlert, Wrench } from "lucide-react";
import { Markdown } from "@/components/markdown";
import { Spinner } from "@/components/loaders";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { readSource } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { SourceBlock, SourceRead } from "@/lib/types";

/** What to read: a source, and optionally the topic to mark in it or the
 * chunk to open at. */
export type ReaderTarget = {
  source: string;
  /** Needed when the source exists in more than one project; null is the empty project. */
  project?: string | null;
  topic?: string;
  chunk?: number;
  /** Shown while the source loads. */
  title?: string;
};

/** A whole source read from its snapshot: a conversation as its exchange,
 * with tool calls and thinking folded between the messages, or a file's
 * text. Where the reader was opened for a topic, the passages it tagged are
 * marked and stepped through. */
export function SourceReader({ target, onClose }: { target: ReaderTarget | null; onClose: () => void }) {
  if (!target) return null;
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="flex h-[90dvh] flex-col gap-0 p-0 sm:max-w-4xl">
        <Reader key={`${target.project}\u0000${target.source}\u0000${target.topic}\u0000${target.chunk}`} target={target} />
      </DialogContent>
    </Dialog>
  );
}

type Step = { key: string; block: SourceBlock; output?: SourceBlock; match: boolean };
type Item =
  | { type: "message"; key: string; block: SourceBlock; match: boolean; chunks: number[] }
  | { type: "activity"; key: string; steps: Step[]; match: boolean; chunks: number[] }
  | { type: "text"; key: string; block: SourceBlock; match: boolean; chunks: number[] };

/** Messages stand on their own; the events between two of them (tool calls
 * with their output, thinking, injected context) fold into one activity. */
function arrange(data: SourceRead): Item[] {
  if (data.kind !== "conversation") {
    return data.blocks.map((block) => ({ type: "text", key: `b${block.start}`, block, match: block.match, chunks: block.chunks }));
  }
  const items: Item[] = [];
  const calls = new Map<string, { step: Step; group: Extract<Item, { type: "activity" }> }>();
  let group: Extract<Item, { type: "activity" }> | null = null;
  for (const block of data.blocks) {
    if (block.kind === "message" && (block.role === "user" || block.role === "assistant")) {
      group = null;
      items.push({ type: "message", key: `e${block.event}`, block, match: block.match, chunks: block.chunks });
      continue;
    }
    const call = block.kind === "tool_output" && block.call_id ? calls.get(block.call_id) : undefined;
    if (call) {
      call.step.output = block;
      call.step.match ||= block.match;
      call.group.match ||= block.match;
      call.group.chunks.push(...block.chunks);
      continue;
    }
    if (!group) {
      group = { type: "activity", key: `a${block.event}`, steps: [], match: false, chunks: [] };
      items.push(group);
    }
    const step: Step = { key: `e${block.event}`, block, match: block.match };
    group.steps.push(step);
    group.match ||= block.match;
    group.chunks.push(...block.chunks);
    if (block.kind === "tool_call" && block.call_id) calls.set(block.call_id, { step, group });
  }
  return items;
}

function Reader({ target }: { target: ReaderTarget }) {
  const [state, setState] = useState<{ data?: SourceRead; error?: string }>({});
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [current, setCurrent] = useState(-1);
  const refs = useRef(new Map<string, HTMLElement>());

  useEffect(() => {
    const controller = new AbortController();
    readSource({ source: target.source, project: target.project, topic: target.topic }, controller.signal)
      .then((data) => setState({ data }))
      .catch((e) => { if (!controller.signal.aborted) setState({ error: e instanceof Error ? e.message : String(e) }); });
    return () => controller.abort();
  }, [target]);

  const data = state.data;
  const items = useMemo(() => (data ? arrange(data) : []), [data]);
  // Neighbouring marked items come from the same chunks: one passage.
  const passages = useMemo(() => {
    const runs: number[][] = [];
    items.forEach((item, i) => {
      if (!item.match) return;
      const run = runs[runs.length - 1];
      if (run && run[run.length - 1] === i - 1) run.push(i);
      else runs.push([i]);
    });
    return runs;
  }, [items]);

  // Open at the first marked passage, or at the chunk asked for.
  const [landed, setLanded] = useState(false);
  const [scrollTo, setScrollTo] = useState<{ key: string; n: number } | null>(null);
  if (data && !landed) {
    setLanded(true);
    const start = target.chunk !== undefined
      ? items.findIndex((item) => item.chunks.includes(target.chunk!))
      : passages[0]?.[0] ?? -1;
    if (start >= 0) {
      const run = passages.find((r) => r.includes(start));
      setCurrent(run ? passages.indexOf(run) : -1);
      setOpen(new Set((run ?? [start]).map((i) => items[i].key)));
      setScrollTo({ key: items[start].key, n: 0 });
    }
  }
  useEffect(() => {
    if (scrollTo) refs.current.get(scrollTo.key)?.scrollIntoView({ block: "start" });
  }, [scrollTo]);

  function go(step: 1 | -1) {
    if (!passages.length) return;
    const next = (current + step + passages.length) % passages.length;
    setCurrent(next);
    setOpen((prev) => new Set([...prev, ...passages[next].map((i) => items[i].key)]));
    setScrollTo((prev) => ({ key: items[passages[next][0]].key, n: (prev?.n ?? 0) + 1 }));
  }

  const toggle = (key: string, on: boolean) => setOpen((prev) => {
    const next = new Set(prev);
    if (on) next.add(key); else next.delete(key);
    return next;
  });
  const bind = (key: string) => (el: HTMLElement | null) => { if (el) refs.current.set(key, el); else refs.current.delete(key); };
  const conversation = data ? data.kind === "conversation" : target.source.startsWith("conversation/");
  const Icon = conversation ? MessagesSquare : FileText;
  const omitted = Object.values(data?.details.omissions ?? {}).reduce((n, v) => n + v, 0);
  const focused = new Set(current >= 0 ? passages[current] : []);

  return (
    <>
      <DialogHeader className="shrink-0 gap-2 border-b border-border p-4 pr-12">
        <div className="flex items-start gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground"><Icon className="size-4" aria-hidden /></span>
          <div className="min-w-0 flex-1 space-y-0.5">
            <DialogTitle className="line-clamp-2 leading-snug">{data?.title || target.title || target.source}</DialogTitle>
            <DialogDescription className="flex flex-wrap items-center gap-x-2 text-xs">
              <span className="font-mono">{target.source}</span>
              {(data?.project ?? target.project) && <span>· {data?.project ?? target.project}</span>}
              {data?.details.event_count !== undefined && <span>· {data.details.event_count.toLocaleString()} events</span>}
              {data && data.kind !== "conversation" && <span>· {data.total_chars.toLocaleString()} characters</span>}
            </DialogDescription>
          </div>
        </div>
        {target.topic && data && (
          <div className="flex flex-wrap items-center gap-2 text-xs" role="status">
            <span className="size-2.5 rounded-sm bg-amber-200 ring-1 ring-amber-400" aria-hidden />
            {passages.length ? (
              <>
                <span>
                  <span className="font-medium">{target.topic}</span>
                  <span className="text-muted-foreground"> · {current >= 0 ? `${current + 1} of ${passages.length}` : passages.length} {passages.length === 1 ? "passage" : "passages"}</span>
                </span>
                <span className="ml-auto flex gap-1">
                  <Button variant="outline" size="icon-xs" aria-label="Previous passage" onClick={() => go(-1)}><ChevronUp aria-hidden /></Button>
                  <Button variant="outline" size="icon-xs" aria-label="Next passage" onClick={() => go(1)}><ChevronDown aria-hidden /></Button>
                </span>
              </>
            ) : <span className="text-muted-foreground">No passage here is tagged {target.topic} any more.</span>}
          </div>
        )}
      </DialogHeader>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {state.error ? (
          <Empty className="h-full">
            <EmptyHeader>
              <EmptyMedia variant="icon"><TriangleAlert aria-hidden /></EmptyMedia>
              <EmptyTitle>The full text is not available</EmptyTitle>
              <EmptyDescription>{state.error}</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : !data ? (
          <div className="mx-auto max-w-3xl space-y-6 p-6" aria-busy="true">
            <p className="flex items-center gap-2 text-xs text-muted-foreground"><Spinner className="size-3.5" /> Loading the full {conversation ? "conversation" : "source"}…</p>
            <Skeleton className="ml-auto h-10 w-2/3 rounded-2xl" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-6 w-1/2" />
          </div>
        ) : conversation ? (
          <div className="mx-auto w-full max-w-3xl space-y-3 px-3 py-6 sm:px-5">
            {items.map((item, i) => (
              <div key={item.key} ref={bind(item.key)} data-match={item.match || undefined}
                className={cn("relative scroll-mt-4 rounded-lg py-1 pl-4",
                  item.match && "before:absolute before:inset-y-1 before:left-0 before:w-[3px] before:rounded-full before:bg-amber-400",
                  focused.has(i) && "bg-amber-50")}>
                {item.type === "message" ? <Message block={item.block} />
                  : item.type === "activity" ? <Activity steps={item.steps} open={open.has(item.key)} onOpen={(on) => toggle(item.key, on)} /> : null}
              </div>
            ))}
          </div>
        ) : (
          <pre className="p-4 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words sm:p-6">
            {items.map((item, i) => (
              <span key={item.key} ref={bind(item.key)}
                className={cn("scroll-mt-4", item.match && "rounded-sm bg-amber-100", focused.has(i) && "bg-amber-200")}>
                {item.type === "text" && item.block.text}
              </span>
            ))}
          </pre>
        )}
      </div>

      {data && (omitted > 0 || data.updated_at) && (
        <p className="shrink-0 border-t border-border px-4 py-2 text-xs text-muted-foreground">
          {data.updated_at && <>Saved {new Date(data.updated_at * 1000).toLocaleString()}. </>}
          {omitted > 0 && <>{omitted.toLocaleString()} encrypted reasoning {omitted === 1 ? "block or attachment was" : "blocks or attachments were"} not stored.</>}
        </p>
      )}
    </>
  );
}

function when(timestamp: SourceBlock["timestamp"]) {
  if (timestamp === undefined) return "";
  const date = new Date(typeof timestamp === "number" ? (timestamp < 1e12 ? timestamp * 1000 : timestamp) : timestamp);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const LONG = 1500;

function Message({ block }: { block: SourceBlock }) {
  const [all, setAll] = useState(false);
  const long = block.text.length > LONG;
  const text = long && !all ? `${block.text.slice(0, LONG)}…` : block.text;
  if (block.role === "user") {
    return (
      <div className="flex flex-col items-end gap-1">
        {block.timestamp !== undefined && <span className="text-[11px] text-muted-foreground">{when(block.timestamp)}</span>}
        <div className="max-w-[85%] rounded-2xl bg-muted px-4 py-2.5 text-sm whitespace-pre-wrap break-words">
          {text}
          {long && <ShowAll all={all} onToggle={() => setAll((v) => !v)} />}
        </div>
      </div>
    );
  }
  return (
    <div className="text-sm">
      <Markdown text={text} className="leading-relaxed break-words" />
      {long && <ShowAll all={all} onToggle={() => setAll((v) => !v)} />}
    </div>
  );
}

function ShowAll({ all, onToggle }: { all: boolean; onToggle: () => void }) {
  return (
    <Button variant="link" size="xs" className="h-auto px-0 text-xs" onClick={onToggle}>
      {all ? "Show less" : "Show all"}
    </Button>
  );
}

/** "Bash" and its input's most telling field, from a tool call's text. */
function describeCall(block: SourceBlock): { name: string; summary: string; input: string } {
  const [first, ...rest] = block.text.split("\n");
  const name = block.tool || first;
  const input = rest.join("\n");
  let summary = "";
  try {
    const args = JSON.parse(input);
    if (args && typeof args === "object") {
      const pick = ["description", "command", "file_path", "path", "pattern", "query", "url", "prompt"]
        .map((key) => args[key]).find((v) => typeof v === "string" && v.trim());
      summary = (pick ?? Object.values(args).find((v) => typeof v === "string" && v.trim()) ?? "") as string;
    }
  } catch {
    summary = input;
  }
  return { name, summary: summary.split("\n")[0].slice(0, 140), input };
}

function firstLine(text: string) {
  return text.split("\n").find((line) => line.trim())?.trim().slice(0, 140) ?? "";
}

function Activity({ steps, open, onOpen }: { steps: Step[]; open: boolean; onOpen: (open: boolean) => void }) {
  const tools = new Map<string, number>();
  let thoughts = 0;
  let other = 0;
  for (const { block } of steps) {
    if (block.kind === "tool_call") {
      const { name } = describeCall(block);
      tools.set(name, (tools.get(name) ?? 0) + 1);
    } else if (block.kind === "analysis") thoughts += 1;
    else other += 1;
  }
  const calls = [...tools.values()].reduce((n, v) => n + v, 0);
  const parts = [
    calls && `${calls} tool ${calls === 1 ? "call" : "calls"}`,
    thoughts && `${thoughts} ${thoughts === 1 ? "thought" : "thoughts"}`,
    other && `${other} context ${other === 1 ? "note" : "notes"}`,
  ].filter(Boolean);
  const names = [...tools.entries()].sort((a, b) => b[1] - a[1]).map(([name, n]) => (n > 1 ? `${name} ×${n}` : name));
  return (
    <Collapsible open={open} onOpenChange={onOpen}>
      <CollapsibleTrigger className="group/activity flex w-full min-w-0 items-center gap-2 rounded-md py-0.5 text-left text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50">
        <ChevronRight className="size-3.5 shrink-0 transition-transform group-data-[panel-open]/activity:rotate-90" aria-hidden />
        <span className="shrink-0 font-medium">{parts.join(", ")}</span>
        {names.length > 0 && <span className="truncate">· {names.slice(0, 4).join(", ")}{names.length > 4 ? ", …" : ""}</span>}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ol className="mt-2 space-y-1 border-l border-border pl-3">
          {steps.map((step) => <StepRow key={step.key} step={step} />)}
        </ol>
      </CollapsibleContent>
    </Collapsible>
  );
}

function StepRow({ step }: { step: Step }) {
  const { block, output } = step;
  let icon = Info;
  let name = "Context";
  let summary = firstLine(block.text);
  let body: React.ReactNode = <Pre text={block.text} />;
  if (block.kind === "tool_call") {
    const call = describeCall(block);
    icon = Wrench;
    name = call.name;
    summary = call.summary;
    body = (
      <>
        {call.input && <Pre label="Input" text={call.input} />}
        {output ? <Pre label="Output" text={output.text} /> : <p className="text-xs text-muted-foreground">No output was recorded.</p>}
      </>
    );
  } else if (block.kind === "analysis") {
    icon = Brain;
    name = "Thinking";
    body = <p className="rounded-lg bg-muted/40 p-3 text-xs leading-relaxed whitespace-pre-wrap text-muted-foreground italic">{block.text}</p>;
  } else if (block.kind === "tool_output") {
    icon = Wrench;
    name = "Tool output";
  }
  const Icon = icon;
  return (
    <li>
      <Collapsible>
        <CollapsibleTrigger className="group/step flex w-full min-w-0 items-center gap-2 rounded-md px-1 py-1 text-left text-xs outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50">
          <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <span className="shrink-0 font-medium">{name}</span>
          <span className="min-w-0 flex-1 truncate text-muted-foreground">{summary}</span>
          {step.match && <Badge variant="outline" className="shrink-0 border-amber-300 bg-amber-100 text-[10px] text-amber-900">match</Badge>}
          <ChevronRight className="size-3.5 shrink-0 text-muted-foreground transition-transform group-data-[panel-open]/step:rotate-90" aria-hidden />
        </CollapsibleTrigger>
        <CollapsibleContent className="space-y-2 py-2 pl-6">{body}</CollapsibleContent>
      </Collapsible>
    </li>
  );
}

function Pre({ label, text }: { label?: string; text: string }) {
  return (
    <div className="space-y-1">
      {label && <p className="text-[11px] font-medium text-muted-foreground">{label}</p>}
      <pre className="max-h-80 overflow-auto rounded-lg border border-border bg-muted/40 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words">
        {text || "(empty)"}
      </pre>
    </div>
  );
}
