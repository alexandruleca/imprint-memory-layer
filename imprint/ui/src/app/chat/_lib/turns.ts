import type { ChatEvent, ChatMessage } from "@/lib/types";

export type TextStep = { kind: "text"; text: string };
export type ToolStep = { kind: "tool"; name: string; args: Record<string, unknown>; result?: string };
/** A question the reply asked the user (ADR 0159). `answer` is undefined while
 * it waits, the user's answer once given, and null when none came in time. */
export type QuestionStep = {
  kind: "question"; id: string; question: string; options: string[]; allowOther: boolean; answer?: string | null;
};
export type Step = TextStep | ToolStep | QuestionStep;

/** The tool a reply asks the user with; it shows as a question, not a tool row. */
export const ASK_USER = "ask_user";
const ANSWERED = "The user answered: ";

/** First-use setup of the local model, shown above the reply it unblocks. */
export type Setup = {
  download?: { done: boolean; file: string; downloaded: number; total: number };
  load?: { done: boolean };
};

export type UserTurn = { role: "user"; text: string };
export type AssistantTurn = {
  role: "assistant";
  steps: Step[];
  setup?: Setup;
  /** Why this reply runs differently, e.g. a local model without tools. */
  notice?: string;
  noticeTitle?: string;
  analysis?: string;
  error?: string;
  stopped?: boolean;
  streaming: boolean;
};
export type Turn = UserTurn | AssistantTurn;

export const pendingReply = (): AssistantTurn => ({ role: "assistant", steps: [], streaming: true });

/** Rebuild turns from a stored conversation: the tool calls and the reply
 * that follow a user message form one assistant turn. */
export function turnsFromHistory(messages: ChatMessage[]): Turn[] {
  const turns: Turn[] = [];
  for (const message of messages) {
    if (message.role === "user") {
      turns.push({ role: "user", text: message.content });
      continue;
    }
    let reply = turns[turns.length - 1];
    if (!reply || reply.role !== "assistant") {
      reply = { role: "assistant", steps: [], streaming: false };
      turns.push(reply);
    }
    if (message.role === "tool" && message.tool_name === ASK_USER) {
      reply.steps.push(storedQuestion(message, reply.steps.length));
      continue;
    }
    reply.steps.push(message.role === "tool"
      ? { kind: "tool", name: message.tool_name ?? "tool", args: message.tool_args ?? {}, result: message.content }
      : { kind: "text", text: message.content });
  }
  return turns;
}

/** A stored ask_user exchange as the question and the answer it got. */
function storedQuestion(message: ChatMessage, index: number): QuestionStep {
  const args = message.tool_args ?? {};
  const options = Array.isArray(args.options) ? args.options.map(String) : [];
  return {
    kind: "question", id: `stored-${index}`, question: typeof args.question === "string" ? args.question : "",
    options, allowOther: args.allow_other !== false,
    answer: message.content.startsWith(ANSWERED) ? message.content.slice(ANSWERED.length) : null,
  };
}

/** The question this reply waits on, if any. */
export function waitingQuestion(turn: Turn | undefined): QuestionStep | undefined {
  if (turn?.role !== "assistant" || !turn.streaming) return undefined;
  const step = turn.steps.findLast((item) => item.kind === "question");
  return step?.kind === "question" && step.answer === undefined ? step : undefined;
}

/** Record an answer on its question. */
export function answerStep(turn: AssistantTurn, id: string, answer: string | null): AssistantTurn {
  return { ...turn, steps: turn.steps.map((step) => (step.kind === "question" && step.id === id ? { ...step, answer } : step)) };
}

/** The model produced output, so a pending model load has finished. */
function loaded(setup: Setup | undefined): Setup | undefined {
  return setup?.load && !setup.load.done ? { ...setup, load: { done: true } } : setup;
}

/** Apply one stream event to the reply being streamed. The streamed text is
 * already the final reply (the server never streams tool-call markup), so
 * `assistant_message` changes nothing here. */
export function applyEvent(turn: AssistantTurn, ev: ChatEvent): AssistantTurn {
  const setup = turn.setup ?? {};
  switch (ev.type) {
    case "download_start":
      return { ...turn, setup: { ...setup, download: { done: false, file: ev.file, downloaded: 0, total: 0 } } };
    case "download_progress":
      if (!setup.download) return turn;
      return { ...turn, setup: { ...setup, download: { ...setup.download, downloaded: ev.downloaded, total: ev.total } } };
    case "download_complete":
      if (!setup.download) return turn;
      return { ...turn, setup: { ...setup, download: { ...setup.download, done: true } } };
    case "load_start":
      return { ...turn, setup: { ...setup, load: { done: false } } };
    case "notice":
      return { ...turn, notice: ev.text, noticeTitle: ev.title, setup: loaded(turn.setup) };
    case "analysis":
      return { ...turn, analysis: ev.text, setup: loaded(turn.setup) };
    case "summary":
      // The page shows the summary itself; the reply is being written now.
      return { ...turn, analysis: undefined };
    case "token": {
      const steps = [...turn.steps];
      const last = steps[steps.length - 1];
      if (last?.kind === "text") steps[steps.length - 1] = { kind: "text", text: last.text + ev.text };
      else steps.push({ kind: "text", text: ev.text });
      return { ...turn, steps, analysis: undefined, setup: loaded(turn.setup) };
    }
    case "tool_call":
      // The question that follows shows the call (ADR 0159).
      if (ev.name === ASK_USER) return { ...turn, analysis: undefined, setup: loaded(turn.setup) };
      return { ...turn, steps: [...turn.steps, { kind: "tool", name: ev.name, args: ev.args }], analysis: undefined, setup: loaded(turn.setup) };
    case "question":
      return { ...turn, analysis: undefined, steps: [...turn.steps, {
        kind: "question", id: ev.id, question: ev.question, options: ev.options ?? [], allowOther: ev.allow_other !== false,
      }] };
    case "answer":
      return answerStep(turn, ev.id, ev.text);
    case "tool_result": {
      const index = turn.steps.findLastIndex((step) => step.kind === "tool" && step.name === ev.name && step.result === undefined);
      if (index < 0) return turn;
      const steps = [...turn.steps];
      steps[index] = { ...(steps[index] as ToolStep), result: ev.result };
      return { ...turn, steps };
    }
    case "error":
      return { ...turn, error: ev.error, analysis: undefined };
    case "done":
      return { ...turn, streaming: false, analysis: undefined };
    default:
      return turn;
  }
}

/** The reply's text, for copying. */
export function replyText(turn: AssistantTurn): string {
  return turn.steps.flatMap((step) => (step.kind === "text" ? [step.text.trim()] : [])).join("\n\n");
}
