import type { AgentMessage, AgentSession } from "@/lib/api";
import { formatDuration } from "@/lib/queue";
import { clockTime, dayLabel } from "@/components/timestamp";

// The names clients give themselves in the MCP handshake.
const CLIENT_NAMES: Record<string, string> = {
  "claude-code": "Claude Code",
  "claude-ai": "Claude Desktop",
  "cursor-vscode": "Cursor",
  "codex-mcp-client": "Codex",
  // As the Board and Live name clients (tasks.client_id).
  codex: "Codex",
  cursor: "Cursor",
  copilot: "GitHub Copilot",
  opencode: "OpenCode",
  cline: "Cline",
  "claude-desktop": "Claude Desktop",
};
export const clientName = (client: string | null) => (client ? CLIENT_NAMES[client] ?? client : "Unknown client");

export const folderName = (dir: string | null) => (dir ? dir.split(/[\\/]/).filter(Boolean).pop() ?? dir : "No folder");
export const plural = (n: number, word: string) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
export const ms = (value: number) => (value < 1000 ? `${Math.round(value)} ms` : formatDuration(value / 1000));

/** How long the session ran, or has run so far. */
export function sessionLength(s: AgentSession, now: Date): string {
  const end = s.connected ? now.getTime() / 1000 : (s.ended_at ?? s.last_seen);
  return formatDuration(Math.max(0, end - s.started_at));
}

/** When a finished session was last active, as short as its time group
 * allows: the clock within Today and Yesterday, the weekday and clock in
 * the last two weeks, else the date. */
export function whenInGroup(seconds: number, group: string, now: Date): string {
  if (group === "today" || group === "yesterday") return clockTime(seconds);
  if (group === "this-week" || group === "last-week") {
    return `${new Date(seconds * 1000).toLocaleDateString(undefined, { weekday: "short" })} ${clockTime(seconds)}`;
  }
  return dayLabel(seconds, now);
}

/** Where a message got to, in words (ADR 0245). */
export function messageState(message: AgentMessage): string {
  if (message.state === "delivered") {
    return message.via === "inbox" ? "Delivered as a peer message"
      : message.via === "tool" ? "Told after its next tool call"
      : message.via === "prompt" ? "Told with its next prompt" : "Told on its next Imprint reply";
  }
  if (message.state === "held") return "Held by Claude Code: approve it in the session. Also waiting for its next Imprint call";
  if (message.state === "refused") return "Refused by Claude Code's inbox. Waiting for its next Imprint call";
  if (message.state === "failed") return "Its inbox could not be reached. Waiting for its next Imprint call";
  return "Waiting for its next Imprint call";
}
