"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { levels, startMeeting, stopMeeting, useRecorder } from "@/lib/recorder";
import { readVoicePreferences } from "@/lib/voice-preferences";

const onRecordings = () => window.location.pathname.replace(/\/$/, "") === "/recordings";

async function until(ready: () => boolean, ms = 5000) {
  for (const end = Date.now() + ms; !ready() && Date.now() < end;) await new Promise((resolve) => setTimeout(resolve, 50));
}

/** In the desktop app the floating launcher starts and stops meeting recordings
 * in this window, shown or hidden, and shows what it reports (ADR 0131). */
export function RecorderBridge() {
  const recorder = useRecorder();
  const router = useRouter();

  useEffect(() => {
    window.electronAPI?.reportRecorder?.({
      phase: recorder.phase, id: recorder.recording?.id ?? "", title: recorder.recording?.title ?? "",
      project: recorder.recording?.project ?? "", startedAt: recorder.startedAt, others: recorder.others, error: recorder.error,
    });
  }, [recorder]);

  useEffect(() => {
    if (recorder.phase !== "recording" || !window.electronAPI?.reportRecorderLevels) return;
    const timer = setInterval(() => window.electronAPI?.reportRecorderLevels?.(levels()), 100);
    return () => clearInterval(timer);
  }, [recorder.phase]);

  useEffect(() => window.electronAPI?.onRecorderCommand?.(async (command) => {
    if (command.type === "stop") { await stopMeeting(); return; }
    if (command.type !== "start") return;
    // Only the Recordings page may capture audio in the desktop app.
    if (!onRecordings()) { router.push("/recordings/"); await until(onRecordings); }
    await startMeeting({ title: command.title, project: command.project, others: command.others, inputDevice: readVoicePreferences().inputDevice });
  }), [router]);

  return null;
}
