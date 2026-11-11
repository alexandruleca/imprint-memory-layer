"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Disc3, Download, FileText, Play, RotateCcw, Square, Trash2 } from "lucide-react";
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Markdown } from "@/components/markdown";
import { PageHeader } from "@/components/split-page";
import { clock } from "@/components/recording-indicator";
import {
  deleteRecording, deleteRecordingAudio, getRecordings, getRecordingTranscript, recordingAudioUrl,
  stopRecording, transcribeRecording, type Recording,
} from "@/lib/api";
import { levels, startMeeting, stopMeeting, useRecorder } from "@/lib/recorder";
import { readVoicePreferences } from "@/lib/voice-preferences";
import { useProjectNames } from "@/lib/use-projects";
import { useCached } from "@/lib/use-cached";

const DESCRIPTION = "Record a call or meeting: your microphone and, if you choose, what this computer plays (the other people). Imprint keeps the audio on this device, transcribes it locally with who said what, and makes the transcript searchable like any memory.";
const STATUS: Record<Recording["status"], { label: string; variant: "default" | "secondary" | "outline" | "destructive" }> = {
  recording: { label: "Recording", variant: "destructive" },
  stopped: { label: "Waiting to transcribe", variant: "secondary" },
  transcribing: { label: "Transcribing", variant: "secondary" },
  ready: { label: "Transcribed", variant: "default" },
  failed: { label: "Failed", variant: "destructive" },
};
const POLL_MS = 4000;

async function attempt<T>(action: () => Promise<T>, failure: string) {
  try { return await action(); }
  catch (e) { toast.error(failure, { description: e instanceof Error ? e.message : String(e) }); return null; }
}

