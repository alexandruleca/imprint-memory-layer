"use client";

import { Fragment } from "react";
import { BookOpenText, Check, RotateCcw, Undo2, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { ActionButton, RowError, type RowContext, type RowParts } from "@/components/review/review-list";
import type { OrganizeProposal } from "@/lib/api";
import { ago } from "@/lib/queue";

/** What the page does to a proposal: Move, Keep here, Move back (an
 * automatic move) and Reopen (a decision). */
export type OrganizeKind = "move" | "keep" | "back" | "reopen";
export type OrganizeStatus = OrganizeProposal["status"];

export const proposalKey = (p: Pick<OrganizeProposal, "origin" | "source">) => `${p.origin}\u0000${p.source}`;
export const parseKey = (key: string) => {
  const at = key.indexOf("\u0000");
  return { origin: key.slice(0, at), source: key.slice(at + 1) };
};

export const originLabel = (origin: string) => origin || "No project";

const METHODS: Record<OrganizeProposal["method"], string> = {
  path: "Mentions its folder", name: "Names the project", "local-model": "Local model, quoted",
};

/** Why it was placed: for a move Organize made itself, why that was strong (ADR 0133). */
export function evidenceLabel(p: OrganizeProposal) {
  if (p.status === "auto" || (p.strength === "strong" && p.status !== "pending")) {
    return p.method === "path" ? "Its folder path" : "Only this project, 2+ times";
  }
  return METHODS[p.method] ?? p.method;
}

/** A short name for toasts: the title, else the source shortened in the middle. */
export function proposalName(p: OrganizeProposal) {
  if (p.title) return `“${p.title.length > 44 ? `${p.title.slice(0, 42)}…` : p.title}”`;
  return p.source.length > 36 ? `${p.source.slice(0, 17)}…${p.source.slice(-15)}` : p.source;
}

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** `text` with the target project's name marked: underlined as well as
 * shaded, so the match does not rest on colour alone. */
export function Highlight({ text, term }: { text: string; term: string }) {
  if (!term) return <>{text}</>;
  const parts = text.split(new RegExp(`(${escape(term)})`, "gi"));
  return (
    <>
      {parts.map((part, i) => i % 2
        ? <mark key={i} className="rounded-sm bg-foreground/10 px-0.5 text-inherit underline decoration-foreground/50 underline-offset-2">{part}</mark>
        : <Fragment key={i}>{part}</Fragment>)}
    </>
  );
}

export interface ProposalRowOptions {
  tab: "review" | "moved" | "decided";
  /** Show "→ target": on All projects and outside To review. */
  showTarget: boolean;
  autoMove: boolean;
  /** The job that holds the ingestion lock, which Move and Move back wait for. */
  lock: string | null;
  error?: string;
  /** The first sentence of the target's profile, for the expanded row. */
  about?: string;
  onAct: (kind: OrganizeKind, key: string) => void;
  onRetry: (key: string) => void;
  onRead: (p: OrganizeProposal) => void;
}

/** One proposal as a two-line row (spec §3.3): the title or source, then
 * where it is from, the evidence and one line of the quote. */
export function proposalRow(p: OrganizeProposal, ctx: RowContext, o: ProposalRowOptions): RowParts {
  const key = proposalKey(p);
  const quote = p.evidence[0]?.quote ?? "";
  const locked = (kind: OrganizeKind) => (kind === "move" || kind === "back") && o.lock !== null;
  const hint = (kind: OrganizeKind, label: string, letter: string) => (locked(kind) ? `Moves wait while ${o.lock} runs` : `${label} (${letter})`);
  const button = (kind: OrganizeKind, label: string, letter: string, icon: React.ReactNode, variant: "default" | "outline") => (
    <ActionButton key={kind} icon={icon} label={label} hint={hint(kind, label, letter)} variant={variant} tabIndex={ctx.tabIndex}
      disabled={locked(kind)} onClick={() => o.onAct(kind, key)} />
  );
  const actions = o.tab === "review"
    ? <>{button("move", "Move", "A", <Check />, "default")}{button("keep", "Keep here", "R", <X />, "outline")}</>
    : o.tab === "moved"
      ? button("back", "Move back", "U", <Undo2 />, "outline")
      : <>
          <Badge variant={p.status === "accepted" ? "secondary" : "outline"} className="@max-[620px]/page:hidden">
            {p.status === "accepted" ? "Moved by you" : p.strength === "strong" && o.autoMove ? "Moved back" : "Kept here"}
          </Badge>
          {button("reopen", "Reopen", "U", <RotateCcw />, "outline")}
        </>;
  const when = o.tab === "review" ? p.created_at : p.decided_at ?? p.created_at;
  return {
    body: (
      <>
        <p className="truncate text-sm font-medium" title={p.source}>
          {p.title ? p.title : <span className="font-mono text-[12.5px]">{p.source}</span>}
        </p>
        <p className="truncate text-xs text-muted-foreground">
          {p.title && <><span className="font-mono">{p.source}</span>{" · "}</>}
          from {originLabel(p.origin)}
          {o.showTarget && <> → <span className="font-medium text-foreground">{p.target}</span></>}
          {" · "}{p.method === "local-model" && o.tab === "review"
            ? <Badge variant="outline" className="h-4 px-1.5 text-[11px]">Local model</Badge> : evidenceLabel(p)}
          {p.status === "pending" && p.strength === "strong" && <>{" "}<Badge variant="outline" className="h-4 px-1.5 text-[11px]">Strong evidence</Badge></>}
          {o.tab === "decided" && <span className="hidden @max-[620px]/page:inline"> · {p.status === "accepted" ? "moved by you" : "kept here"}</span>}
          {quote && <>{" · "}<span className="italic">“<Highlight text={quote} term={p.target} />”</span></>}
        </p>
      </>
    ),
    meta: <>{p.chunks} {p.chunks === 1 ? "chunk" : "chunks"} · {o.tab === "moved" ? "moved " : ""}{ago(when)}</>,
    actions,
    details: ctx.expanded ? (
      <div className="mt-1 space-y-2 rounded-lg border border-border bg-background p-3 text-sm">
        <p className="font-mono text-xs break-all text-muted-foreground">{p.source}</p>
        {p.preview && <p className="leading-relaxed"><Highlight text={p.preview} term={p.target} /></p>}
        {quote && <p className="text-muted-foreground">Quote: “<Highlight text={quote} term={p.target} />”</p>}
        {o.tab === "review" && o.about && <p className="text-muted-foreground"><span className="font-medium text-foreground">About {p.target}:</span> {o.about}</p>}
        <Button variant="outline" size="sm" tabIndex={ctx.tabIndex} onClick={() => o.onRead(p)}>
          <BookOpenText /> Read source <Kbd>O</Kbd>
        </Button>
      </div>
    ) : undefined,
    error: o.error ? <RowError message={o.error} tabIndex={ctx.tabIndex} onRetry={() => o.onRetry(key)} /> : undefined,
  };
}
