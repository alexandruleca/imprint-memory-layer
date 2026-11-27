"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { getTaskInbox, markTaskCommentsRead, type TaskInbox } from "@/lib/api";
import { useCached } from "@/lib/use-cached";
import { who } from "@/components/agents/board/format";

const SEEN_KEY = "imprint.board.review-seen";

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
 * once its toast is opened or closed; a Task in Review is told once. */
export function BoardAlerts() {
  const router = useRouter();
  const inbox = useCached<TaskInbox>("tasks-inbox", () => getTaskInbox(), { refreshInterval: 15_000, persist: false });
  const shown = useRef(new Set<string>());

  useEffect(() => {
    // An older API, or one that failed part-way, answers without them: nothing to tell.
    const comments = Array.isArray(inbox.data?.comments) ? inbox.data.comments : [];
    const review = Array.isArray(inbox.data?.review) ? inbox.data.review : [];
    if (!comments.length && !review.length) return;
    const openTask = (id: string) => router.push(`/agents/?task=${encodeURIComponent(id)}#board`);
    for (const comment of comments) {
      if (shown.current.has(comment.id)) continue;
      shown.current.add(comment.id);
      const read = () => { void markTaskCommentsRead([comment.id]).catch(() => {}); };
      const title = `${who(comment.by)} on ${comment.task}`;
      toast(title, {
        id: `task-comment-${comment.id}`, description: comment.text, duration: 20_000, onDismiss: read, onAutoClose: () => {},
        action: { label: "Open", onClick: () => { read(); openTask(comment.task); } },
      });
      notifyDesktop(title, comment.text, () => { read(); openTask(comment.task); });
    }
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
