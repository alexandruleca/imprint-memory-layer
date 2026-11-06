import { API_BASE } from "./api-base";

export type VoiceMode = "ptt" | "handsfree";
export type VoicePhase = "idle" | "connecting" | "ready" | "listening" | "thinking" | "speaking" | "error";
export type VoicePreparation = { stage: "download" | "load"; downloaded: number; total: number };
/** One question and its answer, as the API records it (ADR 0145). */
export type VoiceTurnState = "queued" | "thinking" | "done" | "stopped" | "failed";
export type VoiceTurn = {
  id: number; user: string; answer: string; state: VoiceTurnState;
  /** What Echo is doing for it, e.g. "Checking memory · search". */
  detail: string; error: string;
  /** It finished while no window was attached. */
  away: boolean;
  /** Asked by voice; its words appear once Echo has heard them. */
  spoken: boolean;
};
export type VoiceSnapshot = {
  phase: VoicePhase; detail: string; connected: boolean; microphone: boolean;
  /** Why the last attempt to turn the microphone on failed; empty otherwise. */
  microphoneError: string;
  /** That failure was a refusal the OS microphone settings can lift (desktop macOS and Windows). */
  microphoneSettings: boolean;
  mode: VoiceMode; held: boolean; workspace: string;
  preparation: VoicePreparation | null;
  /** This conversation was started by "Hey Echo" and ends after quiet (ADR 0137). */
  wake: boolean;
  /** Recent questions and answers, oldest first, queued ones included. */
  turns: VoiceTurn[];
  /** Echo's voice is lowered while the user speaks over it. */
  ducked: boolean;
  /** This window picked up a conversation that went on without it. */
  resumed: boolean;
  /** Not connected, but the API keeps a conversation this window can continue. */
  resumable: boolean;
  /** Another window holds the conversation. */
  elsewhere: boolean;
};
export const initialVoice: VoiceSnapshot = { phase: "idle", detail: "Ready when you are", connected: false,
  microphone: false, microphoneError: "", microphoneSettings: false, mode: "ptt", held: false, workspace: "", preparation: null,
  wake: false, turns: [], ducked: false, resumed: false, resumable: false, elsewhere: false };

/** Echo is answering, or has questions waiting. */
export const replying = (voice: VoiceSnapshot) => voice.turns.some(turn => turn.state === "thinking" || turn.state === "queued");

/** Turning the microphone on: asking the OS (desktop app), opening the device,
 * loading the capture worklet, connecting the graph. */
export type MicrophoneStep = "permission" | "device" | "worklet" | "graph";

/** The desktop app's OS consent check said no, or could not say (ADR 0069). */
export class MicrophoneAccessError extends Error {
  constructor(readonly status: string) { super(`The system reports microphone access as ${status}`); this.name = "MicrophoneAccessError"; }
}

/** Whether the OS answer lets capture go ahead. macOS has already asked if it
 * could, so only a grant does; Windows proceeds unless it reports a refusal;
 * Linux has no OS consent ("unmanaged") and capture decides. */
export function microphoneAccessAllows(status: string, platform?: string) {
  return platform === "darwin" ? status === "granted" : !["denied", "restricted", "error"].includes(status);
}

function refused(step: MicrophoneStep, error: unknown) {
  if (step === "permission") return error instanceof MicrophoneAccessError && error.status === "denied";
  return step === "device" && error instanceof Error && (error.name === "NotAllowedError" || error.name === "SecurityError");
}

/** The failure is a refusal that the OS microphone settings can lift. */
export function microphoneSettingsHelp(step: MicrophoneStep, error: unknown, platform?: string) {
  return refused(step, error) && (platform === "darwin" || platform === "win32");
}

/** Plain words for a failed microphone step, ending with the browser's own error
 * name and message. Only a refusal says to check permissions. */
export function microphoneProblem(step: MicrophoneStep, error: unknown, platform?: string) {
  const name = error instanceof Error ? error.name : "";
  const message = error instanceof Error ? error.message : String(error);
  if (refused(step, error)) {
    if (platform === "darwin") return "Microphone permission denied. Allow Imprint in System Settings → Privacy & Security → Microphone, then quit and reopen Imprint.";
    if (platform === "win32") return "Microphone permission denied. In Windows Settings → Privacy & security → Microphone, turn on Microphone access and Let desktop apps access your microphone, then try again.";
    if (platform === "linux") return "Microphone permission denied. Check your desktop's microphone privacy settings and audio input, then try again.";
    return "Microphone permission denied. Allow microphone access for this page in your browser's site settings, then try again.";
  }
  if (step === "permission") {
    if (error instanceof MicrophoneAccessError && error.status === "restricted") return "Microphone access is restricted by a system policy. Ask your device administrator; you can still type.";
    return `Imprint couldn't check microphone permission. Quit and reopen Imprint, then try again. (${message})`;
  }
  const plain = step === "worklet" ? "Microphone audio processing failed to load."
    : step === "graph" ? "Microphone audio processing failed to start."
    : name === "NotFoundError" || name === "OverconstrainedError" ? "No microphone found. Connect or turn on a microphone, then try again."
    : name === "NotReadableError" || name === "AbortError" ? "The microphone couldn't start. Another app may be using it; close that app or choose another input in sound settings."
    : name === "NotSupportedError" ? "This page can't use a microphone. Open Imprint on this computer (127.0.0.1) or over HTTPS."
    : "The microphone couldn't start.";
  return `${plain} (${[name, message].filter(Boolean).join(": ") || "no details"})`;
}

