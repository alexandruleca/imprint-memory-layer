import { API_BASE } from "./api-base";

export type VoiceMode = "ptt" | "handsfree";
export type VoicePhase = "idle" | "connecting" | "ready" | "listening" | "thinking" | "speaking" | "error";
export type VoicePreparation = { stage: "install" | "download" | "load"; downloaded: number; total: number };
export type VoiceSnapshot = {
  phase: VoicePhase; detail: string; connected: boolean; microphone: boolean;
  mode: VoiceMode; held: boolean; user: string; answer: string; workspace: string;
  preparation: VoicePreparation | null;
};
export const initialVoice: VoiceSnapshot = { phase: "idle", detail: "Ready when you are", connected: false,
  microphone: false, mode: "ptt", held: false, user: "", answer: "", workspace: "", preparation: null };

// Adaptive energy VAD, with onset hysteresis, endpoint silence and pre-roll.
// This is intentionally separate from the acoustic model; see live-voice.md.
export class VoiceGate {
  noise = .003;
  onset = 0;
  silence = 0;
  active = false;
  frames: Float32Array[] = [];
  pre: Float32Array[] = [];
  reset() { this.onset = this.silence = 0; this.active = false; this.frames = []; this.pre = []; }
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
        this.frames = [...this.pre]; this.pre = [];
      }
    } else {
      this.frames.push(frame);
      this.silence = voiced ? 0 : this.silence + 1;
      if (this.silence >= 30 || this.frames.length >= 1490) {
        const complete = this.frames;
        this.reset();
        return { began, complete };
      }
    }
    return { began, complete: null };
  }
}

