"use client";

import { useSyncExternalStore } from "react";
import { sendRecordingAudio, startRecording, stopRecording, type Recording } from "./api";

/** Records a meeting (ADR 0131): this computer's microphone on the left
 * channel and, when asked, its audio output on the right, as Opus in WebM.
 * Pieces upload every few seconds, so a closed window keeps what was sent.
 *
 * One recorder per page load, kept outside React so moving between pages
 * does not stop it. The desktop app answers the screen-capture request with
 * the system's audio (loopback); a browser asks the user what to share. */

export type RecorderState = {
  phase: "idle" | "starting" | "recording" | "stopping";
  recording: Recording | null;
  startedAt: number;
  /** The computer's audio is in the recording. */
  others: boolean;
  error: string;
  /** Bumped when a recording stops, so lists refetch. */
  stopped: number;
};

const PIECE_MS = 5000;
let state: RecorderState = { phase: "idle", recording: null, startedAt: 0, others: false, error: "", stopped: 0 };
const listeners = new Set<() => void>();
let live: {
  streams: MediaStream[]; context: AudioContext; recorder: MediaRecorder; analysers: AnalyserNode[];
  upload: Promise<unknown>; failed: string;
} | null = null;

function set(values: Partial<RecorderState>) {
  state = { ...state, ...values };
  listeners.forEach((notify) => notify());
}

export function useRecorder(): RecorderState {
  return useSyncExternalStore(
    (notify) => { listeners.add(notify); return () => listeners.delete(notify); },
    () => state,
    () => state,
  );
}

/** Loudness of the microphone and the computer's audio, 0 to 1. */
export function levels(): [number, number] {
  if (!live) return [0, 0];
  const level = (analyser?: AnalyserNode) => {
    if (!analyser) return 0;
    const samples = new Float32Array(analyser.fftSize);
    analyser.getFloatTimeDomainData(samples);
    return Math.min(1, Math.sqrt(samples.reduce((sum, v) => sum + v * v, 0) / samples.length) * 4);
  };
  return [level(live.analysers[0]), level(live.analysers[1])];
}

/** The desktop app's loopback came without sound to record. On macOS that is
 * a missing or refused permission: the track then arrives already ended. */
class ComputerAudioError extends Error {
  constructor() {
    super(window.electronAPI?.platform === "darwin"
      ? "macOS did not let Imprint record the computer's audio. Allow Imprint under System Settings → Privacy & Security → "
        + "Screen & System Audio Recording, in System Audio Recording Only, or record your microphone only."
      : "The computer's audio could not be recorded. Try again, or record your microphone only.");
  }
}

/** The computer's audio: the desktop app hands over its loopback, a browser
 * lets the user pick a tab or screen to share with its audio. */
async function computerAudio(): Promise<MediaStream | null> {
  if (!navigator.mediaDevices?.getDisplayMedia) return null;
  const display = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
  display.getVideoTracks().forEach((track) => track.stop());
  const audio = display.getAudioTracks().filter((track) => track.readyState === "live");
  if (!audio.length && window.electronAPI) {
    display.getTracks().forEach((track) => track.stop());
    throw new ComputerAudioError();
  }
  return audio.length ? new MediaStream(audio) : null;
}

function release() {
  live?.streams.forEach((stream) => stream.getTracks().forEach((track) => track.stop()));
  void live?.context.close().catch(() => {});
  live = null;
}

export async function startMeeting(options: { title: string; project: string; others: boolean; inputDevice: string }) {
  if (state.phase !== "idle") return;
  set({ phase: "starting", error: "" });
  const streams: MediaStream[] = [];
  try {
    const audio = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
    const microphone = await navigator.mediaDevices.getUserMedia({
      audio: options.inputDevice ? { ...audio, deviceId: { ideal: options.inputDevice } } : audio, video: false,
    });
    streams.push(microphone);
    let computer: MediaStream | null = null;
    if (options.others) {
      computer = await computerAudio();
      if (computer) streams.push(computer);
    }
    const recording = await startRecording({ title: options.title, project: options.project, others: !!computer, consent: true });
    const context = new AudioContext();
    const merger = context.createChannelMerger(2);
    const analysers: AnalyserNode[] = [];
    // Each source becomes one mono channel: left the microphone, right the computer.
    [microphone, computer].forEach((stream, channel) => {
      if (!stream) return;
      const mono = context.createGain();
      mono.channelCount = 1;
      mono.channelCountMode = "explicit";
      mono.channelInterpretation = "speakers";
      context.createMediaStreamSource(stream).connect(mono);
      mono.connect(merger, 0, channel);
      const analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      mono.connect(analyser);
      analysers[channel] = analyser;
    });
    const destination = context.createMediaStreamDestination();
    destination.channelCount = 2;
    merger.connect(destination);
    const recorder = new MediaRecorder(destination.stream, { mimeType: "audio/webm;codecs=opus", audioBitsPerSecond: 96000 });
    live = { streams, context, recorder, analysers, upload: Promise.resolve(), failed: "" };
    const current = live;
    recorder.ondataavailable = (event) => {
      if (!event.data.size) return;
      current.upload = current.upload.then(() => sendRecordingAudio(recording.id, event.data)).catch((e) => {
        current.failed = e instanceof Error ? e.message : String(e);
        if (state.phase === "recording") void stopMeeting();
      });
    };
    recorder.start(PIECE_MS);
    set({ phase: "recording", recording, startedAt: Date.now(), others: !!computer });
  } catch (e) {
    streams.forEach((stream) => stream.getTracks().forEach((track) => track.stop()));
    release();
    const message = e instanceof Error ? e.message : String(e);
    set({ phase: "idle", recording: null, error: e instanceof DOMException && e.name === "NotAllowedError"
      ? "The microphone or the computer's audio was not allowed. Allow it, or record without the computer's audio."
      : message });
  }
}

/** Stop, send the last piece, and queue the transcription. */
export async function stopMeeting() {
  if (state.phase !== "recording" || !live || !state.recording) return;
  set({ phase: "stopping" });
  const { recorder } = live;
  const current = live;
  const ended = new Promise((resolve) => { recorder.onstop = resolve; });
  if (recorder.state !== "inactive") recorder.stop();
  await ended;
  await current.upload;
  release();
  const id = state.recording.id;
  try {
    await stopRecording(id, (Date.now() - state.startedAt) / 1000);
    set({ phase: "idle", recording: null, error: current.failed ? `Recording stopped: ${current.failed}` : "", stopped: state.stopped + 1 });
  } catch (e) {
    set({ phase: "idle", recording: null, error: e instanceof Error ? e.message : String(e), stopped: state.stopped + 1 });
  }
}
