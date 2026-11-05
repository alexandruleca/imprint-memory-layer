"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { ArrowRight, Check, Undo2, X, WandSparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { PageHeader } from "@/components/split-page";
import { decideOrganize, enqueueCommand, getOrganize, undoOrganize, type OrganizeProposal, type OrganizeReview } from "@/lib/api";
import { ago } from "@/lib/queue";

const DESCRIPTION = "Organize reads the memories that sit in catch-alls (no project, Desktop, unassigned, URLs, desktop conversations) and finds the project each belongs to, with the evidence. A source that names exactly one project's folder, or one project's distinctive name at least twice, moves by itself and can be undone; the rest wait for you. It also writes a short profile of each project and suggests core memories. Moves hold through refreshes and reach paired devices.";
const METHODS: Record<OrganizeProposal["method"], string> = {
  path: "Mentions its folder", name: "Names the project", "local-model": "Local model, quoted",
};
const originLabel = (origin: string) => origin || "No project";
const key = (p: Pick<OrganizeProposal, "origin" | "source">) => `${p.origin}\u0000${p.source}`;

function ProposalRow({ proposal, busy, onDecide, onUndo }: {
  proposal: OrganizeProposal; busy: boolean; onDecide: (items: OrganizeProposal[], accept: boolean) => void;
  onUndo?: (items: OrganizeProposal[]) => void;
}) {
  return (
    <li className="space-y-1.5 px-4 py-3 sm:px-6" aria-label={proposal.source}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="min-w-0 truncate font-mono text-xs">{proposal.source}</span>
        <Badge variant="outline">{originLabel(proposal.origin)}</Badge>
        <Badge variant="secondary">{METHODS[proposal.method]}</Badge>
        {proposal.status === "pending" && proposal.strength === "strong" && <Badge variant="outline">Strong evidence</Badge>}
        <span className="text-xs text-muted-foreground">{proposal.chunks} {proposal.chunks === 1 ? "chunk" : "chunks"}</span>
        {proposal.status === "pending" ? (
          <div className="ml-auto flex gap-1">
            <Button size="sm" disabled={busy} onClick={() => onDecide([proposal], true)}><Check /> Move</Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => onDecide([proposal], false)}><X /> Keep here</Button>
          </div>
        ) : proposal.status === "auto" && onUndo ? (
          <div className="ml-auto flex items-center gap-2">
            <span className="text-xs text-muted-foreground">to {proposal.target}</span>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => onUndo([proposal])}><Undo2 /> Undo</Button>
          </div>
        ) : (
          <Badge className="ml-auto" variant={proposal.status === "rejected" ? "outline" : "default"}>
            {proposal.status === "accepted" ? "Moved" : proposal.status === "auto" ? "Moved automatically" : "Kept"}
          </Badge>
        )}
      </div>
      {proposal.evidence[0]?.quote && (
        <p className="border-l-2 border-border pl-2 text-xs text-muted-foreground">“{proposal.evidence[0].quote}”</p>
      )}
    </li>
  );
}

