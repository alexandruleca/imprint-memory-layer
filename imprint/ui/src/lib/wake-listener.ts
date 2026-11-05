import { API_BASE } from "./api-base";
import { MicrophoneAccessError, VoiceGate, encodeWav, microphoneAccessAllows, microphoneProblem, type MicrophoneStep } from "./voice-client";
import { readVoicePreferences } from "./voice-preferences";

/** What the hidden listener tells the desktop app (ADR 0137). */
export type WakeReport =
  | { type: "listening" }
  | { type: "checking"; on: boolean }
  | { type: "heard"; id: number }
  | { type: "question"; id: number; text: string }
  | { type: "error"; kind: "microphone" | "model" | "service"; message?: string };

const PROBE_FRAMES = 100; // the first 2.0 s of a burst, its 240 ms pre-roll included
const SHORTEST = 15;      // bursts under 0.3 s are not speech worth checking
const SENSITIVITY = .016;

type Burst = { id: number; quiet: number; probed: boolean; woke: boolean; frames: Float32Array[] | null; sent: boolean };

class Blocked extends Error {}

/** The desktop app's hidden "Hey Echo" listener. The energy gate keeps silence
 * in this page; only the first 2 s of each burst of speech goes to the local
 * API's wake worker, which answers yes or no. After a yes, the rest of that
 * burst is transcribed and handed to Echo; the listener then stops checking. */
export class WakeListener {
  private context?: AudioContext;
  private stream?: MediaStream;
  private nodes: AudioNode[] = [];
  private socket?: WebSocket;
  private gate = new VoiceGate();
  private id = 0;
  private current: Burst | null = null;
  private waiting: Burst | null = null;
  private quietSince = performance.now();
  private handover = false;
  private stopped = false;
  private unsubscribe?: () => void;

  constructor(private report: (report: WakeReport) => void) {}

  async start() {
    const api = window.electronAPI;
    try {
      // The socket first: while the local service is down, the microphone stays closed.
      const config = await api?.wakeListenerConfig?.();
      await this.connect(config?.battery === true);
      this.unsubscribe = api?.onWakeListenerConfig?.(next => this.send({ type: "power", battery: next.battery === true }));
    } catch (error) {
      if (this.stopped) return;
      this.report(error instanceof Blocked ? { type: "error", kind: "model", message: error.message } : { type: "error", kind: "service" });
      this.stop();
      return;
    }
    const platform = api?.platform;
    let step: MicrophoneStep = "permission";
    try {
      const access = await api?.requestMicrophoneAccess?.();
      if (access && !microphoneAccessAllows(access.status, platform)) throw new MicrophoneAccessError(access.status);
      step = "device";
      if (!navigator.mediaDevices?.getUserMedia) throw new DOMException("Microphone capture needs a secure page.", "NotSupportedError");
      this.stream = await openMicrophone(readVoicePreferences().inputDevice);
      if (this.stopped) { this.stop(); return; }
      step = "worklet";
      const context = this.context = new AudioContext();
      await context.resume();
      await context.audioWorklet.addModule("/audio/imprint-capture.js");
      step = "graph";
      const capture = new AudioWorkletNode(context, "imprint-capture");
      capture.port.onmessage = event => this.frame(event.data as Float32Array);
      const source = context.createMediaStreamSource(this.stream);
      const silent = context.createGain(); silent.gain.value = 0;
      source.connect(capture).connect(silent).connect(context.destination);
      this.nodes = [source, capture, silent];
    } catch (error) {
      if (this.stopped) return;
      this.report({ type: "error", kind: "microphone", message: microphoneProblem(step, error, platform) });
      this.stop();
      return;
    }
    if (!this.stopped) this.report({ type: "listening" });
  }

