"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { BookOpenText, FileText, MessagesSquare, Network, Tags } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { getTopics, getTopicDetail } from "@/lib/api";
import { ListSkeleton } from "@/components/loaders";
import { MigrateDialog } from "@/components/migrate-dialog";
import { PageHeader, PaneCount, PaneFilter, PaneItem, SplitPage } from "@/components/split-page";
import { SourceReader, type ReaderTarget } from "@/components/source-reader";
import { cn } from "@/lib/utils";
import type { TopicOverviewData, TopicDetailData, TopicSource } from "@/lib/types";

const DESCRIPTION = "Themes tagged across your memories. Pick one to see the conversations and files it comes from, and read them whole.";

function topicFromUrl() {
  return new URLSearchParams(window.location.search).get("topic");
}

export default function TopicsPage() {
  const [data, setData] = useState<TopicOverviewData | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [result, setResult] = useState<{ topic: string; detail?: TopicDetailData; error?: string }>({ topic: "" });
  const [project, setProject] = useState<string | null>(null);
  const [migrateOpen, setMigrateOpen] = useState(false);
  const [reading, setReading] = useState<ReaderTarget | null>(null);

  useEffect(() => {
    getTopics()
      .then((d) => { setData(d); setSelected((s) => s ?? topicFromUrl()); })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    getTopicDetail(selected)
      .then((detail) => { if (!cancelled) setResult({ topic: selected, detail }); })
      .catch((e) => { if (!cancelled) setResult({ topic: selected, error: e instanceof Error ? e.message : String(e) }); });
    return () => { cancelled = true; };
  }, [selected]);

  function select(name: string) {
    setSelected(name);
    setProject(null);
    window.history.replaceState(null, "", `?topic=${encodeURIComponent(name)}`);
  }

  const header = <PageHeader title="Topics" count={data?.topics.length} description={DESCRIPTION} />;

  if (error) return (
    <div>
      {header}
      <p role="alert" className="px-4 text-sm text-destructive sm:px-8">Topics could not load: {error}</p>
    </div>
  );

  if (!data) return (
    <SplitPage header={header} list={<ListSkeleton count={14} />}>
      <Skeleton className="h-40 w-full rounded-xl" />
    </SplitPage>
  );

  if (!data.topics.length) return (
    <div>
      {header}
      <div className="px-4 sm:px-8">
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon"><Tags aria-hidden /></EmptyMedia>
            <EmptyTitle>No topics yet</EmptyTitle>
            <EmptyDescription>Retag memories from <Link href="/commands/">Commands</Link> to give existing memories topics.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      </div>
    </div>
  );

  const q = filter.trim().toLowerCase();
  const topics = q ? data.topics.filter((t) => t.name.toLowerCase().includes(q)) : data.topics;
  const loading = !!selected && result.topic !== selected;
  const detail = !loading ? result.detail : undefined;
  const sources = detail ? detail.sources.filter((s) => project === null || s.project === project) : [];

  return (
    <SplitPage
      header={header}
      filter={<PaneFilter label="Filter topics" value={filter} onChange={setFilter} />}
      list={
        <nav aria-label="Topics" className="space-y-0.5">
          {topics.map((t) => (
            <PaneItem key={t.id} selected={selected === t.name} onClick={() => select(t.name)}>
              <span className="min-w-0 truncate">{t.name}</span>
              <PaneCount value={t.count} />
            </PaneItem>
          ))}
          {!topics.length && <p className="px-2.5 py-6 text-center text-sm text-muted-foreground">No topic matches &ldquo;{filter}&rdquo;.</p>}
        </nav>
      }
    >
      {!selected ? (
        <Empty className="min-h-64 border border-dashed">
          <EmptyHeader>
            <EmptyMedia variant="icon"><Tags aria-hidden /></EmptyMedia>
            <EmptyTitle>Select a topic</EmptyTitle>
            <EmptyDescription>Its memories are grouped by the conversation or file they came from.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : loading ? (
        <div className="space-y-4" aria-busy="true">
          <Skeleton className="h-28 w-full rounded-xl" />
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24 w-full rounded-xl" />)}
        </div>
      ) : result.error || !detail ? (
        <p role="alert" className="text-sm text-destructive">{selected} could not load: {result.error}</p>
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">{detail.topic}</CardTitle>
              <CardDescription>
                {detail.total.toLocaleString()} {detail.total === 1 ? "memory" : "memories"} from {detail.sources.length.toLocaleString()} {detail.sources.length === 1 ? "source" : "sources"}
              </CardDescription>
              <CardAction className="flex gap-2">
                <Button size="sm" variant="outline" nativeButton={false} render={<Link href={`/graph/?scope=${encodeURIComponent(`topic:${detail.topic}`)}`} />}>
                  <Network aria-hidden /> Graph
                </Button>
                <Button size="sm" variant="outline" onClick={() => setMigrateOpen(true)}>Migrate…</Button>
              </CardAction>
            </CardHeader>
            {detail.projects.length > 1 && (
              <CardContent>
                <div role="group" aria-label="Show sources from" className="flex flex-wrap gap-1.5">
                  <Chip on={project === null} onClick={() => setProject(null)}>All projects</Chip>
                  {detail.projects.map(([name, count]) => (
                    <Chip key={name} on={project === name} onClick={() => setProject(name)}>
                      {name || "No project"} <span className="text-muted-foreground tabular-nums">{count}</span>
                    </Chip>
                  ))}
                </div>
              </CardContent>
            )}
          </Card>

          <ul aria-label="Sources" className="space-y-3">
            {sources.map((s) => (
              <li key={`${s.project}\u0000${s.source}`}>
                <SourceCard group={s} topic={detail.topic} onRead={setReading} />
              </li>
            ))}
          </ul>
        </>
      )}

      {detail && (
        <MigrateDialog
          open={migrateOpen}
          preset={{ mode: "topic", value: detail.topic }}
          onClose={() => setMigrateOpen(false)}
          onDone={() => setMigrateOpen(false)}
        />
      )}
      <SourceReader target={reading} onClose={() => setReading(null)} />
    </SplitPage>
  );
}

function Chip({ on, className, ...props }: React.ComponentProps<"button"> & { on: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-full border px-3 text-xs transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
        on ? "border-foreground bg-foreground text-background [&_span]:text-background/70" : "border-border bg-background hover:bg-muted",
        className,
      )}
      {...props}
    />
  );
}