export default function OrganizePage() {
  const [data, setData] = useState<OrganizeReview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [withoutModel, setWithoutModel] = useState(false);

  const load = async () => {
    try { setData(await getOrganize()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  useEffect(() => {
    let live = true;
    getOrganize().then(
      (d) => { if (live) setData(d); },
      (e) => { if (live) setError(e instanceof Error ? e.message : String(e)); },
    );
    return () => { live = false; };
  }, []);

  async function run() {
    setBusy(true);
    try {
      await enqueueCommand("organize", withoutModel ? { no_model: true } : {});
      toast.success("Organize queued", { description: "Follow it in Queue; refresh this page when it finishes." });
    } catch (e) {
      toast.error("Could not queue Organize", { description: e instanceof Error ? e.message : String(e) });
    } finally { setBusy(false); }
  }

  async function undo(items: OrganizeProposal[]) {
    if (!data) return;
    setBusy(true);
    try {
      const result = await undoOrganize(data.workspace, items.map(({ origin, source }) => ({ origin, source })));
      for (const failure of result.failed) toast.error(`Could not move ${failure.source} back`, { description: failure.error });
      if (result.undone) toast.success(result.undone === 1 ? "Moved back" : `Moved ${result.undone} sources back`);
      await load();
    } catch (e) {
      toast.error("Could not undo the move", { description: e instanceof Error ? e.message : String(e) });
    } finally { setBusy(false); }
  }

  async function decide(items: OrganizeProposal[], accept: boolean) {
    if (!data) return;
    setBusy(true);
    try {
      const result = await decideOrganize(data.workspace, items.map(({ origin, source }) => ({ origin, source })), accept);
      for (const failure of result.failed) toast.error(`Could not move ${failure.source}`, { description: failure.error });
      await load();
    } catch (e) {
      toast.error("Could not save the decision", { description: e instanceof Error ? e.message : String(e) });
    } finally { setBusy(false); }
  }

  const header = (
    <PageHeader title="Organize" description={DESCRIPTION} actions={
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <Switch aria-label="Without the local helper" checked={withoutModel} onCheckedChange={setWithoutModel} />
          Without the local helper
        </label>
        <Button disabled={busy} onClick={() => void run()}><WandSparkles /> Run Organize</Button>
      </div>
    } />
  );

  if (!data) return (
    <div className="mx-auto max-w-5xl">
      {header}
      <div className="space-y-4 px-4 sm:px-8">
        {error ? <p role="alert" className="text-sm text-destructive">Could not load Organize: {error}</p>
          : <><Skeleton className="h-32 w-full rounded-xl" /><Skeleton className="h-48 w-full rounded-xl" /></>}
      </div>
    </div>
  );

  const pending = data.proposals.filter((p) => p.status === "pending");
  const moved = data.proposals.filter((p) => p.status === "auto").sort((a, b) => (b.decided_at ?? 0) - (a.decided_at ?? 0));
  const decided = data.proposals.filter((p) => p.status === "accepted" || p.status === "rejected");
  const background = data.background;
  const byTarget = new Map<string, OrganizeProposal[]>();
  for (const proposal of pending) byTarget.set(proposal.target, [...(byTarget.get(proposal.target) ?? []), proposal]);
  const summary = data.last_run ? JSON.parse(data.last_run.summary) as { profiles: number; pending: number; moved?: number; core: number; model: boolean } : null;

  return (
    <div className="mx-auto max-w-5xl pb-10">
      {header}
      <div className="space-y-4 px-4 sm:px-8">
        <p className="text-sm text-muted-foreground" role="status">
          {data.last_run && summary
            ? <>Last run {ago(data.last_run.finished_at)}: {summary.profiles} profiles, {summary.moved ?? 0} moved, {summary.pending} moves to review, {summary.core} core memory proposals{summary.model ? "" : ", without the local helper"}.{" "}
                {summary.core > 0 && <Link href="/core/" className="underline underline-offset-2">Review core memories</Link>}</>
            : "Organize has not run in this workspace yet."}
        </p>
        <p className="text-sm text-muted-foreground" aria-label="How Organize runs">
          {background?.enabled
            ? "Runs by itself whenever the queue is idle, after background tagging, and looks only at what changed."
            : "Runs when you start it."}{" "}
          {background && !background.auto_move && "Every move waits for your review. "}
          <Link href="/settings/#settings-organize" className="underline underline-offset-2">Organize settings</Link>
        </p>

        {moved.length > 0 && (
          <Card className="gap-0 pb-0" aria-label="Moved automatically">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">Moved automatically <span className="text-sm font-normal text-muted-foreground tabular-nums">{moved.length}</span></CardTitle>
              <CardDescription>Each named one project clearly. Undo moves a source back to where it was and keeps it there.</CardDescription>
            </CardHeader>
            <CardContent className="mt-4 border-t border-border px-0">
              <ul className="divide-y divide-border">
                {moved.map((p) => <ProposalRow key={key(p)} proposal={p} busy={busy} onDecide={() => {}} onUndo={(i) => void undo(i)} />)}
              </ul>
            </CardContent>
          </Card>
        )}

        {byTarget.size === 0 ? (
          <Empty className="border border-dashed border-border">
            <EmptyHeader>
              <EmptyTitle>No moves to review</EmptyTitle>
              <EmptyDescription>{moved.length ? "Every proposal Organize made was strong enough to apply." : "Run Organize to look for memories in catch-alls that belong to a project."}</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : [...byTarget.entries()].map(([target, items]) => (
          <Card key={target} className="gap-0 pb-0" aria-label={`Moves to ${target}`}>
            <CardHeader className="flex flex-row flex-wrap items-center gap-2">
              <CardTitle className="flex items-center gap-2"><ArrowRight className="size-4" aria-hidden /> {target}</CardTitle>
              <span className="text-sm text-muted-foreground tabular-nums">{items.length}</span>
              <div className="ml-auto flex gap-1">
                <Button size="sm" disabled={busy} onClick={() => void decide(items, true)}>Move all {items.length}</Button>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => void decide(items, false)}>Keep all</Button>
              </div>
            </CardHeader>
            <CardContent className="mt-4 border-t border-border px-0">
              <ul className="divide-y divide-border">
                {items.map((p) => <ProposalRow key={key(p)} proposal={p} busy={busy} onDecide={(i, a) => void decide(i, a)} />)}
              </ul>
            </CardContent>
          </Card>
        ))}

        {data.profiles.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Project profiles</CardTitle>
              <CardDescription>Written by the local helper from each project&apos;s memories; search and chat find them too.</CardDescription>
            </CardHeader>
            <CardContent>
              <dl className="grid gap-4 sm:grid-cols-2">
                {data.profiles.map((profile) => (
                  <div key={profile.project} className="space-y-1">
                    <dt className="text-sm font-medium">{profile.project}</dt>
                    <dd className="text-sm whitespace-pre-line text-muted-foreground">
                      {profile.text.replace(/^Project profile: [^\n]*\n\n/, "")}
                    </dd>
                  </div>
                ))}
              </dl>
            </CardContent>
          </Card>
        )}

        {decided.length > 0 && (
          <Collapsible>
            <CollapsibleTrigger className="text-sm text-muted-foreground underline-offset-2 hover:underline">
              Decided ({decided.length})
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-3">
              <Card className="gap-0 py-0">
                <ul className="divide-y divide-border">
                  {decided.map((p) => <ProposalRow key={key(p)} proposal={p} busy={busy} onDecide={() => {}} />)}
                </ul>
              </Card>
            </CollapsibleContent>
          </Collapsible>
        )}
      </div>
    </div>
  );
}