/** Echo's robotic voice (ADR 0071): continuous 70 Hz ring modulation with light
 * quantization, applied to reply audio only. It keeps every sample, so playback
 * length and scheduling are unchanged; `offset` continues the phase across chunks. */
export function robotVoice(samples: Float32Array, offset = 0) {
  for (let i = 0; i < samples.length; i++) {
    samples[i] = Math.round(samples[i] * (.65 + .35 * Math.sin(2 * Math.PI * 70 * (offset + i) / 24000)) * 512) / 512;
  }
}

// Adaptive energy VAD, with onset hysteresis, endpoint silence and pre-roll.
// This is intentionally separate from the acoustic model; see live-voice.md.
export class VoiceGate {
  noise = .003;
  onset = 0;
  silence = 0;
  active = false;
  /** Voiced frames in the utterance so far; a short burst is noise (ADR 0145). */
  voiced = 0;
  frames: Float32Array[] = [];
  pre: Float32Array[] = [];
  reset() { this.onset = this.silence = this.voiced = 0; this.active = false; this.frames = []; this.pre = []; }
  feed(frame: Float32Array, speaking: boolean, sensitivity: number) {
    const rms = Math.sqrt(frame.reduce((sum, v) => sum + v * v, 0) / frame.length);
    const threshold = Math.max(sensitivity, this.noise * (speaking ? 4.5 : 3));
    const voiced = rms > threshold;
    let began = false;
    if (!this.active) {
      this.pre.push(frame);
      if (this.pre.length > 12) this.pre.shift();
      this.onset = voiced ? this.onset + 1 : 0;
      if (!voiced) this.noise = Math.min(.02, this.noise * .98 + rms * .02);
      if (this.onset >= (speaking ? 7 : 5)) {
        this.active = began = true;
        this.voiced = this.onset;
        this.frames = [...this.pre]; this.pre = [];
      }
    } else {
      this.frames.push(frame);
      if (voiced) this.voiced++;
      this.silence = voiced ? 0 : this.silence + 1;
      if (this.silence >= 30 || this.frames.length >= 1490) {
        const complete = this.frames, spoken = this.voiced;
        this.reset();
        return { began, complete, voiced: spoken };
      }
    }
    return { began, complete: null, voiced: this.voiced };
  }
}

/** Mono PCM16 WAV, base64: 24 kHz for Echo's turns, 16 kHz for the wake listener. */
export function encodeWav(frames: Float32Array[], sourceRate: number, rate = 24000) {
  const count = frames.reduce((sum, frame) => sum + frame.length, 0);
  if (count / sourceRate > 30) throw new Error("Keep each question under 30 seconds.");
  const joined = new Float32Array(count);
  let offset = 0;
  for (const frame of frames) { joined.set(frame, offset); offset += frame.length; }
  const length = Math.floor(count * rate / sourceRate);
  const data = new ArrayBuffer(44 + length * 2), view = new DataView(data);
  function word(at: number, text: string) { for (let i = 0; i < text.length; i++) view.setUint8(at + i, text.charCodeAt(i)); }
  word(0, "RIFF"); view.setUint32(4, 36 + length * 2, true); word(8, "WAVE"); word(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true);
  view.setUint16(34, 16, true); word(36, "data"); view.setUint32(40, length * 2, true);
  for (let i = 0; i < length; i++) {
    // Box averaging downsamples the common 48 kHz browser input.
    const from = Math.floor(i * sourceRate / rate), to = Math.max(from + 1, Math.floor((i + 1) * sourceRate / rate));
    let sample = 0;
    for (let j = from; j < to; j++) sample += joined[j] || 0;
    sample = Math.max(-1, Math.min(1, sample / (to - from)));
    view.setInt16(44 + i * 2, sample * (sample < 0 ? 32768 : 32767), true);
  }
  const bytes = new Uint8Array(data);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(binary);
}

// Speech shorter than this (in 20 ms frames of voiced audio) is a cough or a
// knock, not a question; it is dropped before it leaves the page (ADR 0145).
export const MIN_SPOKEN_FRAMES = 12;
// Echo's voice while the user speaks over it: lowered, never stopped.
const DUCKED_GAIN = .2;
const KEPT_TURNS = 8;

type ServerTurn = { id: number; user?: string; answer?: string; state?: string; detail?: string; error?: string; away?: boolean };
const turnState = (state?: string): VoiceTurnState => state === "running" ? "thinking"
  : state === "cancelled" ? "stopped" : state === "queued" || state === "done" || state === "failed" ? state : "done";

/** What the API keeps of the conversation for a window that comes back (ADR 0145). */
export type VoiceConversation = { attached: boolean; busy: boolean; engine: string; turns: ServerTurn[] } | null;
export async function conversationStatus(): Promise<VoiceConversation> {
  const response = await fetch(`${API_BASE}/api/voice/conversation`, { signal: AbortSignal.timeout(3000) });
  if (!response.ok) return null;
  const body = await response.json();
  return body && typeof body.conversation === "object" && Array.isArray(body.conversation?.turns) ? body.conversation : null;
}

