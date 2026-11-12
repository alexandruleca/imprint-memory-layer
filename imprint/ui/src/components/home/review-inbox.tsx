"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { ArrowRight, CircleAlert, CircleCheck, FolderInput, GitCompareArrows, Sparkles, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  changeCoreMemory, decideOrganize, getCoreMemories, getOrganize, reopenOrganize, resolveCoreConflict, retryJob,
  type CoreKeep, type CoreMemory, type OrganizeProposal,
} from "@/lib/api";
import { ago } from "@/lib/queue";
import type { QueueResponse } from "@/lib/types";
import { useCached } from "@/lib/use-cached";
import { useNow } from "@/components/home/activity";

/** Home's inbox in the simple dashboard (ADR 0182): what waits for the
 * user's yes or no, in one list. A job that did not finish, sources Organize
 * would move to a project, guidelines it drafted and newer statements that
 * contradict older ones. Each decision is the same write the Organize and
 * Core memories pages make, and can be undone from its toast. */

const SHOWN = 3;
const message = (error: unknown) => (error instanceof Error ? error.message : String(error));
const basename = (source: string) => source.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || source;
const proposalKey = (p: OrganizeProposal) => `move:${p.origin}\u0000${p.source}`;

type Choice = { label: string; primary?: boolean; run: () => Promise<unknown>; done: string; undo?: () => Promise<unknown> };

