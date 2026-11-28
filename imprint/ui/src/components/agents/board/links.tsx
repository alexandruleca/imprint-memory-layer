"use client";

import { Fragment, useState } from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

/** A Task id where text mentions one: T- and five of the Board's id characters. */
const TASK_ID = /\bT-[0-9a-hjkmnp-tv-z]{5}\b/g;

/** The dashboard's address of a Task: the Board with its drawer open. */
export function taskUrl(id: string): string {
  return `${window.location.origin}/agents/?task=${encodeURIComponent(id)}#board`;
}

/** Copy text, also where the clipboard API is missing (a page served over
 * plain http to another device). */
export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(text); return; } catch { /* Try the fallback. */ }
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  const copied = document.execCommand("copy");
  area.remove();
  if (!copied) throw new Error("The browser refused to copy");
}

/** Copy a Task's id or link, saying so. */
export async function copyTask(id: string, what: "id" | "link" = "id"): Promise<boolean> {
  try {
    await copyText(what === "id" ? id : taskUrl(id));
    toast.success(what === "id" ? `Copied ${id}` : `Copied the link to ${id}`);
    return true;
  } catch {
    toast.error(`Could not copy the ${what}`, { description: "Select it and copy it instead." });
    return false;
  }
}

/** A Task's id that copies itself when clicked; it shows a tick for a moment. */
export function CopyId({ id, className }: { id: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const Icon = copied ? Check : Copy;
  return (
    <button type="button" title="Copy the id" aria-label={`Copy ${id}`}
      onClick={async (e) => {
        e.stopPropagation();
        if (await copyTask(id)) { setCopied(true); setTimeout(() => setCopied(false), 1500); }
      }}
      className={cn("group/id inline-flex shrink-0 items-center gap-1 rounded px-1 -mx-1 font-mono whitespace-nowrap outline-none",
        "hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50", className)}>
      {id}
      <Icon aria-hidden className={cn("size-3 transition-opacity", copied ? "opacity-100 text-emerald-600 dark:text-emerald-400" : "opacity-0 group-hover/id:opacity-70 group-focus-visible/id:opacity-70")} />
    </button>
  );
}

/** A Task id written in text, as a link that opens that Task. */
export function TaskLink({ id, onOpen, className }: { id: string; onOpen: (id: string) => void; className?: string }) {
  return (
    <button type="button" onClick={(e) => { e.stopPropagation(); onOpen(id); }} title={`Open ${id}`}
      className={cn("rounded font-mono text-[0.92em] text-primary underline decoration-primary/30 underline-offset-2 outline-none hover:decoration-primary focus-visible:ring-2 focus-visible:ring-ring/50", className)}>
      {id}
    </button>
  );
}

/** Plain text with every Task id in it a link to that Task. */
export function TaskRefs({ text, onOpen }: { text: string; onOpen: (id: string) => void }) {
  const parts = text.split(new RegExp(`(${TASK_ID.source})`, "g"));
  return <>{parts.map((part, i) => (i % 2 ? <TaskLink key={i} id={part} onOpen={onOpen} /> : <Fragment key={i}>{part}</Fragment>))}</>;
}

/** The address a Markdown link to a Task carries, for the link renderer to
 * recognise. A fragment, which react-markdown's URL check keeps. */
const TASK_HREF = "#imprint-task=";

type MdNode = { type: string; value?: string; url?: string; children?: MdNode[] };

/** A remark plugin that turns Task ids in text (not in code, not already in a
 * link) into links the renderer below shows as Task links. */
export function remarkTaskLinks() {
  const split = (node: MdNode): MdNode[] => {
    if (node.type !== "text" || !node.value) return [node];
    // Splitting on a captured pattern alternates text and ids: ids at odd places.
    const parts = node.value.split(new RegExp(`(${TASK_ID.source})`, "g"));
    if (parts.length === 1) return [node];
    return parts.flatMap((part, i): MdNode[] => {
      if (!part) return [];
      return i % 2 ? [{ type: "link", url: TASK_HREF + part, children: [{ type: "text", value: part }] }] : [{ type: "text", value: part }];
    });
  };
  const walk = (node: MdNode) => {
    if (!node.children || node.type === "link" || node.type === "linkReference") return;
    node.children = node.children.flatMap(split);
    node.children.forEach(walk);
  };
  return (tree: MdNode) => walk(tree);
}

/** The Markdown link renderer for Task links; other links render as usual. */
export function taskLinkRenderer(onOpen: (id: string) => void) {
  return function renderTaskLink(href: string | undefined) {
    return href?.startsWith(TASK_HREF) ? <TaskLink id={href.slice(TASK_HREF.length)} onOpen={onOpen} /> : null;
  };
}