export class VoiceClient {
  snapshot: VoiceSnapshot = { ...initialVoice };
  level = 0;
  sensitivity = .016;
  private socket?: WebSocket;
  private context?: AudioContext;
  private stream?: MediaStream;
  private capture?: AudioWorkletNode;
  private source?: MediaStreamAudioSourceNode;
  private silent?: GainNode;
  private analyser?: AnalyserNode;
  /** Reply audio passes through it, so speech over Echo lowers its voice. */
  private output?: GainNode;
  private meter?: Float32Array<ArrayBuffer>;
  /** Scheduled reply audio, with the turn it belongs to. */
  private sources = new Map<AudioBufferSourceNode, number>();
  private nextAudio = 0;
  private id = 0;
  /** The turn the API is answering now; 0 when it waits for a question. */
  private running = 0;
  /** A finished answer read aloud again: replay id → the turn it reads. */
  private replays = new Map<number, number>();
  /** Reply audio per turn on this connection: samples scheduled and played, for the API's playback credit. */
  private counts = new Map<number, { output: number; played: number }>();
  private gate = new VoiceGate();
  private heldFrames: Float32Array[] = [];
  /** The user is speaking now (hands-free gate open or push-to-talk held). */
  private hearing = false;
  /** That speech began while Echo's voice played. */
  private overlap = false;
  /** A short note for the caption until the next change, e.g. "I didn't catch that". */
  private note = "";
  private timer?: ReturnType<typeof setInterval>;
  private stopped = true;
  private epoch = 0;
  private micPending = false;
  /** Bumped by Stop and by turning the microphone off, so a pending consent or capture is dropped. */
  private micAttempt = 0;
  private question?: string;
  /** This connection picks a conversation up again and does not turn the microphone on (ADR 0069). */
  private quiet = false;
  /** One automatic reconnection for a connection lost mid-reply. */
  private reconnected = false;
  /** The user's remembered choice: arm the microphone whenever a conversation is ready (ADR 0069). */
  armMicrophone = false;
  /** The microphone and speaker to use, as browser device IDs; empty for the system default. */
  inputDevice = "";
  outputDevice = "";
  /** The session's voice effect, as the API reports it when the session is ready. */
  private effect: string | null = null;
  private engine: "chat" | "lfm" = "chat";
  /** A conversation "Hey Echo" started (ADR 0137): hands-free with the microphone
   * on for its duration, ending after `idle` ms of quiet once Echo has replied. */
  private wakeSession: { idle: number; mode: VoiceMode; timer?: ReturnType<typeof setTimeout> } | null = null;
  /** Called once when a wake-started conversation ends by itself or is stopped. */
  onWakeEnd?: (reason: "quiet" | "asked" | "stopped") => void;
  constructor(private changed: (value: VoiceSnapshot) => void) {}
  private update(values: Partial<VoiceSnapshot>) {
    this.snapshot = { ...this.snapshot, ...values };
    const session = this.wakeSession;
    // Quiet counts from when Echo is ready again; speaking or thinking resets it.
    if (session && this.snapshot.phase === "ready" && this.snapshot.connected && !session.timer) {
      session.timer = setTimeout(() => this.endWake("quiet"), session.idle);
    } else if (session?.timer && this.snapshot.phase !== "ready") {
      clearTimeout(session.timer); session.timer = undefined;
    }
    this.changed(this.snapshot);
  }
  private get busy() { return this.snapshot.turns.some(turn => turn.state === "thinking" || turn.state === "queued"); }
  /** The phase and caption follow from what is happening: the user speaking,
   * Echo's voice playing, a question being answered or waiting, or nothing. */
  private refresh(values: Partial<VoiceSnapshot> = {}) {
    const next = { ...this.snapshot, ...values };
    if (!next.connected) { this.update(values); return; }
    const current = next.turns.find(turn => turn.id === this.running);
    const waiting = next.turns.filter(turn => turn.state === "queued").length;
    const busy = next.turns.some(turn => turn.state === "thinking" || turn.state === "queued");
    const idle = !next.microphone ? "Type a question or enable your microphone"
      : next.mode === "ptt" ? "Hold to talk" : "Listening for your voice";
    let phase: VoicePhase, detail: string;
    if (this.hearing) {
      phase = "listening";
      detail = next.held ? "Listening… release to send" : "Listening…";
      if (busy || this.sources.size) detail += " · answered next";
    } else if (this.sources.size) {
      phase = "speaking"; detail = "Speaking";
    } else if (busy) {
      phase = "thinking"; detail = current?.detail || (current ? "Thinking…" : "Waiting to answer…");
    } else {
      phase = "ready"; detail = this.note || idle;
    }
    if (waiting && phase !== "listening") detail += ` · ${waiting} ${waiting === 1 ? "question waits" : "questions wait"}`;
    this.update({ ...values, phase, detail, ducked: this.hearing && this.sources.size > 0 });
  }
  private setTurns(change: (turns: VoiceTurn[]) => VoiceTurn[]) {
    const turns = change(this.snapshot.turns);
    // Keep the last few, and every one still waiting or being answered.
    const finished = turns.filter(turn => turn.state !== "queued" && turn.state !== "thinking");
    const drop = new Set(finished.slice(0, Math.max(0, turns.length - KEPT_TURNS)).map(turn => turn.id));
    return turns.filter(turn => !drop.has(turn.id));
  }
  private patchTurn(id: number, change: Partial<VoiceTurn> | ((turn: VoiceTurn) => Partial<VoiceTurn>)) {
    return this.setTurns(turns => turns.map(turn => turn.id === id ? { ...turn, ...typeof change === "function" ? change(turn) : change } : turn));
  }
  private endWake(reason: "quiet" | "asked" | "stopped") {
    const session = this.wakeSession;
    if (!session) return;
    this.wakeSession = null;
    clearTimeout(session.timer);
    if (reason !== "stopped") this.stop();
    this.update({ wake: false, mode: session.mode });
    this.onWakeEnd?.(reason);
  }
  /** "Hey Echo" (ADR 0137): an open conversation carries on hands-free with the
   * microphone on; otherwise a new one starts that way. */
  wakeUp(engine: "chat" | "lfm", idleSeconds: number) {
    if (!this.snapshot.connected) { void this.start(engine, { wake: { idleSeconds } }); return; }
    if (!this.wakeSession) this.wakeSession = { idle: Math.max(5, Math.min(120, idleSeconds)) * 1000, mode: this.snapshot.mode };
    this.setMode("handsfree");
    this.update({ wake: true });
    void this.enableMicrophone();
  }
  /** The user took over a wake-started conversation: it no longer ends by itself. */
  keepWake() {
    const session = this.wakeSession;
    if (!session) return;
    this.wakeSession = null;
    clearTimeout(session.timer);
    this.update({ wake: false });
  }
  private send(message: object) { if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(message)); }
  /** The connection failed: the conversation stays in the API for the next try. */
  private fail(message: string) { this.detach(); this.update({ phase: "error", detail: message }); }

  /** Connect to the API's conversation, starting one or picking the open one up
   * (ADR 0145). `resume` comes back to it without turning the microphone on. */
  async start(engine: "chat" | "lfm", options: { wake?: { idleSeconds: number }; resume?: boolean } = {}) {
    this.detach(); this.stopped = false; this.engine = engine;
    this.quiet = !!options.resume;
    const epoch = ++this.epoch;
    if (options.wake) {
      this.wakeSession = { idle: Math.max(5, Math.min(120, options.wake.idleSeconds)) * 1000, mode: this.snapshot.mode };
      this.snapshot = { ...this.snapshot, mode: "handsfree", wake: true };
    }
    this.update({ phase: "connecting", elsewhere: false,
      detail: options.wake ? "Heard “Hey Echo” · getting ready…" : options.resume ? "Picking up your conversation…" : "Preparing audio…" });
    try {
      this.context = new AudioContext();
      let audioTimer: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([this.context.resume(), new Promise<never>((_, reject) => {
          audioTimer = setTimeout(() => reject(new Error("Audio output is unavailable. Choose a speaker or headset in sound settings, then try again.")), 10000);
        })]);
      } finally { clearTimeout(audioTimer); }
      await this.routeOutput();
      if (this.stopped || epoch !== this.epoch) return;
      // A new look or reasoning model reconnects; the API switches once the
      // reply in progress is answered (ADR 0071, 0145). One voice window holds
      // the conversation at a time. A status check that does not answer in
      // time is asked again, not a failure.
      for (const deadline = Date.now() + 8000; ;) {
        const previous = await fetch(`${API_BASE}/api/voice/status`, { signal: AbortSignal.timeout(1500) }).catch(() => null);
        if (this.stopped || epoch !== this.epoch) return;
        if (previous?.ok && !(await previous.json()).busy) break;
        if (previous && options.resume) { this.detach(); this.update({ elsewhere: true }); return; }
        if (Date.now() >= deadline) throw new Error(previous ? "Voice is open in another window. Stop it there, then try again."
          : "Local voice isn't answering. Try again in a moment.");
        this.update({ detail: "Waiting for the previous conversation…" });
        await new Promise(resolve => setTimeout(resolve, 150));
        if (this.stopped || epoch !== this.epoch) return;
      }
      this.update({ detail: "Connecting locally…" });
      const response = await fetch(`${API_BASE}/api/voice/sessions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ engine }) });
      if (!response.ok) { const error = await response.json(); throw new Error(error.detail || "Voice is unavailable."); }
      const session = await response.json();
      if (this.stopped || epoch !== this.epoch) return;
      const url = new URL(`${API_BASE}/api/voice/stream`, location.href);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(url);
      this.socket = socket;
      socket.onopen = () => { if (!this.stopped && epoch === this.epoch) socket.send(JSON.stringify({ token: session.token })); else socket.close(); };
      socket.onmessage = event => { if (!this.stopped && epoch === this.epoch) this.receive(JSON.parse(event.data)); };
      socket.onerror = () => { if (!this.stopped && epoch === this.epoch) this.fail("Couldn't connect to local voice."); };
      socket.onclose = () => { if (!this.stopped && epoch === this.epoch) this.lost(); };
      this.update({ workspace: session.workspace });
      this.timer = setInterval(() => this.tick(), 40);
    } catch (error) {
      if (!this.stopped && epoch === this.epoch) this.fail(error instanceof Error ? error.message : "Voice couldn't start.");
    }
  }
  /** The connection dropped. A reply in progress goes on in the API; this
   * window comes back to it once by itself, without the microphone. */
  private lost() {
    const busy = this.busy;
    if (busy && !this.reconnected) {
      this.reconnected = true;
      this.detach();
      void this.start(this.engine, { resume: true });
      return;
    }
    this.fail(busy ? "Voice disconnected. Echo keeps answering; continue to see it." : "Voice disconnected. Start again to reconnect.");
  }
  /** What a window coming back shows (ADR 0145): a reply still being written is
   * picked up at once; a finished one is shown without asking again. Never
   * turns the microphone on. */
  async restore(engine: "chat" | "lfm") {
    if (this.engaged()) return;
    const conversation = await conversationStatus().catch(() => null);
    if (this.engaged()) return;
    if (!conversation) {
      if (this.snapshot.resumable || this.snapshot.elsewhere) this.update({ resumable: false, elsewhere: false, turns: [] });
      return;
    }
    const turns = conversation.turns.map(turn => this.fromServer(turn));
    this.update({ turns, elsewhere: conversation.attached, resumable: !conversation.attached && turns.length > 0 });
    if (!conversation.attached && conversation.busy) await this.start(engine, { resume: true });
  }
  /** Connected, or connecting. */
  private engaged() { return this.snapshot.connected || this.snapshot.phase === "connecting"; }
  private fromServer(turn: ServerTurn): VoiceTurn {
    return { id: turn.id, user: turn.user || "", answer: turn.answer || "", state: turnState(turn.state), detail: turn.detail || "",
      error: turn.error || "", away: !!turn.away, spoken: false };
  }

  /** Resolves true once capture runs. The desktop app asks the OS first; the
   * answer can arrive after Stop or a change of mind, which then wins. */
  async enableMicrophone() {
    if (!this.snapshot.connected || this.stream || this.micPending) return false;
    const epoch = this.epoch, attempt = ++this.micAttempt;
    const current = () => !this.stopped && epoch === this.epoch && attempt === this.micAttempt;
    const platform = window.electronAPI?.platform;
    this.micPending = true;
    let step: MicrophoneStep = "permission";
    this.update({ microphoneError: "", microphoneSettings: false });
    try {
      const access = await window.electronAPI?.requestMicrophoneAccess?.();
      if (!current()) return false;
      if (access && !microphoneAccessAllows(access.status, platform)) throw new MicrophoneAccessError(access.status);
      step = "device";
      if (!navigator.mediaDevices?.getUserMedia) throw new DOMException("Microphone capture needs a secure page.", "NotSupportedError");
      const stream = await this.openMicrophone();
      if (!current()) { stream.getTracks().forEach(track => track.stop()); return false; }
      this.stream = stream;
      step = "worklet";
      const context = this.context!;
      await context.audioWorklet.addModule("/audio/imprint-capture.js");
      if (!current()) return false;
      step = "graph";
      this.capture = new AudioWorkletNode(context, "imprint-capture");
      this.capture.port.onmessage = event => this.frame(event.data);
      this.source = context.createMediaStreamSource(stream);
      this.silent = context.createGain(); this.silent.gain.value = 0;
      this.source.connect(this.capture).connect(this.silent).connect(context.destination);
      stream.getAudioTracks().forEach(track => { track.enabled = this.snapshot.mode === "handsfree"; });
      this.refresh({ microphone: true });
      return true;
    } catch (error) {
      if (!current()) return false;
      this.releaseMicrophone();
      const detail = microphoneProblem(step, error, platform);
      this.update({ microphone: false, detail, microphoneError: detail, microphoneSettings: microphoneSettingsHelp(step, error, platform) });
      return false;
    } finally {
      if (attempt === this.micAttempt) this.micPending = false;
    }
  }
  /** The chosen microphone, or the system default when it is no longer connected. */
  private async openMicrophone() {
    const audio = { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true };
    if (!this.inputDevice) return navigator.mediaDevices.getUserMedia({ audio, video: false });
    try {
      return await navigator.mediaDevices.getUserMedia({ audio: { ...audio, deviceId: { exact: this.inputDevice } }, video: false });
    } catch (error) {
      if (!(error instanceof DOMException) || !["OverconstrainedError", "NotFoundError"].includes(error.name)) throw error;
      return navigator.mediaDevices.getUserMedia({ audio, video: false });
    }
  }
  /** Play through the chosen speaker, or the system default when it is gone
   * or the browser cannot choose one (Web Audio's setSinkId is Chromium's).
   * A new context already plays through the default, so starting with no
   * choice routes nothing; a device that does not answer in 3 s is left. */
  private async routeOutput(changed = false) {
    const context = this.context as (AudioContext & { setSinkId?: (id: string) => Promise<void> }) | undefined;
    if (!context?.setSinkId || (!this.outputDevice && !changed)) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const route = (id: string) => Promise.race([context.setSinkId!(id), new Promise<void>(resolve => { timer = setTimeout(resolve, 3000); })])
      .finally(() => clearTimeout(timer));
    try { await route(this.outputDevice); }
    catch { if (this.outputDevice) await route("").catch(() => {}); }
  }
  /** Another microphone: a running capture reopens on it, keeping the conversation. */
  async setInputDevice(id: string) {
    this.inputDevice = id;
    if (!this.stream) return;
    this.release(false); this.gate.reset(); this.heldFrames = []; this.hearing = false;
    this.releaseMicrophone();
    this.refresh({ microphone: false });
    await this.enableMicrophone();
  }
  /** Another speaker: playback moves to it, mid-reply included. */
  async setOutputDevice(id: string) {
    this.outputDevice = id;
    await this.routeOutput(true);
  }
  private releaseMicrophone() {
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = undefined;
    this.capture?.disconnect(); this.capture?.port.close(); this.capture = undefined;
    this.source?.disconnect(); this.source = undefined; this.silent?.disconnect(); this.silent = undefined;
  }
  /** The remembered choice changed: turning it off releases capture and drops a
   * pending consent, but keeps the conversation for typing. */
  setMicrophonePreference(enabled: boolean) {
    this.armMicrophone = enabled;
    if (enabled) { if (this.snapshot.connected) void this.enableMicrophone(); return; }
    ++this.micAttempt; this.micPending = false;
    this.release(false); this.gate.reset(); this.heldFrames = []; this.hearing = false; this.duck(false);
    this.releaseMicrophone();
    if (this.snapshot.connected) this.refresh({ microphone: false, held: false, microphoneError: "", microphoneSettings: false });
    else this.update({ microphone: false, held: false, microphoneError: "", microphoneSettings: false });
  }

  /** Push to talk or hands-free. Switching never touches the reply in progress (ADR 0145). */
  setMode(mode: VoiceMode) {
    if (this.snapshot.held) this.release(false);
    this.gate.reset(); this.heldFrames = [];
    if (this.hearing) { this.hearing = false; this.duck(false); }
    this.stream?.getAudioTracks().forEach(track => { track.enabled = mode === "handsfree"; });
    this.refresh({ mode, held: false });
  }
  hold() {
    if (!this.snapshot.connected || !this.snapshot.microphone || this.snapshot.mode !== "ptt" || this.snapshot.held) return;
    this.heldFrames = []; this.note = "";
    this.stream?.getAudioTracks().forEach(track => { track.enabled = true; });
    this.hearing = true; this.overlap = this.sources.size > 0; this.duck(true);
    this.refresh({ held: true });
  }
  release(send = true) {
    if (!this.snapshot.held) return;
    this.stream?.getAudioTracks().forEach(track => { track.enabled = false; });
    this.hearing = false; this.duck(false);
    const frames = this.heldFrames; this.heldFrames = [];
    if (send && frames.length >= 8) this.submit({ audio: encodeWav(frames, this.context!.sampleRate) }, this.overlap);
    else this.refresh({ held: false });
  }
  private frame(frame: Float32Array) {
    if (!this.snapshot.connected) return;
    if (this.snapshot.mode === "ptt") {
      if (this.snapshot.held) { this.heldFrames.push(frame); if (this.heldFrames.length >= 1490) this.release(); }
      return;
    }
    const playing = this.sources.size > 0;
    const result = this.gate.feed(frame, playing, this.sensitivity);
    if (result.began) { this.hearing = true; this.overlap = playing; this.note = ""; this.duck(true); this.refresh(); }
    if (result.complete) {
      this.hearing = false; this.duck(false);
      // A cough, a knock or a word cut short is not a question.
      if (result.voiced >= MIN_SPOKEN_FRAMES) this.submit({ audio: encodeWav(result.complete, this.context!.sampleRate) }, this.overlap);
      else this.refresh();
    }
  }
  /** Lower Echo's voice while the user speaks over it; nothing is skipped. */
  private duck(on: boolean) {
    const output = this.output, context = this.context;
    if (!output || !context) return;
    output.gain.setTargetAtTime(on ? DUCKED_GAIN : 1, context.currentTime, .04);
  }
  /** Typing needs no open conversation: one starts, and the question is sent once it is ready. */
  ask(text: string, engine: "chat" | "lfm") {
    const question = text.trim().slice(0, 2000);
    if (!question) return;
    if (this.snapshot.connected) { this.submit({ text: question }); return; }
    if (this.snapshot.phase !== "connecting") void this.start(engine);
    this.question = question;
  }
  /** A question goes to the API at once; while Echo answers another, it waits
   * its turn there instead of replacing it (ADR 0145). */
  private submit(content: { audio?: string; text?: string }, overlap = false) {
    if (!this.snapshot.connected) return;
    const id = ++this.id, waits = this.busy;
    if (!waits) this.running = id;
    this.note = "";
    const turn: VoiceTurn = { id, user: content.text || "", answer: "", state: waits ? "queued" : "thinking", detail: "", error: "",
      away: false, spoken: !!content.audio };
    this.refresh({ held: false, turns: this.setTurns(turns => [...turns, turn]), resumed: false });
    this.send({ type: "turn", id, ...content, ...this.wakeSession ? { wake: true } : {}, ...overlap ? { overlap: true } : {} });
  }
  /** Ask a question again after it failed. */
  retry(id: number) {
    const turn = this.snapshot.turns.find(item => item.id === id);
    if (!turn?.user) return;
    this.update({ turns: this.snapshot.turns.filter(item => item.id !== id) });
    this.submit({ text: turn.user });
  }
  /** Read a finished answer aloud again, without asking again. */
  replay(id: number) {
    const turn = this.snapshot.turns.find(item => item.id === id);
    if (!this.snapshot.connected || !turn?.answer.trim()) return;
    const replay = ++this.id;
    this.replays.set(replay, id);
    this.refresh({ turns: this.patchTurn(id, { away: false }) });
    this.send({ type: "replay", id: replay, of: id });
  }
  /** The user's stop (ADR 0145): Echo's voice goes quiet, and the reply in
   * progress and every waiting question are cancelled. */
  stopReply() {
    this.silence();
    this.counts.clear(); this.replays.clear();
    if (!this.snapshot.connected) return;
    // Only the API's work needs cancelling; a reply already written just goes quiet.
    if (this.busy) this.send({ type: "cancel" });
    const turns = this.setTurns(list => list.map(turn => turn.state === "thinking" || turn.state === "queued" ? { ...turn, state: "stopped" } : turn));
    this.running = 0;
    this.refresh({ turns, preparation: null });
  }
  /** Take back a question that waits its turn. */
  withdraw(id: number) {
    if (!this.snapshot.connected) return;
    this.send({ type: "cancel", id });
    this.refresh({ turns: this.snapshot.turns.filter(turn => turn.id !== id) });
  }
  /** Stop every scheduled sound, this window only. */
  private silence(turn?: number) {
    for (const [source, owner] of this.sources) {
      if (turn !== undefined && owner !== turn) continue;
      source.onended = null; try { source.stop(); } catch {} source.disconnect();
      this.sources.delete(source);
    }
    if (!this.sources.size) { this.nextAudio = 0; this.level = 0; }
  }
  private receive(event: { type: string; id?: number; text?: string; data?: string; error?: string; state?: string; name?: string; workspace?: string;
    stage?: string; downloaded?: number; total?: number; effect?: string | null; engine?: string; resumed?: boolean; last_id?: number;
    turns?: ServerTurn[]; reason?: string }) {
    if (event.type === "connecting") { this.update({ detail: "Loading local voice…" }); return; }
    if (event.type === "ready") { this.ready(event); return; }
    if (event.type === "error" && !event.id) { this.fail(event.error || "Voice unavailable."); return; }
    const id = event.id;
    if (!id) return;
    const replayed = this.replays.get(id);
    if (replayed !== undefined) {
      if (event.type === "audio" && event.data) this.play(event.data, id);
      if (event.type === "done" || event.type === "error" || event.type === "cancelled") this.replays.delete(id);
      return;
    }
    const known = this.snapshot.turns.some(turn => turn.id === id);
    switch (event.type) {
      case "queued":
        if (this.running === id) this.running = 0;
        this.refresh({ turns: this.patchTurn(id, { state: "queued" }) });
        return;
      case "start":
        this.running = id; this.counts.delete(id);
        this.refresh({ turns: this.patchTurn(id, { state: "thinking" }) });
        return;
      case "user": this.refresh({ turns: this.patchTurn(id, { user: event.text || "" }) }); return;
      case "provision": {
        if (!["download", "load"].includes(event.stage || "") || this.running !== id) return;
        const stage = event.stage as VoicePreparation["stage"];
        this.refresh({ preparation: { stage, downloaded: Math.max(0, event.downloaded || 0), total: Math.max(0, event.total || 0) },
          turns: this.patchTurn(id, { detail: stage === "download" ? "Downloading your chat model…" : "Loading your chat model…" }) });
        return;
      }
      case "text":
        if (!known || this.stale(id)) return;
        this.refresh({ turns: this.patchTurn(id, turn => ({ answer: turn.answer + (event.text || "") })), preparation: null });
        return;
      case "state":
        if (this.stale(id)) return;
        this.refresh({ turns: this.patchTurn(id, { detail: event.state === "transcribing" ? "Hearing your question…"
          : event.state === "analyzing" ? event.text || "Reading sections…" : "Thinking…" }) });
        return;
      // The server words the step, e.g. "Searching earlier in this conversation for …" (ADR 0141).
      case "tool":
        if (this.stale(id)) return;
        this.refresh({ turns: this.patchTurn(id, { detail: event.text || `Checking memory · ${event.name}` }), preparation: null });
        return;
      case "audio": if (event.data && !this.stale(id)) this.play(event.data, id); return;
      case "done":
        if (this.running === id) this.running = 0;
        this.refresh({ turns: this.patchTurn(id, turn => ({ state: turn.state === "stopped" ? "stopped" : "done", detail: "" })), preparation: null });
        return;
      case "error":
        if (this.running === id) this.running = 0;
        this.refresh({ turns: this.patchTurn(id, { state: "failed", error: event.error || "Echo couldn't finish this answer.", detail: "" }),
          preparation: null });
        return;
      case "cancelled":
        if (this.running === id) this.running = 0;
        this.silence(id); this.counts.delete(id);
        this.refresh({ turns: this.patchTurn(id, turn => turn.state === "done" || turn.state === "failed" ? {} : { state: "stopped", detail: "" }),
          preparation: null });
        return;
      case "ignored": {
        // Noise, or Echo hearing itself: not a question, and nothing to show.
        const deliberate = this.snapshot.mode === "ptt" && !this.busy;
        if (this.running === id) this.running = 0;
        this.note = deliberate && event.reason === "unheard" ? "I didn't catch that. Try again." : "";
        this.refresh({ turns: this.snapshot.turns.filter(turn => turn.id !== id) });
        return;
      }
      case "stopped":
        // "Stop" said alone: the API stopped the reply; its voice stops here.
        this.silence(); this.running = 0;
        this.note = "Stopped";
        this.refresh({ turns: this.snapshot.turns.filter(turn => turn.id !== id) });
        return;
      case "end": this.endWake("asked"); return;
    }
  }
  /** Output for a turn the user stopped is never shown or played. */
  private stale(id: number) { return this.snapshot.turns.find(turn => turn.id === id)?.state === "stopped"; }
  private ready(event: { workspace?: string; effect?: string | null; resumed?: boolean; last_id?: number; turns?: ServerTurn[] }) {
    this.effect = event.effect ?? null;
    if (this.snapshot.connected) {
      // The API switched to another model or voice once the reply was answered.
      this.refresh({ workspace: event.workspace });
      return;
    }
    this.id = Math.max(this.id, event.last_id ?? 0);
    const turns = Array.isArray(event.turns) ? event.turns.map(turn => this.fromServer(turn)) : this.snapshot.turns;
    this.running = turns.find(turn => turn.state === "thinking")?.id ?? 0;
    this.counts.clear(); this.reconnected = false;
    const resumed = !!event.resumed && turns.some(turn => turn.state === "thinking" || turn.state === "queued" || turn.away);
    this.refresh({ connected: true, workspace: event.workspace, turns, resumed, resumable: false, elsewhere: false });
    const question = this.question; this.question = undefined;
    if (question) this.submit({ text: question });
    if ((this.armMicrophone && !this.quiet) || this.wakeSession) void this.enableMicrophone();
  }
  private play(encoded: string, id: number) {
    const context = this.context!;
    const binary = atob(encoded), bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
    if (bytes.length % 2) return;
    const count = this.counts.get(id) ?? { output: 0, played: 0 };
    this.counts.set(id, count);
    const view = new DataView(bytes.buffer), buffer = context.createBuffer(1, bytes.length / 2, 24000);
    const samples = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
    if (this.effect === "robot") robotVoice(samples, count.output);
    count.output += samples.length;
    const completed = count.output;
    const acknowledge = () => {
      if (this.counts.get(id) !== count) return;
      count.played = Math.max(count.played, completed);
      this.send({ type: "playback", id, samples: count.played });
    };
    // Far more audio than Echo should ever queue: the rest stays readable and
    // can be played again; the reply itself goes on (ADR 0145).
    if (this.nextAudio - context.currentTime > 30) { acknowledge(); this.note = "Some of the answer was not read aloud. Play it again to hear it."; return; }
    if (!this.analyser) {
      this.analyser = context.createAnalyser(); this.analyser.fftSize = 256;
      this.meter = new Float32Array(256); this.analyser.connect(context.destination);
    }
    if (!this.output) {
      this.output = context.createGain(); this.output.gain.value = this.hearing ? DUCKED_GAIN : 1;
      this.output.connect(this.analyser);
    }
    const source = context.createBufferSource(); source.buffer = buffer; source.connect(this.output);
    this.sources.set(source, id);
    source.onended = () => {
      this.sources.delete(source); source.disconnect();
      acknowledge();
      if (!this.sources.size) { this.nextAudio = 0; this.refresh(); }
    };
    const start = Math.max(context.currentTime + .025, this.nextAudio);
    source.start(start); this.nextAudio = start + buffer.duration;
    if (this.snapshot.phase !== "speaking" && !this.hearing) this.refresh();
  }
  private tick() {
    if (this.analyser && this.meter) {
      this.analyser.getFloatTimeDomainData(this.meter);
      this.level = Math.min(1, Math.sqrt(this.meter.reduce((sum, x) => sum + x * x, 0) / this.meter.length) * 7);
    }
  }
  /** Let go of the conversation without ending it: hiding, closing or moving
   * Echo, or switching its model. A reply in progress goes on in the API, and
   * the next window picks it up (ADR 0145). */
  detach() {
    this.stopped = true; ++this.epoch; ++this.micAttempt;
    this.silence();
    this.socket?.close(); this.socket = undefined;
    this.releaseMicrophone();
    this.analyser?.disconnect(); this.analyser = undefined; this.meter = undefined;
    this.output?.disconnect(); this.output = undefined;
    void this.context?.close().catch(() => {}); this.context = undefined;
    this.micPending = false; this.question = undefined; this.running = 0; this.replays.clear(); this.counts.clear();
    clearInterval(this.timer); this.timer = undefined; this.gate.reset(); this.heldFrames = []; this.hearing = false;
    const resumable = this.snapshot.connected && this.snapshot.turns.length > 0;
    this.update({ connected: false, microphone: false, microphoneError: "", microphoneSettings: false, held: false, preparation: null,
      phase: "idle", detail: "Microphone off", ducked: false, resumed: false, resumable: resumable || this.snapshot.resumable });
    // Hiding or closing ends a wake-started conversation too.
    if (this.wakeSession) this.endWake("stopped");
  }
  /** End the conversation, by the user's choice: the reply in progress stops
   * and the API forgets the conversation. */
  stop() {
    if (this.socket?.readyState === WebSocket.OPEN) this.send({ type: "end" });
    else if (this.snapshot.resumable) void this.forget();
    this.detach();
    this.update({ turns: [], resumable: false, elsewhere: false });
  }
  /** End a conversation this window is not connected to. */
  private async forget() {
    await fetch(`${API_BASE}/api/voice/conversation/end`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => {});
  }
}