export function ReviewInbox({ queue }: { queue: QueueResponse | null }) {
  const now = useNow();
  const organize = useCached("home-organize", getOrganize, { revalidateOnDataChange: true, persist: false });
  const core = useCached("home-core", getCoreMemories, { global: true, persist: false });
  // Decided here and not yet gone from the lists the server sends.
  const [gone, setGone] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const proposals = (Array.isArray(organize.data?.proposals) ? organize.data.proposals : [])
    .filter((p) => p.status === "pending" && !gone.includes(proposalKey(p)));
  const memories = Array.isArray(core.data?.memories) ? core.data.memories : [];
  const byId = new Map(memories.map((m) => [m.id, m]));
  const drafts = memories.filter((m) => m.tier === "guideline" && m.state === "proposed" && !gone.includes(`draft:${m.id}`));
  const conflicts = memories.filter((m) => m.tier === "record" && m.state === "active"
    && m.conflicts.some((c) => c.resolution === "") && !gone.includes(`conflict:${m.id}`));
  const failed = queue?.recent?.find((job) => job.status === "failed" && (job.ended_at ?? 0) > now / 1000 - 86400
    && !gone.includes(`job:${job.id}`));
  const workspace = organize.data?.workspace ?? "";

  async function decide(key: string, choice: Choice) {
    setBusy(key);
    setGone((keys) => [...keys, key]);
    const back = () => setGone((keys) => keys.filter((k) => k !== key));
    const reload = () => Promise.all([organize.refresh(), core.refresh()]);
    try {
      await choice.run();
      toast.success(choice.done, choice.undo && {
        action: {
          label: "Undo",
          onClick: () => {
            choice.undo!().then(back).then(reload)
              .catch((error) => toast.error("Could not undo", { description: message(error) }));
          },
        },
      });
      void reload();
    } catch (error) {
      back();
      toast.error("That did not work", { description: message(error) });
    } finally {
      setBusy(null);
    }
  }

  /** A move the server refused (a job is running) is an error, not a decision. */
  async function move(p: OrganizeProposal, accept: boolean) {
    const answer = await decideOrganize(workspace, [{ origin: p.origin, source: p.source }], accept);
    if (answer.failed?.length) throw new Error(answer.failed[0].error);
  }

  const total = proposals.length + drafts.length + conflicts.length + (failed ? 1 : 0);

  return (
    <Card className="gap-3" data-tour="home-review">
      <CardHeader>
        <CardTitle className="text-sm"><h2>Needs your review{total > 0 ? ` (${total})` : ""}</h2></CardTitle>
        <CardDescription className="text-xs">Things Imprint would rather ask you about than decide by itself.</CardDescription>
      </CardHeader>
      <CardContent>
        {total === 0 ? (
          <p className="flex items-center gap-2 py-1 text-sm text-muted-foreground">
            <CircleCheck className="size-4 shrink-0" aria-hidden />
            Nothing needs you right now.
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {failed && (
              <Row key={`job:${failed.id}`} icon={CircleAlert} tone="problem" busy={busy !== null}
                title={`${failed.label ?? failed.command} did not finish${failed.ended_at ? ` (${ago(failed.ended_at)})` : ""}`}
                detail={failed.error ?? undefined}
                choices={[
                  { label: "Try again", primary: true, done: "Trying again", run: () => retryJob(failed.id) },
                ]}
                onChoose={(choice) => void decide(`job:${failed.id}`, choice)}
                more={{ href: "/queue/", label: "Details" }} />
            )}
            {proposals.slice(0, SHOWN).map((p) => (
              <Row key={proposalKey(p)} icon={FolderInput} busy={busy !== null}
                title={p.title || basename(p.source)}
                detail={`Looks like it belongs to the project “${p.target}”.${p.evidence[0]?.quote ? ` It says: “${p.evidence[0].quote}”` : ""}`}
                choices={[
                  { label: `Move to ${p.target}`, primary: true, done: `Moved to ${p.target}`, run: () => move(p, true),
                    undo: () => reopenOrganize(workspace, [{ origin: p.origin, source: p.source }]) },
                  { label: "Keep where it is", done: "Kept where it is", run: () => move(p, false),
                    undo: () => reopenOrganize(workspace, [{ origin: p.origin, source: p.source }]) },
                ]}
                onChoose={(choice) => void decide(proposalKey(p), choice)} />
            ))}
            {proposals.length > SHOWN && <More count={proposals.length - SHOWN} href="/organize/#review" what="to sort into projects" />}
            {drafts.slice(0, SHOWN).map((m) => {
              const replaces = m.amends ? byId.get(m.amends) : undefined;
              return (
                <Row key={`draft:${m.id}`} icon={Sparkles} busy={busy !== null}
                  title={replaces ? "Imprint would reword something it knows about you" : "Imprint noticed something about you"}
                  detail={`“${m.text}”${replaces ? ` It would replace: “${replaces.text}”` : ""}`}
                  choices={[
                    { label: replaces ? "Use the new wording" : "Yes, remember this", primary: true, done: "Remembered",
                      run: () => changeCoreMemory(m.id, { state: "active" }), undo: () => changeCoreMemory(m.id, { state: "proposed" }) },
                    { label: replaces ? "Leave as it was" : "No, that is wrong", done: "Left out",
                      run: () => changeCoreMemory(m.id, { state: "retired" }), undo: () => changeCoreMemory(m.id, { state: "proposed" }) },
                  ]}
                  onChoose={(choice) => void decide(`draft:${m.id}`, choice)} />
              );
            })}
            {drafts.length > SHOWN && <More count={drafts.length - SHOWN} href="/core/#proposed" what="about you" />}
            {conflicts.slice(0, SHOWN).map((m) => <ConflictRow key={`conflict:${m.id}`} memory={m} byId={byId} busy={busy !== null}
              onChoose={(choice) => void decide(`conflict:${m.id}`, choice)} />)}
            {conflicts.length > SHOWN && <More count={conflicts.length - SHOWN} href="/core/#conflicts" what="that contradict each other" />}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function ConflictRow({ memory, byId, busy, onChoose }: {
  memory: CoreMemory; byId: Map<string, CoreMemory>; busy: boolean; onChoose: (choice: Choice) => void;
}) {
  const older = memory.conflicts.filter((c) => c.resolution === "").map((c) => byId.get(c.id)?.text).filter(Boolean);
  const settle = (keep: CoreKeep, label: string, done: string, primary = false): Choice => ({
    label, primary, done, run: () => resolveCoreConflict(memory.id, keep), undo: () => resolveCoreConflict(memory.id, "open"),
  });
  return (
    <Row icon={GitCompareArrows} busy={busy}
      title="Two things Imprint knows about you disagree"
      detail={`Newer: “${memory.text}”${older.length ? ` Earlier: “${older.join("” and “")}”` : ""}`}
      choices={[settle("newer", "Use the newer", "Kept the newer one", true), settle("older", "Keep the earlier", "Kept the earlier one"),
        settle("both", "Keep both", "Kept both")]}
      onChoose={onChoose} />
  );
}

function Row({ icon: Icon, tone, title, detail, choices, onChoose, busy, more }: {
  icon: LucideIcon; tone?: "problem"; title: string; detail?: string; choices: Choice[];
  onChoose: (choice: Choice) => void; busy: boolean; more?: { href: string; label: string };
}) {
  return (
    <li className="flex flex-wrap items-start gap-x-3 gap-y-2 py-3 first:pt-0 last:pb-0">
      <Icon className={`mt-0.5 size-4 shrink-0 ${tone === "problem" ? "text-destructive" : "text-muted-foreground"}`} aria-hidden />
      <div className="min-w-0 flex-1 basis-56">
        <p className="text-sm font-medium">{title}</p>
        {detail && <p className="line-clamp-3 text-xs text-muted-foreground" title={detail}>{detail}</p>}
      </div>
      <div className="ml-7 flex flex-wrap items-center gap-2 sm:ml-0">
        {choices.map((choice) => (
          <Button key={choice.label} size="sm" variant={choice.primary ? "default" : "outline"} disabled={busy} onClick={() => onChoose(choice)}>
            {choice.label}
          </Button>
        ))}
        {more && (
          <Link href={more.href} className="inline-flex items-center gap-1 rounded-sm text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
            {more.label}<ArrowRight className="size-3" aria-hidden />
          </Link>
        )}
      </div>
    </li>
  );
}

function More({ count, href, what }: { count: number; href: string; what: string }) {
  return (
    <li className="py-2.5 last:pb-0">
      <Link href={href} className="inline-flex items-center gap-1 rounded-sm text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
        {count.toLocaleString()} more {what}<ArrowRight className="size-3" aria-hidden />
      </Link>
    </li>
  );
}
