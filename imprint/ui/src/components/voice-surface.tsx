"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { ArrowUpRight, Download, GripHorizontal, Mic, MicOff, Pin, Square, X, AudioLines } from "lucide-react";
import { Button } from "@/components/ui/button";
import { VoiceClient, initialVoice, type VoiceSnapshot } from "@/lib/voice-client";
import { API_BASE } from "@/lib/api-base";
import "@/styles/voice.css";

const EchoAvatar = dynamic(() => import("./echo-avatar"), { ssr: false });
type RuntimeStatus = { configured: boolean; installed: boolean; supported: boolean; chat_provider: string; workspace: string };

export function VoiceSurface({ compact = false }: { compact?: boolean }) {
  const [voice, setVoice] = useState<VoiceSnapshot>({ ...initialVoice });
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [engine, setEngine] = useState<"chat" | "lfm">("chat");
  const [text, setText] = useState("");
  const [pinned, setPinned] = useState(false);
  const [desktop, setDesktop] = useState(false);
  const client = useRef<VoiceClient | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    client.current = new VoiceClient(setVoice);
    // Read host capabilities only after hydration.
    const timer = setTimeout(() => setDesktop(!!window.electronAPI?.openCompanion), 0);
    const controller = new AbortController();
    void fetch(`${API_BASE}/api/voice/status`, { signal: controller.signal }).then(r => r.ok ? r.json() : null).then(setRuntime).catch(() => {});
    function hidden() { if (document.hidden) client.current?.stop(); }
    function blur() { client.current?.release(false); }
    document.addEventListener("visibilitychange", hidden);
    window.addEventListener("blur", blur);
    return () => { clearTimeout(timer); controller.abort(); client.current?.stop(); document.removeEventListener("visibilitychange", hidden); window.removeEventListener("blur", blur); };
  }, []);
  const energy = () => client.current?.level || 0;
  const ready = runtime?.installed && runtime.supported;
  const active = voice.connected || voice.phase === "connecting";
  function popout() {
    client.current?.stop();
    if (window.electronAPI?.openCompanion) window.electronAPI.openCompanion();
    else window.open("/companion/", "imprint-echo", "popup,width=380,height=560");
  }
  return <section className={`voice-surface ${compact ? "voice-compact" : ""}`} data-phase={voice.phase}>
    <header className={`voice-header ${compact ? "app-drag" : ""}`}>
      <div className="voice-wordmark"><AudioLines size={17} aria-hidden="true"/><span>IMPRINT <b>/ ECHO</b></span></div>
      <div className="voice-window-actions app-no-drag">
        {compact ? <>
          {desktop && <Button variant="ghost" size="icon-sm" aria-label={pinned ? "Unpin companion" : "Keep companion on top"} aria-pressed={pinned} onClick={() => { window.electronAPI?.pinCompanion(!pinned); setPinned(!pinned); }}><Pin size={14}/></Button>}
          <Button variant="ghost" size="icon-sm" aria-label="Close companion" onClick={() => { client.current?.stop(); if (window.electronAPI?.closeCompanion) window.electronAPI.closeCompanion(); else window.close(); }}><X size={16}/></Button>
        </> : <Button variant="ghost" onClick={popout} className="voice-popout"><ArrowUpRight size={15}/> Float Echo</Button>}
      </div>
    </header>
    {!compact && <div className="voice-intro"><span className="voice-eyebrow">A LITTLE PRESENCE. A FAMILIAR VOICE.</span><h1>Your thoughts,<br/><em>in conversation.</em></h1><p>A quiet companion for your local Imprint memory.</p></div>}
    <div className="voice-stage">
      <div className="voice-halo" aria-hidden="true"/>
      <EchoAvatar phase={voice.phase} energy={energy} compact={compact}/>
      <div className="voice-caption"><span className={`voice-dot ${active ? "active" : ""}`}/><span>{voice.phase === "idle" ? "ECHO · LOCAL VOICE COMPANION" : voice.detail}</span></div>
    </div>
    <div className="voice-control-area">
      <div className="voice-modes" role="group" aria-label="Microphone mode">
        <Button variant="ghost" aria-pressed={voice.mode === "ptt"} onClick={() => client.current?.setMode("ptt")}>Push to talk</Button>
        <Button variant="ghost" aria-pressed={voice.mode === "handsfree"} onClick={() => client.current?.setMode("handsfree")}>Hands-free</Button>
      </div>
      <div className="voice-primary-controls">
        {!active ? <Button className="voice-start" disabled={!ready} onClick={() => void client.current?.start(engine)}><AudioLines size={18}/> Start conversation</Button>
          : voice.phase === "connecting" ? <Button className="voice-start" onClick={() => client.current?.stop()}>Connecting… · Cancel</Button>
          : <>
            {!voice.microphone ? <Button className="voice-start" onClick={() => void client.current?.enableMicrophone()}><Mic size={18}/> Enable microphone</Button>
              : voice.mode === "ptt" ? <Button className={`voice-start voice-hold ${voice.held ? "held" : ""}`} aria-label="Hold to talk" aria-pressed={voice.held}
                onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); client.current?.hold(); }}
                onPointerUp={() => client.current?.release()} onPointerCancel={() => client.current?.release(false)}
                onLostPointerCapture={() => client.current?.release(false)}
                onKeyDown={event => { if ((event.key === " " || event.key === "Enter") && !event.repeat) { event.preventDefault(); client.current?.hold(); } }}
                onKeyUp={event => { if (event.key === " " || event.key === "Enter") { event.preventDefault(); client.current?.release(); } }}
                onBlur={() => client.current?.release(false)}><Mic size={18}/>{voice.held ? "Release to send" : "Hold to talk"}</Button>
              : <Button className="voice-start" onClick={() => client.current?.interrupt()}><Mic size={18}/> Listening · interrupt</Button>}
            <Button variant="ghost" size="icon-lg" aria-label="Interrupt reply" onClick={() => client.current?.interrupt()}><Square size={16}/></Button>
            <Button variant="ghost" size="icon-lg" aria-label="Stop conversation and microphone" onClick={() => client.current?.stop()}><MicOff size={18}/></Button>
          </>}
      </div>
      <p className="voice-privacy" role="status">{voice.phase === "error" ? voice.detail : voice.microphone ? voice.mode === "handsfree" ? "Mic on · speak to start, speak again to interrupt" : "Mic armed · hold button or focused Space key" : "Microphone off · English · runs locally"}</p>
      <form className="voice-text" onSubmit={event => { event.preventDefault(); if (voice.connected && text.trim()) { client.current?.text(text); setText(""); input.current?.focus(); } }}>
        <input ref={input} aria-label="Type a question" placeholder="Or type a thought…" value={text} maxLength={2000} disabled={!voice.connected} onChange={event => setText(event.target.value)}/>
        <Button type="submit" variant="ghost" size="icon-sm" aria-label="Send question" disabled={!voice.connected || !text.trim()}><ArrowUpRight size={17}/></Button>
      </form>
      {(voice.user || voice.answer) && <div className="voice-transcript"><p className="voice-user">{voice.user}</p><p>{voice.answer || "…"}</p></div>}
      <div className="voice-settings">
        <label>Voice reasoning <select aria-label="Voice reasoning model" value={engine} disabled={active} onChange={event => setEngine(event.target.value as "chat" | "lfm")}>
          <option value="chat" disabled={runtime?.chat_provider !== "local"}>Same local model as Chat</option><option value="lfm">Small voice model · LFM 1.5B</option>
        </select></label>
        {voice.mode === "handsfree" && <label>Speech threshold <input type="range" aria-label="Speech detection threshold" min="8" max="60" defaultValue="16" onChange={event => { if (client.current) client.current.sensitivity = +event.target.value / 1000; }}/><small>Raise if room noise interrupts</small></label>}
      </div>
      {runtime && !ready && <div className="voice-setup"><p>{runtime.supported ? "Local voice needs its optional speech runtime." : "The speech runtime currently supports Linux x64, including WSL2."}</p><a href="https://github.com/alexandruleca/imprint-memory-layer/blob/feat/local-voice-avatar/docs/live-voice.md" target="_blank" rel="noreferrer">Voice setup guide <ArrowUpRight size={12}/></a></div>}
    </div>
    {!compact && <footer className="voice-footer"><span>PARTICLE STUDY 03 / ECHO</span><a href="/models/imprint-echo.glb" download><Download size={13}/> Download 3D model</a></footer>}
    {compact && desktop && <div className="voice-grip app-drag"><GripHorizontal size={16}/><span>Drag to move</span></div>}
  </section>;
}