const SHOWN = 2;

/** One conversation or file a topic comes from, its tagged passages, and a way to read it whole. */
function SourceCard({ group, topic, onRead }: { group: TopicSource; topic: string; onRead: (target: ReaderTarget) => void }) {
  const [all, setAll] = useState(false);
  const conversation = group.source_type === "conversation";
  const Icon = conversation ? MessagesSquare : FileText;
  const name = group.title || group.source || "Memories saved without a source";
  const shown = all ? group.chunks : group.chunks.slice(0, SHOWN);
  const read = (chunk?: number) => onRead({ source: group.source, project: group.project, topic, chunk, title: group.title });
  return (
    <Card size="sm" className="gap-3">
      <CardHeader>
        <div className="flex min-w-0 items-start gap-3">
          <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground"><Icon className="size-4" aria-hidden /></span>
          <div className="min-w-0 space-y-0.5">
            <CardTitle className="line-clamp-2 leading-snug" title={name}>{name}</CardTitle>
            <CardDescription className="flex flex-wrap gap-x-1.5 text-xs">
              {group.title && <span className="font-mono">{group.source}</span>}
              <span>{group.title && "· "}{group.project || "No project"}</span>
              <span>· {group.chunks.length} {group.chunks.length === 1 ? "passage" : "passages"}</span>
            </CardDescription>
          </div>
        </div>
        {group.source && (
          <CardAction>
            <Button size="sm" variant="secondary" onClick={() => read()}>
              <BookOpenText aria-hidden /> Read {conversation ? "conversation" : "source"}
            </Button>
          </CardAction>
        )}
      </CardHeader>
      <CardContent className="space-y-1.5">
        {shown.map((chunk) => (
          <button
            key={chunk.id}
            type="button"
            disabled={!group.source}
            onClick={() => read(chunk.chunk_index)}
            className="block w-full rounded-lg border border-border/70 bg-muted/30 px-3 py-2 text-left text-xs leading-relaxed text-foreground/80 transition-colors outline-none hover:border-foreground/20 hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none"
          >
            <span className="line-clamp-2 whitespace-pre-wrap">{chunk.content}</span>
          </button>
        ))}
        {group.chunks.length > SHOWN && (
          <Button variant="ghost" size="xs" className="font-normal text-muted-foreground" onClick={() => setAll((v) => !v)}>
            {all ? "Show fewer" : `Show ${group.chunks.length - SHOWN} more`}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
