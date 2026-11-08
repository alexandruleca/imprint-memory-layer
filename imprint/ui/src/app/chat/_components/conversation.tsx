"use client";

import { Fragment, useState } from "react";
import Link from "next/link";
import { Check, ChevronRight, CircleAlert, Copy, History, Info, RotateCcw } from "lucide-react";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Markdown } from "@/components/markdown";
import type { ChatSummary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { replyText, type AssistantTurn, type Setup, type Turn } from "../_lib/turns";
import { SetupProgress } from "./setup-progress";
import { ToolStepRow } from "./tool-step";
import { QuestionCard } from "./question-card";

type Answer = (id: string, answer: string) => Promise<void>;

const setupRunning = (setup?: Setup) =>
  !!setup && [setup.download, setup.load].some((step) => step && !step.done);

function ThinkingIndicator() {
  return (
    <div className="flex items-center gap-1.5 py-1" role="status" aria-label="Thinking">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="block size-1.5 rounded-full bg-muted-foreground"
          style={{ animation: "thinking-dot 1.4s ease-in-out infinite", animationDelay: `${i * 0.2}s` }}
        />
      ))}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={copied ? "Copied" : "Copy reply"}
            onClick={() => {
              navigator.clipboard.writeText(text).then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }).catch(() => {});
            }}
          />
        }
      >
        {copied ? <Check /> : <Copy />}
      </TooltipTrigger>
      <TooltipContent>{copied ? "Copied" : "Copy"}</TooltipContent>
    </Tooltip>
  );
}

function Reply({ turn, last, onRetry, onAnswer }: { turn: AssistantTurn; last: boolean; onRetry: () => void; onAnswer?: Answer }) {
  const lastStep = turn.steps[turn.steps.length - 1];
  // Between tool calls, and once a question is answered, the model is still
  // working, with nothing on screen.
  const thinking = turn.streaming && !turn.error && !setupRunning(turn.setup)
    && (!lastStep || (lastStep.kind === "tool" && lastStep.result !== undefined)
      || (lastStep.kind === "question" && lastStep.answer !== undefined));
  const text = replyText(turn);
  return (
    <article aria-label="Imprint's reply" className="group/reply space-y-3 text-sm">
      {turn.setup && <SetupProgress setup={turn.setup} />}
      {turn.notice && (
        <Alert role="status">
          <Info />
          <AlertTitle>{turn.noticeTitle ?? "Chatting without tools"}</AlertTitle>
          <AlertDescription>
            {turn.notice} <Link href="/settings/#settings-chat" className="underline underline-offset-2">Open chat settings</Link>
          </AlertDescription>
        </Alert>
      )}
      {turn.steps.map((step, i) =>
        step.kind === "text"
          ? <Markdown key={i} text={step.text} className="leading-relaxed break-words" />
          : step.kind === "question"
            ? <QuestionCard key={step.id} step={step} live={turn.streaming && last} onAnswer={onAnswer} />
            : <ToolStepRow key={i} step={step} />,
      )}
      {turn.streaming && turn.analysis && <p role="status" className="text-xs text-muted-foreground">{turn.analysis}</p>}
      {thinking && !turn.analysis && <ThinkingIndicator />}
      {turn.stopped && <p className="text-xs text-muted-foreground">Stopped</p>}
      {turn.error && (
        <Alert variant="destructive">
          <CircleAlert />
          <AlertTitle>The reply failed</AlertTitle>
          <AlertDescription className="font-mono text-xs whitespace-pre-wrap">{turn.error}</AlertDescription>
          {last && (
            <AlertAction>
              <Button variant="outline" size="xs" onClick={onRetry}><RotateCcw /> Retry</Button>
            </AlertAction>
          )}
        </Alert>
      )}
      {!turn.streaming && text && (
        <div className={cn("flex gap-1 transition-opacity", !last && "opacity-0 focus-within:opacity-100 group-hover/reply:opacity-100")}>
          <CopyButton text={text} />
        </div>
      )}
    </article>
  );
}

/** Where the summarized part of a chat ends: a quiet rule whose label opens
 * the summary Imprint now reads in place of the exchanges above (ADR 0141). */
function SummaryDivider({ summary }: { summary: ChatSummary }) {
  const above = summary.exchanges === 1
    ? "the exchange above, which no longer fits"
    : `the ${summary.exchanges} exchanges above, which no longer fit`;
  return (
    <Collapsible>
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span className="h-px flex-1 bg-border" aria-hidden />
        <CollapsibleTrigger className="group/summary flex items-center gap-1.5 rounded-md outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50">
          <History className="size-3.5 shrink-0" aria-hidden />
          <span>Summary of earlier conversation</span>
          <ChevronRight className="size-3.5 shrink-0 transition-transform group-data-[panel-open]/summary:rotate-90" aria-hidden />
        </CollapsibleTrigger>
        <span className="h-px flex-1 bg-border" aria-hidden />
      </div>
      <CollapsibleContent>
        <section aria-label="Summary of earlier conversation" className="mt-3 rounded-lg border border-border bg-muted/40 p-3 text-xs">
          <p className="mb-2 text-muted-foreground">
            Imprint reads this summary in place of {above} the model&apos;s context. It can still look up what
            was said there.
          </p>
          <Markdown text={summary.text} className="leading-relaxed break-words" />
        </section>
      </CollapsibleContent>
    </Collapsible>
  );
}

/** A conversation laid out like a document: the user's messages as bubbles
 * on the right, replies as plain text with their tool calls inline. A
 * summary sits after the last exchange it covers. */
export function Conversation({ turns, summary, onRetry, onAnswer }: {
  turns: Turn[];
  summary?: ChatSummary | null;
  onRetry: () => void;
  /** Answer a question the reply asks (ADR 0159). */
  onAnswer?: Answer;
}) {
  // The user turn right after the summarized exchanges.
  const users = turns.flatMap((turn, i) => (turn.role === "user" ? [i] : []));
  const boundary = summary?.text && summary.exchanges > 0 ? users[summary.exchanges] : undefined;
  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6">
      {turns.map((turn, i) => (
        <Fragment key={i}>
          {i === boundary && summary && <SummaryDivider summary={summary} />}
          {turn.role === "user" ? (
            <div className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl bg-muted px-4 py-2.5 text-sm whitespace-pre-wrap break-words">{turn.text}</div>
            </div>
          ) : (
            <Reply turn={turn} last={i === turns.length - 1} onRetry={onRetry} onAnswer={onAnswer} />
          )}
        </Fragment>
      ))}
    </div>
  );
}
