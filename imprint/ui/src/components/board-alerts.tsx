"use client";

import { isAgentsWindow } from "@/lib/agents-window";
import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { getTaskInbox, markTaskCommentsRead, type TaskInbox } from "@/lib/api";
import { useCached } from "@/lib/use-cached";
import { who } from "@/components/agents/board/format";

const SEEN_KEY = "imprint.board.review-seen";
const TOLD_KEY = "imprint.board.comments-told";

/** Comments already toasted in this browser: closing a toast no longer reads one (ADR 0241), so a reload must not toast it again. */
function toldComments(): Set<string> {
  try { return new Set(JSON.parse(window.localStorage.getItem(TOLD_KEY) ?? "[]")); } catch { return new Set(); }
}

function rememberTold(told: Set<string>, unread: string[]) {
  // Only the comments still unread are kept, so the list stays short.
  try { window.localStorage.setItem(TOLD_KEY, JSON.stringify(unread.filter((id) => told.has(id)))); } catch { /* Told again after a reload. */ }
}

/** Tasks in Review the viewer was already told about, kept in this browser. */
function seenReview(): Record<string, number> {
  try { return JSON.parse(window.localStorage.getItem(SEEN_KEY) ?? "{}") ?? {}; } catch { return {}; }
}

function rememberReview(seen: Record<string, number>) {
  try { window.localStorage.setItem(SEEN_KEY, JSON.stringify(seen)); } catch { /* Told again after a reload. */ }
}

/** A native notification while the desktop app is not in front. */
function notifyDesktop(title: string, body: string, onClick: () => void) {
  if (!window.electronAPI || document.visibilityState === "visible" || typeof Notification === "undefined") return;
  if (Notification.permission !== "granted") return;
  const shown = new Notification(title, { body });
  shown.onclick = () => { window.focus(); onClick(); };
}

/** Tells the user, on any page, about comments agents addressed to them and
 * Tasks that reached Review (ADR 0212): a toast, and in the desktop app a
 * native notification while it is not in front. A comment counts as read
 * once its Task is opened or answered, or it is dismissed on Home's
 * Needs-you list (ADR 0241); closing a toast leaves it there. A Task in
 * Review is told once. */
export function BoardAlerts() {
  const router = useRouter();
  const inbox = useCached<TaskInbox>("tasks-inbox", () => getTaskInbox(), { refreshInterval: 15_000, persist: false });
  const shown = useRef(new Set<string>());

  useEffect(() => {
    // An older API, or one that failed part-way, answers without them: nothing to tell.
    const comments = Array.isArray(inbox.data?.comments) ? inbox.data.comments : [];
    const review = Array.isArray(inbox.data?.review) ? inbox.data.review : [];
    // The desktop app's Agents window leaves telling to the main window, so nothing is told twice (ADR 0232).
    if ((!comments.length && !review.length) || isAgentsWindow()) return;
    // In the desktop app a Task opens in the Agents window; in a browser, on this page's Board.
    const openTask = (id: string) => (window.electronAPI?.openAgents
      ? window.electronAPI.openAgents(id) : router.push(`/agents/?task=${encodeURIComponent(id)}#board`));
    // Comments come from every workspace; one from another names it and opens nothing here, since its Task is on that Board.
    const here = inbox.data?.workspace;
    // A comment stays unread, and on Home's Needs-you list, until it is opened, answered or dismissed there (ADR 0241).
    const told = toldComments();
    for (const comment of comments) {
      if (shown.current.has(comment.id) || told.has(comment.id)) continue;
      shown.current.add(comment.id);
      told.add(comment.id);
      const read = () => { void markTaskCommentsRead([comment.id], comment.workspace).catch(() => {}); };
      const elsewhere = comment.workspace && here && comment.workspace !== here;
      const title = `${who(comment.by)} on ${comment.task}${elsewhere ? ` (workspace ${comment.workspace})` : ""}`;
      toast(title, {
        id: `task-comment-${comment.id}`, description: comment.text, duration: 20_000,
        action: elsewhere ? undefined : { label: "Open", onClick: () => { read(); openTask(comment.task); } },
      });
      notifyDesktop(title, comment.text, () => { read(); if (!elsewhere) openTask(comment.task); });
    }
    rememberTold(told, comments.map((c) => c.id));
    const seen = seenReview();
    let changed = false;
    for (const task of review) {
      if (seen[task.id] === task.status_at) continue;
      seen[task.id] = task.status_at;
      changed = true;
      const title = `${task.id} is ready for review`;
      toast(title, {
        id: `task-review-${task.id}`, description: task.title, duration: 15_000,
        action: { label: "Review", onClick: () => openTask(task.id) },
      });
      notifyDesktop(title, task.title, () => openTask(task.id));
    }
    if (changed) rememberReview(Object.fromEntries(Object.entries(seen).filter(([id]) => review.some((t) => t.id === id))));
  }, [inbox.data, router]);

  return null;
}
