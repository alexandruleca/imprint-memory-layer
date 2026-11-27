import type { BoardTask, TaskHolder, TaskPerson, TaskPriority, TaskStatus } from "@/lib/api";
import { clientName } from "../format";

/** The Board's statuses in the words of the glossary. */
export const STATUS_LABELS: Record<TaskStatus, string> = {
  backlog: "Backlog", ready: "Ready", in_progress: "In progress", review: "Review", done: "Done", cancelled: "Cancelled",
};
export const STATUS_HINTS: Record<TaskStatus, string> = {
  backlog: "Captured, not yet ready to start",
  ready: "Described well enough for an agent to start",
  in_progress: "Claimed by an agent and being worked on",
  review: "Finished, waiting to be checked",
  done: "Checked and finished",
  cancelled: "Will not be done",
};
export const PRIORITY_LABELS: Record<TaskPriority, string> = { P0: "P0 · urgent", P1: "P1 · high", P2: "P2 · normal", P3: "P3 · low" };

/** Who wrote something: "you", or an agent by its name and client. */
export function who(person: TaskPerson | TaskHolder | null | undefined): string {
  if (!person) return "nobody";
  if ("kind" in person && person.kind === "user") return "you";
  if ("kind" in person && person.kind === "system") return "Imprint";
  const name = person.name || "an agent";
  return person.client ? `${name} (${clientName(person.client)})` : name;
}

/** "3 min ago", "2 h ago", "4 d ago". */
export function ago(seconds: number, now: Date): string {
  const minutes = Math.max(0, Math.floor((now.getTime() / 1000 - seconds) / 60));
  if (minutes < 1) return "just now";
  if (minutes < 120) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  return hours < 48 ? `${hours} h ago` : `${Math.floor(hours / 24)} d ago`;
}

/** What holds a Task up, in the order a reader should see it. */
export function trouble(task: BoardTask): { label: string; tone: "danger" | "warning" | "muted"; detail: string }[] {
  const found: { label: string; tone: "danger" | "warning" | "muted"; detail: string }[] = [];
  if (task.stale) found.push({ label: "Stale", tone: "danger", detail: `${who(task.previous_holder)} stopped working on it; another agent can take it over` });
  if (task.stalling) found.push({ label: "Stalling", tone: "warning", detail: `No progress from ${who(task.holder)} for a while` });
  if (task.blocked) found.push({ label: "Blocked", tone: "danger", detail: task.blocked.reason });
  if (task.open_blockers.length) found.push({ label: `Waits on ${task.open_blockers.length}`, tone: "muted", detail: `Waits on ${task.open_blockers.join(", ")}` });
  return found;
}
