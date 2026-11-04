"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Check, Pencil, RotateCcw, Sparkles, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/split-page";
import {
  addCoreMemory, changeCoreMemory, getCoreMemories, getStats,
  type CoreKind, type CoreMemories, type CoreMemory,
} from "@/lib/api";

const DESCRIPTION = "Short statements about you that every agent session receives at its start: how you like things done, rules to follow, facts about you. Only you make one active. Agents and Organize can only propose them.";
const KINDS: { value: CoreKind; label: string }[] = [
  { value: "preference", label: "Preference" },
  { value: "instruction", label: "Instruction" },
  { value: "fact", label: "Fact about me" },
];
const EVERYWHERE = "*";

const scopeLabel = (scope: string) => (scope === EVERYWHERE ? "Every project" : scope);

async function attempt<T>(action: () => Promise<T>, failure: string) {
  try {
    return await action();
  } catch (e) {
    toast.error(failure, { description: e instanceof Error ? e.message : String(e) });
    return null;
  }
}

/** Kind and scope, as native selects so they stay small inside a row. */
function KindScope({ kind, scope, projects, onKind, onScope, label }: {
  kind: CoreKind; scope: string; projects: string[]; label: string;
  onKind: (kind: CoreKind) => void; onScope: (scope: string) => void;
}) {
  const scopes = [EVERYWHERE, ...projects, ...(scope !== EVERYWHERE && !projects.includes(scope) ? [scope] : [])];
  return (
    <div className="flex flex-wrap gap-2 text-xs">
      <select aria-label={`Kind of ${label}`} value={kind} onChange={(e) => onKind(e.target.value as CoreKind)}
        className="h-8 rounded-md border border-input bg-transparent px-2">
        {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
      </select>
      <select aria-label={`Where ${label} applies`} value={scope} onChange={(e) => onScope(e.target.value)}
        className="h-8 max-w-56 rounded-md border border-input bg-transparent px-2">
        {scopes.map((s) => <option key={s} value={s}>{scopeLabel(s)}</option>)}
      </select>
    </div>
  );
}

function CoreRow({ memory, projects, maxText, onChange }: {
  memory: CoreMemory; projects: string[]; maxText: number; onChange: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(memory.text);
  const change = async (values: Parameters<typeof changeCoreMemory>[1], failure: string) => {
    if (await attempt(() => changeCoreMemory(memory.id, values), failure)) await onChange();
  };
  const quote = memory.evidence.find((e) => e.quote)?.quote;
  return (
    <li className="space-y-2 px-4 py-3 sm:px-6" aria-label={memory.text}>
      {editing ? (
        <div className="space-y-2">
          <Textarea aria-label="Core memory text" value={draft} maxLength={maxText} onChange={(e) => setDraft(e.target.value)} />
          <div className="flex gap-2">
            <Button size="sm" disabled={!draft.trim() || draft === memory.text}
              onClick={async () => { await change({ text: draft }, "Could not save the core memory"); setEditing(false); }}>
              <Check /> Save
            </Button>
            <Button size="sm" variant="ghost" onClick={() => { setDraft(memory.text); setEditing(false); }}>Cancel</Button>
          </div>
        </div>
      ) : (
        <p className="text-sm leading-relaxed">{memory.text}</p>
      )}
      {quote && <p className="border-l-2 border-border pl-2 text-xs text-muted-foreground">“{quote}”</p>}
      <div className="flex flex-wrap items-center gap-2">
        <KindScope kind={memory.kind} scope={memory.scope} projects={projects} label={`“${memory.text.slice(0, 40)}”`}
          onKind={(kind) => void change({ kind }, "Could not change the kind")}
          onScope={(scope) => void change({ scope }, "Could not change where it applies")} />
        {memory.origin !== "user" && <Badge variant="outline">From {memory.origin === "organize" ? "Organize" : "an agent"}</Badge>}
        <div className="ml-auto flex gap-1">
          {!editing && memory.state !== "retired" && (
            <Button size="sm" variant="ghost" aria-label="Edit" onClick={() => setEditing(true)}><Pencil /></Button>
          )}
          {memory.state === "proposed" && (
            <Button size="sm" onClick={() => void change({ state: "active" }, "Could not approve it")}><Check /> Approve</Button>
          )}
          {memory.state !== "retired" ? (
            <Button size="sm" variant="ghost" onClick={() => void change({ state: "retired" }, "Could not retire it")}>
              <X /> {memory.state === "proposed" ? "Reject" : "Retire"}
            </Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => void change({ state: "active" }, "Could not restore it")}><RotateCcw /> Restore</Button>
          )}
        </div>
      </div>
    </li>
  );
}

function Section({ title, description, memories, children }: {
  title: string; description: string; memories: CoreMemory[]; children: (memory: CoreMemory) => React.ReactNode;
}) {
  return (
    <Card className="gap-0 pb-0">
      <CardHeader>
        <CardTitle>{title} <span className="text-muted-foreground tabular-nums">{memories.length}</span></CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="mt-4 border-t border-border px-0">
        <ul className="divide-y divide-border">{memories.map(children)}</ul>
      </CardContent>
    </Card>
  );
}

export default function CoreMemoriesPage() {
  const [data, setData] = useState<CoreMemories | null>(null);
  const [error, setError] = useState("");
  const [projects, setProjects] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [kind, setKind] = useState<CoreKind>("preference");
  const [scope, setScope] = useState(EVERYWHERE);

  const load = async () => {
    try { setData(await getCoreMemories()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  useEffect(() => {
    let live = true;
    getCoreMemories().then(
      (d) => { if (live) setData(d); },
      (e) => { if (live) setError(e instanceof Error ? e.message : String(e)); },
    );
    getStats().then((s) => { if (live) setProjects(s.projects.map(([name]) => name).filter(Boolean).sort()); }).catch(() => {});
    return () => { live = false; };
  }, []);

  if (!data) return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title="Core memories" description={DESCRIPTION} />
      <div className="space-y-4 px-4 sm:px-8">
        {error ? <p role="alert" className="text-sm text-destructive">Could not load core memories: {error}</p>
          : <><Skeleton className="h-32 w-full rounded-xl" /><Skeleton className="h-48 w-full rounded-xl" /></>}
      </div>
    </div>
  );

  const of = (state: CoreMemory["state"]) => data.memories.filter((m) => m.state === state);
  const [active, proposed, retired] = [of("active"), of("proposed"), of("retired")];
  const row = (memory: CoreMemory) => (
    <CoreRow key={`${memory.id}:${memory.updated_at}`} memory={memory} projects={projects} maxText={data.max_text} onChange={load} />
  );
  const add = async () => {
    if (await attempt(() => addCoreMemory(text, kind, scope), "Could not add the core memory")) {
      setText(""); await load();
    }
  };

  return (
    <div className="mx-auto max-w-4xl pb-10">
      <PageHeader title="Core memories" count={active.length} description={DESCRIPTION} />
      <div className="space-y-4 px-4 sm:px-8">
        <Card>
          <CardHeader>
            <CardTitle>Add one</CardTitle>
            <CardDescription>
              Write it as you would tell a new colleague, for example &ldquo;Commit only when I ask&rdquo;. It is active at once.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <Textarea aria-label="New core memory" placeholder="Prefers answers that lead with the result" value={text}
              maxLength={data.max_text} onChange={(e) => setText(e.target.value)} />
            <div className="flex flex-wrap items-center gap-2">
              <KindScope kind={kind} scope={scope} projects={projects} label="the new core memory" onKind={setKind} onScope={setScope} />
              <Button className="ml-auto" disabled={!text.trim()} onClick={() => void add()}><Sparkles /> Add</Button>
            </div>
          </CardContent>
        </Card>

        <div className="space-y-1.5">
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>Each session receives up to {data.budget.toLocaleString()} characters of them</span>
            <span className="tabular-nums">{data.used.toLocaleString()} used by the ones for every project</span>
          </div>
          <Progress value={Math.min(100, (100 * data.used) / data.budget)} aria-label="Session budget used" />
        </div>

        {proposed.length > 0 && (
          <Section title="Proposed" description="Suggested by agents or by Organize, with what they were based on. Approve the ones you want sessions to follow." memories={proposed}>
            {row}
          </Section>
        )}
        {active.length > 0 ? (
          <Section title="Active" description="Every session in a matching project receives these." memories={active}>{row}</Section>
        ) : (
          <Empty className="border border-dashed border-border">
            <EmptyHeader>
              <EmptyTitle>No active core memories</EmptyTitle>
              <EmptyDescription>Add one above, or approve a proposal. Sessions start without any until then.</EmptyDescription>
            </EmptyHeader>
          </Empty>
        )}
        {retired.length > 0 && (
          <Collapsible>
            <CollapsibleTrigger className="text-sm text-muted-foreground underline-offset-2 hover:underline">
              Retired and rejected ({retired.length})
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-3">
              <Section title="Retired" description="Sessions no longer receive these; restore one to make it active again." memories={retired}>{row}</Section>
            </CollapsibleContent>
          </Collapsible>
        )}
      </div>
    </div>
  );
}
