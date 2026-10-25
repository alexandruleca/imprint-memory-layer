"use client";

import { useEffect, useState } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Empty, EmptyDescription } from "@/components/ui/empty";
import { Hint } from "@/components/hint";
import { Spinner } from "@/components/loaders";
import { CancelButton } from "@/components/ingestion-progress";
import { getQueue, resumeBackgroundTagging, tailJobStream } from "@/lib/api";
import { useFollowOutput } from "@/lib/use-follow-output";
import {
  CheckCircle2,
  XCircle,
  Ban,
  Clock,
  PlayCircle,
  PauseCircle,
  ChevronRight,
  ChevronDown,
  RotateCw,
  Sparkles,
} from "lucide-react";
import type {
  BackgroundTagging,
  BackgroundTaggingState,
  QueueJob,
  QueueJobStatus,
  QueueResponse,
} from "@/lib/types";

function formatDuration(seconds: number): string {
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function formatTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString();
}

function jobDuration(j: QueueJob): number | null {
  if (j.started_at && j.ended_at) return j.ended_at - j.started_at;
  if (j.started_at && j.status === "running") return Date.now() / 1000 - j.started_at;
  return null;
}

function summarizeBody(body: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(body ?? {})) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    if (v === true) parts.push(`--${k}`);
    else parts.push(`${k}=${String(v)}`);
  }
  return parts.join(" ") || "—";
}

/** One line of a job list that has nothing in it. */
function EmptyList({ children }: { children: React.ReactNode }) {
  return (
    <Empty className="items-start p-0 text-left">
      <EmptyDescription className="text-xs">{children}</EmptyDescription>
    </Empty>
  );
}

function StatusIcon({ status }: { status: QueueJobStatus }) {
  switch (status) {
    case "running":
      return <Spinner className="w-3.5 h-3.5 text-amber-600" />;
    case "queued":
      return <Clock className="w-3.5 h-3.5 text-muted-foreground" />;
    case "done":
      return <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />;
    case "failed":
      return <XCircle className="w-3.5 h-3.5 text-red-500" />;
    case "cancelled":
      return <Ban className="w-3.5 h-3.5 text-muted-foreground" />;
    case "yielded":
      return <PauseCircle className="w-3.5 h-3.5 text-muted-foreground" />;
  }
}

const memories = (n: number) => `${n.toLocaleString()} ${n === 1 ? "memory" : "memories"}`;

const STATE_LABEL: Record<BackgroundTaggingState, string> = {
  off: "Off",
  running: "Running",
  yielded: "Yielded",
  waiting: "Waiting",
  paused: "Paused",
  failed: "Failed",
  idle: "Idle",
};

function describe(bg: BackgroundTagging): string {
  const waiting = bg.pending != null ? memories(bg.pending) : "Memories";
  switch (bg.state) {
    case "off":
      return bg.pending
        ? `${waiting} wait for LLM tagging. Turn on LLM tagging (tagger.llm) in Settings to tag them here.`
        : "LLM tagging (tagger.llm) is off. Memories keep their keyword and zero-shot tags.";
    case "running":
      return `Tagging, newest first: ${waiting} to go. Every other job runs first; this one steps aside between memories.`;
    case "yielded":
      return `Stepped aside for another job. ${waiting} wait; tagging resumes when the queue is idle.`;
    case "waiting":
      return `${waiting} wait for LLM tagging. It starts when no other job runs or waits.`;
    case "paused":
      return `Cancelled with ${waiting.toLowerCase()} waiting. It resumes when more memories wait.`;
    case "failed":
      return `The last run failed; see its log. ${waiting} wait${bg.resume_at ? `; it retries at ${formatTime(bg.resume_at)}` : ""}.`;
    case "idle":
      return "Every memory stored with LLM tagging on has its LLM tags.";
  }
}

/** Background LLM tagging (ADR 0067): learn and ingest leave LLM tags to it,
 * and it gives way to every other job. */
