"use client";

import { useState } from "react";
import Link from "next/link";
import { Check, CircleAlert, Copy, Info, RotateCcw } from "lucide-react";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Markdown } from "@/components/markdown";
import { cn } from "@/lib/utils";
import { replyText, type AssistantTurn, type Setup, type Turn } from "../_lib/turns";
import { SetupProgress } from "./setup-progress";
import { ToolStepRow } from "./tool-step";

const setupRunning = (setup?: Setup) =>
  !!setup && [setup.install, setup.download, setup.load].some((step) => step && !step.done);

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

function Reply({ turn, last, onRetry }: { turn: AssistantTurn; last: boolean; onRetry: () => void }) {
  const lastStep = turn.steps[turn.steps.length - 1];
  // Between tool calls the model is still working, with nothing on screen.
  const thinking = turn.streaming && !turn.error && !setupRunning(turn.setup)
    && (!lastStep || (lastStep.kind === "tool" && lastStep.result !== undefined));
  const text = replyText(turn);
  return (
    <article aria-label="Imprint's reply" className="group/reply space-y-3 text-sm">
      {turn.setup && <SetupProgress setup={turn.setup} />}
      {turn.notice && (
        <Alert role="status">
          <Info />
          <AlertTitle>Chatting without tools</AlertTitle>
          <AlertDescription>
            {turn.notice} <Link href="/settings/#settings-chat" className="underline underline-offset-2">Open chat settings</Link>
          </AlertDescription>
        </Alert>
      )}
      {turn.steps.map((step, i) =>
        step.kind === "text"
          ? <Markdown key={i} text={step.text} className="leading-relaxed break-words" />
          : <ToolStepRow key={i} step={step} />,
      )}
      {thinking && <ThinkingIndicator />}
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

/** A conversation laid out like a document: the user's messages as bubbles
 * on the right, replies as plain text with their tool calls inline. */
export function Conversation({ turns, onRetry }: { turns: Turn[]; onRetry: () => void }) {
  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6">
      {turns.map((turn, i) =>
        turn.role === "user" ? (
          <div key={i} className="flex justify-end">
            <div className="max-w-[85%] rounded-2xl bg-muted px-4 py-2.5 text-sm whitespace-pre-wrap break-words">{turn.text}</div>
          </div>
        ) : (
          <Reply key={i} turn={turn} last={i === turns.length - 1} onRetry={onRetry} />
        ),
      )}
    </div>
  );
}