function Meters() {
  const [values, setValues] = useState<[number, number]>([0, 0]);
  useEffect(() => {
    let frame = 0;
    const tick = () => { setValues(levels()); frame = requestAnimationFrame(tick); };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, []);
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {(["You", "Others"] as const).map((who, i) => (
        <div key={who} className="space-y-1">
          <span className="text-xs text-muted-foreground">{who}</span>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div className="h-full bg-primary transition-[width] duration-75" style={{ width: `${Math.round(values[i] * 100)}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function Start({ projects, defaultProject }: { projects: string[]; defaultProject: string }) {
  const recorder = useRecorder();
  const [title, setTitle] = useState("");
  const [project, setProject] = useState(defaultProject);
  const [others, setOthers] = useState(true);
  const [consent, setConsent] = useState(false);
  const [now, setNow] = useState(0);
  const recording = recorder.phase === "recording" || recorder.phase === "stopping";
  useEffect(() => {
    if (!recording) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [recording]);

  if (recording) return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center gap-3">
        <span className="size-2.5 animate-pulse rounded-full bg-destructive" aria-hidden />
        <CardTitle className="tabular-nums">{recorder.recording?.title} · {clock(((now || recorder.startedAt) - recorder.startedAt) / 1000)}</CardTitle>
        <Button className="ml-auto" variant="destructive" disabled={recorder.phase === "stopping"} onClick={() => void stopMeeting()}>
          <Square className="fill-current" /> {recorder.phase === "stopping" ? "Saving…" : "Stop and transcribe"}
        </Button>
      </CardHeader>
      <CardContent className="space-y-2">
        <Meters />
        {!recorder.others && <p className="text-xs text-muted-foreground">Only your microphone is being recorded.</p>}
        <p className="text-xs text-muted-foreground">You can move to other pages; recording continues until you stop it.</p>
      </CardContent>
    </Card>
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>New recording</CardTitle>
        <CardDescription>The microphone is the one chosen in Voice → Companion settings. The desktop app records what the computer plays directly (macOS asks you to allow it once); a browser asks which tab or screen to share, with its audio.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Input aria-label="Recording title" placeholder="Weekly sync" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
          <Input aria-label="Project" list="recording-projects" value={project} maxLength={256} onChange={(e) => setProject(e.target.value)} />
          <datalist id="recording-projects">{projects.map((p) => <option key={p} value={p} />)}</datalist>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <Switch aria-label="Include the computer's audio" checked={others} onCheckedChange={setOthers} />
          Include what this computer plays (the other people on the call)
        </label>
        <label className="flex items-start gap-2 text-sm">
          <Checkbox aria-label="Everyone knows it is being recorded" checked={consent} onCheckedChange={(v) => setConsent(v === true)} className="mt-0.5" />
          Everyone on this call knows it is being recorded. Recording people without their consent is illegal in many places.
        </label>
        {recorder.error && <p role="alert" className="text-sm text-destructive">{recorder.error}</p>}
        <Button disabled={!consent || !project.trim() || recorder.phase === "starting"}
          onClick={() => void startMeeting({ title, project: project.trim(), others, inputDevice: readVoicePreferences().inputDevice })}>
          <Disc3 /> {recorder.phase === "starting" ? "Starting…" : "Start recording"}
        </Button>
      </CardContent>
    </Card>
  );
}

/** A transcript without its title, which the dialog shows, one paragraph per
 * turn. Transcripts made before ADR 0173 put each turn on a line of its own,
 * which Markdown would join into one block of text. */
function transcriptBody(text: string) {
  return text.replace(/^# [^\n]*\n+/, "").replace(/([^\n])\n(?=[^\n])/g, "$1\n\n");
}

function Row({ recording, live, onChange, onTranscript, onDelete }: {
  recording: Recording; live: boolean; onChange: () => Promise<void>;
  onTranscript: (r: Recording) => void; onDelete: (r: Recording, audioOnly: boolean) => void;
}) {
  const [playing, setPlaying] = useState(false);
  const status = STATUS[recording.status];
  const interrupted = recording.status === "recording" && !live;
  const minutes = Math.max(1, Math.round(recording.duration / 60));
  return (
    <li className="space-y-2 px-4 py-3 sm:px-6" aria-label={recording.title}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{recording.title}</span>
        <Badge variant={status.variant}>{interrupted ? "Interrupted" : status.label}</Badge>
        <span className="text-xs text-muted-foreground">
          {new Date(recording.started_at * 1000).toLocaleString()} · {recording.status === "recording" ? "" : `${minutes} min · `}{recording.project}
          {recording.memories > 0 && ` · ${recording.memories} memories`}
        </span>
        <div className="ml-auto flex flex-wrap gap-1">
          {interrupted && (
            <Button size="sm" onClick={async () => { if (await attempt(() => stopRecording(recording.id), "Could not finish it")) await onChange(); }}>
              <Square /> Finish and transcribe
            </Button>
          )}
          {recording.transcript && <Button size="sm" variant="ghost" onClick={() => onTranscript(recording)}><FileText /> Transcript</Button>}
          {recording.audio_bytes > 0 && recording.status !== "recording" && (
            <>
              <Button size="sm" variant="ghost" aria-pressed={playing} onClick={() => setPlaying(!playing)}><Play /> Listen</Button>
              <a href={recordingAudioUrl(recording.id, true)} download className={buttonVariants({ variant: "ghost", size: "sm" })}>
                <Download /> Download
              </a>
            </>
          )}
          {(recording.status === "ready" || recording.status === "failed" || recording.status === "stopped") && recording.audio_bytes > 0 && (
            <Button size="sm" variant="ghost" onClick={async () => { if (await attempt(() => transcribeRecording(recording.id), "Could not queue it")) { toast.success("Transcription queued"); await onChange(); } }}>
              <RotateCcw /> Transcribe again
            </Button>
          )}
          {recording.audio_bytes > 0 && recording.status === "ready" && (
            <Button size="sm" variant="ghost" onClick={() => onDelete(recording, true)}>Delete audio</Button>
          )}
          {!live && <Button size="sm" variant="ghost" aria-label={`Delete ${recording.title}`} onClick={() => onDelete(recording, false)}><Trash2 /></Button>}
        </div>
      </div>
      {recording.status === "failed" && recording.error && <p role="alert" className="text-xs text-destructive">{recording.error}</p>}
      {playing && <audio controls autoPlay className="h-9 w-full" src={recordingAudioUrl(recording.id)} />}
    </li>
  );
}

export default function RecordingsPage() {
  const recorder = useRecorder();
  // The list shows at once from the last answer (ADR 0152), and is read
  // again every few seconds while a recording records or transcribes.
  const list = useCached("recordings", getRecordings);
  const data = list.data ?? null;
  const error = list.error ? list.error.message : "";
  const projects = useProjectNames({ order: "name" }).names;
  const [transcript, setTranscript] = useState<{ title: string; text: string } | null>(null);
  const [deleting, setDeleting] = useState<{ recording: Recording; audioOnly: boolean } | null>(null);

  const { refresh } = list;
  const load = refresh;
  const busy = data?.recordings.some((r) => r.status === "stopped" || r.status === "transcribing");
  const live = busy || recorder.phase !== "idle";
  // A recording that starts or stops is listed at once.
  const recorderMark = `${recorder.phase}|${recorder.stopped}`;
  const lastMark = useRef(recorderMark);
  useEffect(() => {
    if (lastMark.current === recorderMark) return;
    lastMark.current = recorderMark;
    void refresh();
  }, [recorderMark, refresh]);
  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [live, refresh]);

  async function showTranscript(recording: Recording) {
    const result = await attempt(() => getRecordingTranscript(recording.id), "Could not open the transcript");
    if (result) setTranscript({ title: recording.title, text: result.text });
  }

  async function confirmDelete() {
    if (!deleting) return;
    const { recording, audioOnly } = deleting;
    const done = await attempt<unknown>(() => (audioOnly ? deleteRecordingAudio(recording.id) : deleteRecording(recording.id)), "Could not delete it");
    setDeleting(null);
    if (done) await load();
  }

  return (
    <div className="mx-auto max-w-4xl pb-10">
      <PageHeader title="Recordings" count={data?.recordings.length} description={DESCRIPTION} updating={list.refreshing && !live} />
      <div className="space-y-4 px-4 sm:px-8">
        {data ? <Start projects={projects} defaultProject={data.default_project} /> : <Skeleton className="h-48 w-full rounded-xl" />}
        {error && <p role="alert" className="text-sm text-destructive">Could not load recordings: {error}</p>}
        {data && (data.recordings.length ? (
          <Card className="gap-0 py-0">
            <ul className="divide-y divide-border">
              {data.recordings.map((r) => (
                <Row key={r.id} recording={r} live={recorder.recording?.id === r.id} onChange={load}
                  onTranscript={(rec) => void showTranscript(rec)} onDelete={(rec, audioOnly) => setDeleting({ recording: rec, audioOnly })} />
              ))}
            </ul>
          </Card>
        ) : (
          <Empty className="border border-dashed border-border">
            <EmptyHeader>
              <EmptyTitle>No recordings yet</EmptyTitle>
              <EmptyDescription>Start one above when a call begins; the transcript appears here and in search once it is transcribed.</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ))}
      </div>

      <Dialog open={!!transcript} onOpenChange={(open) => { if (!open) setTranscript(null); }}>
        <DialogContent className="flex max-h-[85dvh] flex-col sm:max-w-3xl">
          <DialogHeader><DialogTitle>{transcript?.title}</DialogTitle></DialogHeader>
          <div className="min-h-0 overflow-y-auto"><Markdown text={transcriptBody(transcript?.text ?? "")} className="text-sm leading-relaxed" /></div>
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!deleting} onOpenChange={(open) => { if (!open) setDeleting(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{deleting?.audioOnly ? "Delete the audio?" : "Delete this recording?"}</AlertDialogTitle>
            <AlertDialogDescription>
              {deleting?.audioOnly
                ? "The transcript and its memories stay; the recording can no longer be played or transcribed again."
                : "The audio, the transcript and the memories made from it are removed from this device."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button variant="destructive" onClick={() => void confirmDelete()}>Delete</Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
