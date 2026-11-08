"use client";

import { useState } from "react";
import { ArrowUp, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

type Props = {
  onSend: (text: string) => void;
  onStop: () => void;
  streaming: boolean;
  /** The reply waits for an answer in the user's own words: sending answers it (ADR 0159). */
  answering?: boolean;
  model: string;
  autoFocus?: boolean;
  className?: string;
  ref?: React.Ref<HTMLTextAreaElement>;
};

/** The message box. Enter sends, Shift+Enter adds a line; while a reply
 * streams the send button stops it and typing the next message continues.
 * While the reply waits for an answer, Enter sends the answer. */
export function Composer({ onSend, onStop, streaming, answering, model, autoFocus, className, ref }: Props) {
  const [text, setText] = useState("");
  const canSend = (!streaming || !!answering) && text.trim().length > 0;

  function send() {
    if (!canSend) return;
    onSend(text.trim());
    setText("");
  }

  return (
    <form
      className={cn("w-full", className)}
      onSubmit={(event) => { event.preventDefault(); send(); }}
    >
      <div className="rounded-2xl border border-input bg-card shadow-sm transition-[border-color,box-shadow] focus-within:border-ring/60 focus-within:shadow-md">
        <Textarea
          ref={ref}
          value={text}
          autoFocus={autoFocus}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              send();
            }
          }}
          rows={1}
          placeholder={answering ? "Answer Imprint's question…" : "Ask Imprint anything…"}
          aria-label="Message"
          className="max-h-52 min-h-12 resize-none border-0 bg-transparent px-4 pt-3.5 pb-1 shadow-none focus-visible:ring-0 dark:bg-transparent"
        />
        <div className="flex items-center justify-between gap-3 px-3 pb-2.5">
          <span className="truncate text-xs text-muted-foreground" title={model}>{model}</span>
          {streaming ? (
            <div className="flex gap-1.5">
              {answering && (
                <Button type="submit" size="icon-sm" className="rounded-full" disabled={!canSend} aria-label="Send answer">
                  <ArrowUp />
                </Button>
              )}
              <Button type="button" size="icon-sm" variant={answering ? "outline" : "default"} className="rounded-full"
                onClick={onStop} aria-label="Stop reply">
                <Square className="size-3 fill-current" />
              </Button>
            </div>
          ) : (
            <Button type="submit" size="icon-sm" className="rounded-full" disabled={!canSend} aria-label="Send message">
              <ArrowUp />
            </Button>
          )}
        </div>
      </div>
    </form>
  );
}