  private async connect(battery: boolean) {
    const response = await fetch(`${API_BASE}/api/voice/wake/sessions`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ battery }) });
    if (response.status === 409) throw new Blocked((await response.json().catch(() => null))?.detail || "The wake model is missing.");
    if (!response.ok) throw new Error("The local service refused listening.");
    const { token } = await response.json();
    const url = new URL(`${API_BASE}/api/voice/wake/stream`, location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const socket = this.socket = new WebSocket(url);
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("The wake listener took too long to start.")), 90000);
      socket.onopen = () => socket.send(JSON.stringify({ token }));
      socket.onmessage = event => {
        const message = JSON.parse(event.data);
        clearTimeout(timer);
        if (message.type === "ready") resolve(); else reject(new Error(message.error || "The wake listener could not start."));
      };
      socket.onclose = () => { clearTimeout(timer); reject(new Error("The wake listener closed.")); };
    });
    socket.onmessage = event => this.receive(JSON.parse(event.data));
    socket.onclose = () => {
      if (this.stopped) return;
      this.report({ type: "error", kind: "service" });
      this.stop();
    };
  }

  private send(message: object) {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(message));
  }

  private frame(frame: Float32Array) {
    // After a wake, only the burst that woke Echo is still captured.
    if (this.stopped || (this.handover && !this.current)) return;
    const result = this.gate.feed(frame, false, SENSITIVITY);
    const now = performance.now();
    if (result.began && !this.handover) {
      this.current = { id: ++this.id, quiet: Math.max(0, (now - this.quietSince) / 1000), probed: false, woke: false, frames: null, sent: false };
    }
    const burst = this.current;
    if (burst && this.gate.active && !burst.probed && this.gate.frames.length >= PROBE_FRAMES) this.probe(burst, this.gate.frames.slice(0, PROBE_FRAMES));
    if (!result.complete) return;
    this.current = null;
    this.quietSince = now;
    if (!burst) return;
    burst.frames = result.complete;
    if (!burst.probed && result.complete.length >= SHORTEST) this.probe(burst, result.complete.slice(0, PROBE_FRAMES));
    if (burst.woke) this.utterance(burst);
    else if (this.waiting !== burst) burst.frames = null;
  }

  /** One probe at a time; a burst that starts while one is checked goes unchecked. */
  private probe(burst: Burst, frames: Float32Array[]) {
    burst.probed = true;
    if (this.waiting || !this.context) return;
    this.waiting = burst;
    this.report({ type: "checking", on: true });
    this.send({ type: "probe", id: burst.id, audio: encodeWav(frames, this.context.sampleRate, 16000), quiet: Math.round(burst.quiet * 10) / 10 });
  }

  private utterance(burst: Burst) {
    if (!burst.frames || burst.sent || !this.context) return;
    burst.sent = true;
    this.send({ type: "utterance", id: burst.id, audio: encodeWav(burst.frames, this.context.sampleRate, 16000) });
    burst.frames = null;
  }

  private receive(message: { type: string; id?: number; wake?: boolean; text?: string; error?: string }) {
    if (message.type === "probe" && this.waiting && message.id === this.waiting.id) {
      const burst = this.waiting;
      this.waiting = null;
      this.report({ type: "checking", on: false });
      if (!message.wake || this.handover) { if (this.current !== burst) burst.frames = null; return; }
      burst.woke = true;
      this.handover = true;
      this.report({ type: "heard", id: burst.id });
      if (this.current !== burst) this.utterance(burst);
    } else if (message.type === "question" && typeof message.id === "number") {
      this.report({ type: "question", id: message.id, text: typeof message.text === "string" ? message.text : "" });
    } else if (message.type === "error") {
      this.report({ type: "error", kind: "service", message: message.error });
      this.stop();
    }
  }

  stop() {
    this.stopped = true;
    this.unsubscribe?.(); this.unsubscribe = undefined;
    if (this.socket) { this.socket.onclose = null; this.socket.close(); this.socket = undefined; }
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = undefined;
    this.nodes.forEach(node => node.disconnect()); this.nodes = [];
    void this.context?.close().catch(() => {}); this.context = undefined;
    this.current = this.waiting = null;
  }
}

/** The microphone chosen in Voice settings, or the system default when it is gone.
 * No echo cancellation (nothing of Imprint's plays while it listens) and no gain
 * control (it would lift the room floor the gate adapts to). */
async function openMicrophone(device: string) {
  const audio = { channelCount: 1, echoCancellation: false, noiseSuppression: true, autoGainControl: false };
  if (!device) return navigator.mediaDevices.getUserMedia({ audio, video: false });
  try {
    return await navigator.mediaDevices.getUserMedia({ audio: { ...audio, deviceId: { exact: device } }, video: false });
  } catch (error) {
    if (!(error instanceof DOMException) || !["OverconstrainedError", "NotFoundError"].includes(error.name)) throw error;
    return navigator.mediaDevices.getUserMedia({ audio, video: false });
  }
}
