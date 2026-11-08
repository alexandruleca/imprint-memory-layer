"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ArrowUp, Check, CircleHelp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { QuestionStep } from "../_lib/turns";

/** A question the reply asks the user, in the reply stream (ADR 0159): its
 * choices as buttons and, when allowed, a box for an answer in the user's own
 * words. Answered, it shows the answer; a reply that ended without one says so. */
export function QuestionCard({ step, live, onAnswer }: {
  step: QuestionStep;
  /** The reply still runs, so the question can be answered. */
  live: boolean;
  onAnswer?: (id: string, answer: string) => Promise<void>;
}) {
  const heading = useId();
  const [other, setOther] = useState("");
  const [sending, setSending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const first = useRef<HTMLButtonElement | null>(null);
  const field = useRef<HTMLInputElement | null>(null);
  const waiting = live && step.answer === undefined && !!onAnswer;

  // A new question takes the focus, so it can be answered from the keyboard.
  useEffect(() => {
    if (waiting) (first.current ?? field.current)?.focus();
    // Only when the question first appears.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step.id]);

  async function answer(text: string) {
    const value = text.trim();
    if (!value || !onAnswer || sending !== null) return;
    setSending(value);
    setError("");
    try {
      await onAnswer(step.id, value);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setSending(null);
    }
  }

  const answered = step.answer ?? (step.answer === undefined ? sending : null);
  const disabled = !waiting || sending !== null;
  return (
    <section role="group" aria-labelledby={heading} data-testid="chat-question"
      className="rounded-xl border border-border bg-card p-3 text-sm shadow-xs">
      <p id={heading} className="flex items-start gap-2 font-medium">
        <CircleHelp className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
        <span className="break-words">{step.question}</span>
      </p>
      {step.options.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {step.options.map((option, index) => (
            <Button key={option} ref={index === 0 ? first : undefined} type="button" variant="outline" size="sm"
              className="h-auto min-h-8 rounded-full py-1 font-normal whitespace-normal"
              aria-pressed={answered === option}
              disabled={disabled} onClick={() => void answer(option)}>
              {answered === option && <Check aria-hidden />}
              {option}
            </Button>
          ))}
        </div>
      )}
      {step.allowOther && waiting && sending === null && (
        <form className="mt-3 flex gap-2" onSubmit={(event) => { event.preventDefault(); void answer(other); }}>
          <Input ref={field} value={other} maxLength={2000} onChange={(event) => setOther(event.target.value)}
            placeholder={step.options.length ? "Or answer in your own words" : "Your answer"}
            aria-label="Your answer" className="h-8" />
          <Button type="submit" size="icon-sm" aria-label="Send answer" disabled={!other.trim()}>
            <ArrowUp />
          </Button>
        </form>
      )}
      <p className="mt-2 text-xs text-muted-foreground" role="status">
        {answered ? <>You answered: <span className="text-foreground">{answered}</span></>
          : step.answer === null ? "No answer came, so Imprint went on with its best assumption."
          : waiting ? "Imprint waits for your answer before it goes on."
          : "Not answered."}
      </p>
      {error && <p className="mt-1 text-xs text-destructive" role="alert">{error}</p>}
    </section>
  );
}