function BackgroundCard({
  bg,
  expandedId,
  onToggleLog,
}: {
  bg: BackgroundTagging;
  expandedId: string | null;
  onToggleLog: (id: string) => void;
}) {
  const [resuming, setResuming] = useState(false);
  const job = bg.job;
  const running = bg.state === "running" && job;

  async function resume() {
    setResuming(true);
    try {
      await resumeBackgroundTagging();
    } catch (e) {
      toast.error("Could not resume background tagging", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setResuming(false);
    }
  }

  return (
    <Card role="region" aria-labelledby="background-tagging-title">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex flex-wrap items-center gap-2">
          <Sparkles className="w-4 h-4 text-muted-foreground" aria-hidden />
          <span id="background-tagging-title">Background tagging</span>
          <Badge variant={bg.state === "failed" ? "destructive" : "secondary"} className="text-[10px]">
            {STATE_LABEL[bg.state]}
          </Badge>
          {bg.pending != null && bg.pending > 0 && (
            <span className="ml-auto whitespace-nowrap text-xs font-normal tabular-nums text-muted-foreground">
              {memories(bg.pending)} waiting
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <p className="flex-1 text-xs text-muted-foreground">{describe(bg)}</p>
          {(bg.state === "paused" || bg.state === "failed") && (
            <Button variant="outline" size="sm" onClick={resume} disabled={resuming}>
              {resuming ? <Spinner /> : <RotateCw />}
              Resume now
            </Button>
          )}
        </div>
        {running && job.percent !== undefined && (
          <Progress value={Math.min(job.percent, 100)} aria-label="Background tagging progress"
            className="[&_[data-slot=progress-track]]:h-2" />
        )}
        {job && (
          <JobRow
            job={job}
            expandedId={expandedId}
            onToggleLog={onToggleLog}
            cancellable={!!running}
          />
        )}
      </CardContent>
    </Card>
  );
}

export default function QueuePage() {
  const [data, setData] = useState<QueueResponse | null>(null);
  const [openLog, setOpenLog] = useState<string | null>(null);

  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const q = await getQueue(50);
        if (!cancelled) {
          setData(q);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setData({ active: null, queued: [], recent: [] });
        }
      }
    }
    poll();
    const t = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  if (!data) {
    return (
      <div className="p-8">
        <h2 className="text-2xl font-bold">Queue</h2>
        <p className="mt-2 flex items-center gap-2 text-sm text-muted-foreground"><Spinner /> Loading…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8 space-y-2">
        <h2 className="text-2xl font-bold">Queue</h2>
        <p role="alert" className="text-sm text-destructive">
          Can&apos;t reach the queue API: {error}. Restart the dashboard
          (<code>imprint ui restart</code>) to pick up the new endpoints.
        </p>
      </div>
    );
  }

  const { active, queued, recent, background } = data;
  const toggleLog = (id: string) => setOpenLog(openLog === id ? null : id);

  return (
    <div className="p-8 space-y-6 max-w-5xl">
      <div className="flex items-end justify-between">
        <div>
          <h2 className="text-2xl font-bold">Queue</h2>
          <p className="text-sm text-muted-foreground">
            One ingest/refresh/retag runs at a time; background tagging runs
            only when nothing else waits. Cancel sends SIGTERM → SIGKILL (3s)
            to the whole process group, so any in-flight LLM tagger call dies
            with it.
          </p>
        </div>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <PlayCircle className="w-4 h-4 text-amber-600" />
            Active
          </CardTitle>
        </CardHeader>
        <CardContent>
          {active ? (
            <JobRow
              job={active}
              expandedId={openLog}
              onToggleLog={(id) => setOpenLog(openLog === id ? null : id)}
              cancellable
            />
          ) : (
            <EmptyList>Nothing running.</EmptyList>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <Clock className="w-4 h-4 text-muted-foreground" />
            Queued ({queued.length})
          </CardTitle>
        </CardHeader>
        <CardContent>
          {queued.length === 0 ? (
            <EmptyList>Queue is empty.</EmptyList>
          ) : (
            <div className="space-y-2">
              {queued.map((j, i) => (
                <JobRow
                  key={j.id}
                  job={j}
                  indexLabel={`${i + 1}.`}
                  expandedId={openLog}
                  onToggleLog={(id) => setOpenLog(openLog === id ? null : id)}
                  cancellable
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {background && (
        <BackgroundCard bg={background} expandedId={openLog} onToggleLog={toggleLog} />
      )}

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Recent</CardTitle>
        </CardHeader>
        <CardContent>
          {recent.length === 0 ? (
            <EmptyList>No history yet.</EmptyList>
          ) : (
            <div className="space-y-2">
              {recent.map((j) => (
                <JobRow
                  key={j.id}
                  job={j}
                  expandedId={openLog}
                  onToggleLog={(id) => setOpenLog(openLog === id ? null : id)}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function JobRow({
  job,
  indexLabel,
  cancellable = false,
  expandedId,
  onToggleLog,
}: {
  job: QueueJob;
  indexLabel?: string;
  cancellable?: boolean;
  expandedId: string | null;
  onToggleLog: (id: string) => void;
}) {
  const dur = jobDuration(job);
  const durStr = dur !== null ? formatDuration(dur) : null;
  const expanded = expandedId === job.id;

  return (
    <div className="rounded-md border border-border bg-card/40">
      <div className="flex items-center gap-2 px-3 py-2">
        {indexLabel && (
          <span className="tabular-nums text-xs text-muted-foreground w-6">
            {indexLabel}
          </span>
        )}
        <StatusIcon status={job.status} />
        <span className="sr-only">{job.status}</span>
        <Badge variant="outline" className="text-[10px] uppercase shrink-0">
          {job.command}
        </Badge>
        <span className="text-xs text-muted-foreground truncate flex-1">
          {summarizeBody(job.body)}
        </span>
        {job.status === "running" && job.percent !== undefined && (
          <span className="text-[11px] tabular-nums text-muted-foreground shrink-0">
            {Math.round(job.percent)}% · {job.processed ?? 0}/{job.total ?? 0}
          </span>
        )}
        {durStr && (
          <span className="text-[11px] tabular-nums text-muted-foreground shrink-0">
            {durStr}
          </span>
        )}
        {job.exit_code != null && job.status !== "running" && (
          <Badge
            variant="secondary"
            className={`text-[10px] shrink-0 ${
              job.status === "failed" ? "text-red-600" : ""
            }`}
          >
            exit {job.exit_code}
          </Badge>
        )}
        <Hint label={expanded ? "Hide log" : "Show log"}>
          <Button
            variant="ghost"
            size="icon-sm"
            className="text-muted-foreground"
            onClick={() => onToggleLog(job.id)}
            aria-label={expanded ? "Hide log" : "Show log"}
            aria-expanded={expanded}
          >
            {expanded ? (
              <ChevronDown className="w-3.5 h-3.5" />
            ) : (
              <ChevronRight className="w-3.5 h-3.5" />
            )}
          </Button>
        </Hint>
        {cancellable && (job.status === "queued" || job.status === "running") && (
          <CancelButton
            job={job}
            label={job.status === "running" ? "Cancel job" : "Remove from queue"}
            className="h-7 px-2 text-muted-foreground hover:text-red-600"
            iconClassName="w-3.5 h-3.5"
          />
        )}
      </div>
      {expanded && (
        <div className="border-t border-border px-3 py-2 space-y-1.5">
          <div className="flex gap-3 text-[11px] text-muted-foreground tabular-nums">
            <span>created {formatTime(job.created_at)}</span>
            <span>started {formatTime(job.started_at)}</span>
            <span>ended {formatTime(job.ended_at)}</span>
            {job.pid != null && <span>pid {job.pid}</span>}
          </div>
          {job.error && (
            <p className="text-[11px] text-destructive font-mono">{job.error}</p>
          )}
          <JobLog key={job.id} jobId={job.id} live={job.status === "running"} />
        </div>
      )}
    </div>
  );
}

function JobLog({ jobId, live }: { jobId: string; live: boolean }) {
  const [lines, setLines] = useState<string[]>([]);
  const scroller = useFollowOutput(lines);

  useEffect(() => {
    const ctrl = tailJobStream(jobId, (ev) => {
      if (ev.type === "output") {
        setLines((prev) => [...prev, ev.text as string]);
      }
    });
    return () => ctrl.abort();
  }, [jobId]);

  return (
    <ScrollArea className="h-40" viewportRef={scroller}>
      <pre className="text-[10.5px] font-mono whitespace-pre-wrap break-words bg-muted rounded p-2">
        {lines.join("") || (live ? "waiting for output…" : "(no output)")}
      </pre>
    </ScrollArea>
  );
}
