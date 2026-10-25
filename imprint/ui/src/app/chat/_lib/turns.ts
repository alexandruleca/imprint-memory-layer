import type { ChatEvent, ChatMessage } from "@/lib/types";

export type TextStep = { kind: "text"; text: string };
export type ToolStep = { kind: "tool"; name: string; args: Record<string, unknown>; result?: string };
export type Step = TextStep | ToolStep;

/** First-use setup of the local model, shown above the reply it unblocks. */
export type Setup = {
  install?: { done: boolean; line: string };
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
    reply.steps.push(message.role === "tool"
      ? { kind: "tool", name: message.tool_name ?? "tool", args: message.tool_args ?? {}, result: message.content }
      : { kind: "text", text: message.content });
  }
  return turns;
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
    case "install_start":
      return { ...turn, setup: { ...setup, install: { done: false, line: "" } } };
    case "install_progress":
      return { ...turn, setup: { ...setup, install: { done: false, line: ev.line } } };
    case "install_complete":
      return { ...turn, setup: { ...setup, install: { done: true, line: "" } } };
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
      return { ...turn, notice: ev.text, setup: loaded(turn.setup) };
    case "token": {
      const steps = [...turn.steps];
      const last = steps[steps.length - 1];
      if (last?.kind === "text") steps[steps.length - 1] = { kind: "text", text: last.text + ev.text };
      else steps.push({ kind: "text", text: ev.text });
      return { ...turn, steps, setup: loaded(turn.setup) };
    }
    case "tool_call":
      return { ...turn, steps: [...turn.steps, { kind: "tool", name: ev.name, args: ev.args }], setup: loaded(turn.setup) };
    case "tool_result": {
      const index = turn.steps.findLastIndex((step) => step.kind === "tool" && step.name === ev.name && step.result === undefined);
      if (index < 0) return turn;
      const steps = [...turn.steps];
      steps[index] = { ...(steps[index] as ToolStep), result: ev.result };
      return { ...turn, steps };
    }
    case "error":
      return { ...turn, error: ev.error };
    case "done":
      return { ...turn, streaming: false };
    default:
      return turn;
  }
}

/** The reply's text, for copying. */
export function replyText(turn: AssistantTurn): string {
  return turn.steps.flatMap((step) => (step.kind === "text" ? [step.text.trim()] : [])).join("\n\n");
}