export function encodeWav(frames: Float32Array[], sourceRate: number) {
  const count = frames.reduce((sum, frame) => sum + frame.length, 0);
  if (count / sourceRate > 30) throw new Error("Keep each question under 30 seconds.");
  const joined = new Float32Array(count);
  let offset = 0;
  for (const frame of frames) { joined.set(frame, offset); offset += frame.length; }
  const rate = 24000, length = Math.floor(count * rate / sourceRate);
  const data = new ArrayBuffer(44 + length * 2), view = new DataView(data);
  function word(at: number, text: string) { for (let i = 0; i < text.length; i++) view.setUint8(at + i, text.charCodeAt(i)); }
  word(0, "RIFF"); view.setUint32(4, 36 + length * 2, true); word(8, "WAVE"); word(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true);
  view.setUint16(34, 16, true); word(36, "data"); view.setUint32(40, length * 2, true);
  for (let i = 0; i < length; i++) {
    // Box averaging downsamples the common 48 kHz browser input to 24 kHz.
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
  private meter?: Float32Array<ArrayBuffer>;
  private sources = new Set<AudioBufferSourceNode>();
  private nextAudio = 0;
  private id = 0;
  private accepted = 0;
  private complete = 0;
  private gate = new VoiceGate();
  private heldFrames: Float32Array[] = [];
  private timer?: ReturnType<typeof setInterval>;
  private stopped = true;
  private epoch = 0;
  private doneAcknowledged = 0;
  private micPending = false;
  constructor(private changed: (value: VoiceSnapshot) => void) {}
  private update(values: Partial<VoiceSnapshot>) {
    this.snapshot = { ...this.snapshot, ...values };
    this.changed(this.snapshot);
  }
  private send(message: object) { if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(message)); }
  private fail(message: string) { this.stop(); this.update({ phase: "error", detail: message }); }

  async start(engine: "chat" | "lfm") {
    this.stop(); this.stopped = false;
    const epoch = ++this.epoch;
    this.update({ phase: "connecting", detail: "Preparing audio…", user: "", answer: "" });
    try {
      this.context = new AudioContext();
      let audioTimer: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([this.context.resume(), new Promise<never>((_, reject) => {
          audioTimer = setTimeout(() => reject(new Error("Audio output is unavailable. Choose a speaker or headset in sound settings, then try again.")), 10000);
        })]);
      } finally { clearTimeout(audioTimer); }
      if (this.stopped || epoch !== this.epoch) return;
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
      socket.onclose = () => { if (!this.stopped && epoch === this.epoch) this.fail("Voice disconnected. Start again to reconnect."); };
      this.update({ workspace: session.workspace });
      this.timer = setInterval(() => this.tick(), 40);
    } catch (error) {
      if (!this.stopped && epoch === this.epoch) this.fail(error instanceof Error ? error.message : "Voice couldn't start.");
    }
  }

  async enableMicrophone() {
    if (!this.snapshot.connected || this.stream || this.micPending) return;
    const epoch = this.epoch;
    this.micPending = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1,
        echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
      if (this.stopped || epoch !== this.epoch) { stream.getTracks().forEach(track => track.stop()); return; }
      this.stream = stream;
      const context = this.context!;
      await context.audioWorklet.addModule("/audio/imprint-capture.js");
      if (this.stopped || epoch !== this.epoch) return;
      this.capture = new AudioWorkletNode(context, "imprint-capture");
      this.capture.port.onmessage = event => this.frame(event.data);
      this.source = context.createMediaStreamSource(stream);
      this.silent = context.createGain(); this.silent.gain.value = 0;
      this.source.connect(this.capture).connect(this.silent).connect(context.destination);
      stream.getAudioTracks().forEach(track => { track.enabled = this.snapshot.mode === "handsfree"; });
      this.update({ microphone: true, detail: this.snapshot.mode === "ptt" ? "Hold to talk" : "Listening for your voice" });
    } catch (error) {
      if (this.stopped || epoch !== this.epoch) return;
      this.stream?.getTracks().forEach(track => track.stop()); this.stream = undefined;
      this.capture?.disconnect(); this.capture?.port.close(); this.capture = undefined;
      this.source?.disconnect(); this.source = undefined; this.silent?.disconnect(); this.silent = undefined;
      const denied = error instanceof DOMException && error.name === "NotAllowedError";
      const platform = window.electronAPI?.platform;
      const detail = denied && platform === "darwin"
        ? "Microphone permission denied. Allow Imprint in System Settings → Privacy & Security → Microphone."
        : denied && platform === "win32"
          ? "Microphone permission denied. Allow microphone access for desktop apps in Windows Settings → Privacy & security."
          : "Microphone unavailable. Check permissions, or type below.";
      this.update({ microphone: false, detail });
    } finally {
      if (epoch === this.epoch) this.micPending = false;
    }
  }

  setMode(mode: VoiceMode) {
    this.interrupt(); this.gate.reset(); this.heldFrames = [];
    this.stream?.getAudioTracks().forEach(track => { track.enabled = mode === "handsfree"; });
    this.update({ mode, held: false, detail: mode === "ptt" ? "Hold to talk" : "Listening for your voice" });
  }
  hold() {
    if (!this.snapshot.connected || !this.snapshot.microphone || this.snapshot.mode !== "ptt" || this.snapshot.held) return;
    this.interrupt(); this.heldFrames = [];
    this.stream?.getAudioTracks().forEach(track => { track.enabled = true; });
    this.update({ held: true, phase: "listening", detail: "Listening… release to send" });
  }
  release(send = true) {
    if (!this.snapshot.held) return;
    this.stream?.getAudioTracks().forEach(track => { track.enabled = false; });
    this.update({ held: false });
    const frames = this.heldFrames; this.heldFrames = [];
    if (send && frames.length >= 8) this.submit({ audio: encodeWav(frames, this.context!.sampleRate) });
    else this.update({ phase: "ready", detail: "Hold to talk" });
  }
  private frame(frame: Float32Array) {
    if (!this.snapshot.connected) return;
    if (this.snapshot.mode === "ptt") {
      if (this.snapshot.held) { this.heldFrames.push(frame); if (this.heldFrames.length >= 1490) this.release(); }
      return;
    }
    const result = this.gate.feed(frame, this.sources.size > 0, this.sensitivity);
    if (result.began) { this.interrupt(); this.update({ phase: "listening", detail: "Listening…" }); }
    if (result.complete) this.submit({ audio: encodeWav(result.complete, this.context!.sampleRate) });
  }
  text(text: string) { if (text.trim()) this.submit({ text: text.trim().slice(0, 2000) }); }
  private submit(content: { audio?: string; text?: string }) {
    if (!this.snapshot.connected) return;
    this.interrupt(); this.accepted = ++this.id; this.complete = 0;
    this.update({ phase: "thinking", detail: "Thinking…", answer: "", user: content.text || "" });
    this.send({ type: "turn", id: this.id, ...content });
  }
  interrupt() {
    const pending = this.accepted > 0;
    this.accepted = 0; this.complete = 0;
    for (const source of this.sources) { source.onended = null; try { source.stop(); } catch {} source.disconnect(); }
    this.sources.clear(); this.nextAudio = 0; this.level = 0;
    if (this.snapshot.connected) {
      if (pending) this.send({ type: "cancel" });
      this.update({ phase: "ready", preparation: null, detail: this.snapshot.mode === "ptt" ? "Hold to talk" : "Listening for your voice" });
    }
  }
  private receive(event: { type: string; id?: number; text?: string; data?: string; error?: string; state?: string; name?: string; workspace?: string; stage?: string; downloaded?: number; total?: number }) {
    if (event.type === "connecting") { this.update({ detail: "Loading local voice…" }); return; }
    if (event.type === "ready") { this.update({ connected: true, phase: "ready", detail: "Type a question or enable your microphone", workspace: event.workspace }); return; }
    if (event.type === "error" && !event.id) { this.fail(event.error || "Voice unavailable."); return; }
    if (!event.id || event.id !== this.accepted) return;
    if (event.type === "user") this.update({ user: event.text || "" });
    if (event.type === "provision" && ["install", "download", "load"].includes(event.stage || "")) {
      const stage = event.stage as VoicePreparation["stage"];
      this.update({ preparation: { stage, downloaded: Math.max(0, event.downloaded || 0), total: Math.max(0, event.total || 0) },
        detail: stage === "download" ? "Downloading your chat model…" : stage === "install" ? "Setting up local chat…" : "Loading your chat model…" });
      return;
    }
    if (event.type === "text") this.update({ answer: this.snapshot.answer + event.text, preparation: null });
    if (event.type === "state") this.update({ detail: event.state === "transcribing" ? "Hearing your question…" : "Thinking…" });
    if (event.type === "tool") this.update({ detail: `Checking memory · ${event.name}`, preparation: null });
    if (event.type === "audio" && event.data) this.play(event.data);
    if (event.type === "done") { this.complete = event.id; this.update({ preparation: null }); }
    if (event.type === "error") { this.interrupt(); this.update({ detail: event.error || "Try again." }); }
  }
  private play(encoded: string) {
    const context = this.context!;
    const binary = atob(encoded), bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
    if (bytes.length % 2) return;
    const view = new DataView(bytes.buffer), buffer = context.createBuffer(1, bytes.length / 2, 24000);
    const samples = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
    if (this.nextAudio - context.currentTime > 30) { this.interrupt(); this.update({ detail: "Reply paused. Ask for a shorter answer." }); return; }
    if (!this.analyser) {
      this.analyser = context.createAnalyser(); this.analyser.fftSize = 256;
      this.meter = new Float32Array(256); this.analyser.connect(context.destination);
    }
    const source = context.createBufferSource(); source.buffer = buffer; source.connect(this.analyser);
    this.sources.add(source);
    source.onended = () => { this.sources.delete(source); source.disconnect(); };
    const start = Math.max(context.currentTime + .025, this.nextAudio);
    source.start(start); this.nextAudio = start + buffer.duration;
    this.update({ phase: "speaking", detail: "Speaking · you can interrupt" });
  }
  private tick() {
    if (this.analyser && this.meter) {
      this.analyser.getFloatTimeDomainData(this.meter);
      this.level = Math.min(1, Math.sqrt(this.meter.reduce((sum, x) => sum + x * x, 0) / this.meter.length) * 7);
    }
    if (this.complete && !this.sources.size) {
      if (this.doneAcknowledged !== this.complete) { this.send({ type: "played", id: this.complete }); this.doneAcknowledged = this.complete; }
      this.complete = 0;
      this.accepted = 0;
      this.update({ phase: "ready", detail: this.snapshot.mode === "ptt" ? "Hold to talk" : "Listening for your voice" });
    }
  }
  stop() {
    this.stopped = true; ++this.epoch;
    this.interrupt();
    this.socket?.close(); this.socket = undefined;
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = undefined;
    this.capture?.disconnect(); this.capture?.port.close(); this.capture = undefined;
    this.source?.disconnect(); this.source = undefined; this.silent?.disconnect(); this.silent = undefined;
    this.analyser?.disconnect(); this.analyser = undefined; this.meter = undefined;
    void this.context?.close().catch(() => {}); this.context = undefined;
    this.micPending = false;
    clearInterval(this.timer); this.timer = undefined; this.gate.reset(); this.heldFrames = [];
    this.update({ connected: false, microphone: false, held: false, preparation: null, phase: "idle", detail: "Microphone off" });
  }
}
