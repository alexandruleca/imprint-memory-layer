"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { ArrowUpRight, Download, Mic, MicOff, Pin, Settings2, Square, X, AudioLines } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import { VoiceClient, initialVoice, type VoiceMode, type VoiceSnapshot } from "@/lib/voice-client";
import { readVoicePreferences, saveVoicePreferences } from "@/lib/voice-preferences";
import { API_BASE } from "@/lib/api-base";
import { useVoiceSetup } from "@/lib/voice-setup";
import { VoiceSetup, VoiceChatSetup } from "@/components/voice-setup";
import { AvatarPicker } from "@/components/avatar-picker";
import { getConfig, setConfigValue } from "@/lib/api";
import type { ConfigChoice } from "@/lib/types";
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
  const [shortcutAvailable, setShortcutAvailable] = useState(true);
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [rememberMicrophone, setRememberMicrophone] = useState(false);
  // Echo's look (the voice.avatar setting); null until it is known, so the
  // default never flashes before a different choice loads.
  const [avatar, setAvatar] = useState<string | null>(null);
  const [looks, setLooks] = useState<ConfigChoice[]>([]);
  const setup = useVoiceSetup();
  const client = useRef<VoiceClient | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const drag = useRef<number | null>(null);
  const shortcut = desktop ? window.electronAPI?.platform === "darwin" ? "⌘⌥⇧E" : "Ctrl+Alt+Shift+E" : "";
  function endDrag(event?: React.PointerEvent<HTMLElement>) {
    if (drag.current === null) return;
    const pointer = drag.current; drag.current = null;
    if (event?.currentTarget.hasPointerCapture(pointer)) event.currentTarget.releasePointerCapture(pointer);
    window.electronAPI?.endCompanionDrag?.();
  }
  useEffect(() => {
    client.current = new VoiceClient(setVoice);
    const preferences = readVoicePreferences();
    client.current.setMode(preferences.mode);
    client.current.armMicrophone = preferences.microphoneEnabled;
    // Read browser storage and host capabilities only after hydration.
    const timer = setTimeout(() => {
      setDesktop(!!window.electronAPI?.openCompanion);
      setEngine(preferences.engine); setRememberMicrophone(preferences.microphoneEnabled);
    }, 0);
    // The main process owns the window, so pinning shows the state it reports.
    function companion(state: { pinned: boolean; shortcutAvailable?: boolean } | null) {
      if (state) { setPinned(state.pinned); setShortcutAvailable(state.shortcutAvailable !== false); }
    }
    const unsubscribe = window.electronAPI?.onCompanionState?.(companion);
    void window.electronAPI?.getCompanionState?.().then(companion);
    const controller = new AbortController();
    void fetch(`${API_BASE}/api/voice/status`, { signal: controller.signal }).then(r => r.ok ? r.json() : null).then(setRuntime).catch(() => {});
    void getConfig().then(config => {
      const setting = config.settings?.find(s => s.key === "voice.avatar");
      setLooks(setting?.choices ?? []);
      setAvatar(String(setting?.value || "echo"));
    }).catch(() => setAvatar("echo"));
    function hidden() { if (document.hidden) client.current?.stop(); }
    function blur() { client.current?.release(false); }
    function transfer() { if (!compact) client.current?.stop(); }
    document.addEventListener("visibilitychange", hidden);
    window.addEventListener("blur", blur);
    window.addEventListener("imprint:stop-dashboard-voice", transfer);
    return () => { clearTimeout(timer); controller.abort(); unsubscribe?.(); client.current?.stop(); document.removeEventListener("visibilitychange", hidden); window.removeEventListener("blur", blur); window.removeEventListener("imprint:stop-dashboard-voice", transfer); };
  }, [compact]);
  const energy = () => client.current?.level || 0;
  const ready = setup.state ? setup.state.installed && setup.state.supported && !setup.state.busy
    && !["error", "blocked", "cancelled"].includes(setup.state.stage) : runtime?.installed && runtime.supported;
  const active = voice.connected || voice.phase === "connecting";
  async function chooseLook(next: string) {
    const previous = avatar;
    setAvatar(next);
    try { await setConfigValue("voice.avatar", next); } catch { setAvatar(previous); }
  }
  const picker = looks.length > 1 && avatar
    ? (compactPicker: boolean) => <AvatarPicker choices={looks} value={avatar} onChange={look => void chooseLook(look)} compact={compactPicker}/>
    : null;
  function chooseMode(value: VoiceMode) { client.current?.setMode(value); saveVoicePreferences({ mode: value }); }
  function chooseEngine(value: "chat" | "lfm") { setEngine(value); saveVoicePreferences({ engine: value }); }
  /** The remembered microphone choice (ADR 0069): later conversations arm the
   * microphone when they are ready; clearing it turns capture off now. */
  function remember(value: boolean) {
    setRememberMicrophone(value); saveVoicePreferences({ microphoneEnabled: value });
    client.current?.setMicrophonePreference(value);
  }
  // The full page shows these under its controls; the companion keeps them in its settings panel.
  const textForm = <form className="voice-text" onSubmit={event => { event.preventDefault(); if (text.trim()) { client.current?.ask(text, engine); setText(""); input.current?.focus(); } }}>
    <input ref={input} aria-label="Type a question" placeholder="Or type a thought…" value={text} maxLength={2000} onChange={event => setText(event.target.value)}/>
    <Button type="submit" variant="ghost" size="icon-sm" aria-label="Send question" disabled={!text.trim()}><ArrowUpRight size={17}/></Button>
  </form>;
  const transcript = (voice.user || voice.answer) && <div className="voice-transcript"><p className="voice-user">{voice.user}</p><p>{voice.answer || "…"}</p></div>;
  function popout() {
    client.current?.stop();
    if (window.electronAPI?.openCompanion) window.electronAPI.openCompanion();
    else window.open("/companion/", "imprint-echo", "popup,width=380,height=560");
  }
  return <section className={`voice-surface ${compact ? "voice-compact" : ""} ${!ready || voice.preparation ? "voice-needs-setup" : ""}`} data-phase={voice.phase}
    onPointerDown={event => {
      // The head and header move the window (ADR 0070). The controls never do, nor
      // the settings panel, whose events reach this handler through its portal.
      if (!compact || !desktop || optionsOpen || event.button !== 0 || !(event.target instanceof Element)
          || !event.currentTarget.contains(event.target)
          || event.target.closest(".voice-control-area,.voice-window-actions")) return;
      drag.current = event.pointerId; event.currentTarget.setPointerCapture(event.pointerId);
      window.electronAPI?.beginCompanionDrag?.({ x: event.screenX, y: event.screenY }); event.preventDefault();
    }}
    onPointerMove={event => {
      if (drag.current !== event.pointerId) return;
      // A release the window never saw must not leave it following the cursor.
      if (!(event.buttons & 1)) { endDrag(event); return; }
      window.electronAPI?.moveCompanionDrag?.({ x: event.screenX, y: event.screenY });
    }}
    onPointerUp={endDrag} onPointerCancel={endDrag} onLostPointerCapture={() => endDrag()}>
    <header className="voice-header">
      <div className="voice-wordmark"><AudioLines size={17} aria-hidden="true"/><span>IMPRINT <b>/ ECHO</b></span></div>
      <div className="voice-window-actions">
        {!compact && <Button variant="ghost" onClick={popout} className="voice-popout"><ArrowUpRight size={15}/> Float Echo</Button>}
        <Popover open={optionsOpen} onOpenChange={setOptionsOpen}>
          <PopoverTrigger render={<Button variant="ghost" size="icon-sm" aria-label="Companion settings"/>}><Settings2 size={16}/></PopoverTrigger>
          <PopoverContent align="end" className={`voice-options ${compact ? "voice-options-compact" : ""}`} aria-label="Companion settings">
            <PopoverTitle>Companion settings</PopoverTitle>
            {compact && picker && <div className="voice-look"><span>Echo&apos;s look</span>{picker(true)}</div>}
            <div className="voice-settings">
              <label>Voice reasoning <select aria-label="Voice reasoning model" value={engine} disabled={active} onChange={event => chooseEngine(event.target.value as "chat" | "lfm")}>
                <option value="chat" disabled={runtime?.chat_provider !== "local"}>Same local model as Chat</option><option value="lfm">Small voice model · LFM 1.5B</option>
              </select></label>
              <label className="voice-check"><Checkbox aria-label="Use microphone for conversations" checked={rememberMicrophone} onCheckedChange={remember}/>Use microphone for conversations</label>
              <small>Remembered on this device. Starting a conversation turns the microphone on, after the system allows it; push-to-talk stays silent until you hold it.</small>
              {voice.mode === "handsfree" && <label>Speech threshold <input type="range" aria-label="Speech detection threshold" min="8" max="60" defaultValue="16" onChange={event => { if (client.current) client.current.sensitivity = +event.target.value / 1000; }}/><small>Raise if room noise interrupts</small></label>}
            </div>
            {compact && ready && textForm}{compact && ready && transcript}
            {compact && <div className="voice-option-actions">
              {active && <><Button variant="ghost" aria-label="Interrupt reply" onClick={() => client.current?.interrupt()}><Square size={14}/> Interrupt</Button>
                <Button variant="ghost" aria-label="Stop conversation and microphone" onClick={() => client.current?.stop()}><MicOff size={14}/> Stop</Button></>}
              {desktop && <Button variant="ghost" aria-label={pinned ? "Unpin companion" : "Keep companion on top"} aria-pressed={pinned} className="voice-pin" onClick={() => window.electronAPI?.pinCompanion(!pinned)}><Pin size={14} fill={pinned ? "currentColor" : "none"}/> {pinned ? "On top" : "Keep on top"}</Button>}
              <Button variant="ghost" aria-label="Close companion" onClick={() => { client.current?.stop(); if (window.electronAPI?.closeCompanion) window.electronAPI.closeCompanion(); else window.close(); }}><X size={14}/> Close</Button>
            </div>}
            {compact && desktop && <small className="voice-hint">Drag Echo to move it; with the dashboard hidden, Echo and the Imprint button move together. {shortcutAvailable ? `${shortcut} recalls Echo.` : "Recall Echo from the Imprint tray menu."}</small>}
          </PopoverContent>
        </Popover>
      </div>
    </header>
    {!compact && <div className="voice-intro"><span className="voice-eyebrow">A LITTLE PRESENCE. A FAMILIAR VOICE.</span><h1>Your thoughts,<br/> <em>in conversation.</em></h1><p>A quiet companion for your local Imprint memory.</p></div>}
    {!ready && !compact && <VoiceSetup setup={setup}/>}
    {!ready && !compact && picker && <div className="echo-look"><p className="echo-setup-eyebrow">CHOOSE HOW ECHO LOOKS</p>{picker(false)}</div>}
    {!compact && <VoiceChatSetup preparation={voice.preparation}/>}
    <div className="voice-stage">
      <div className="voice-halo" aria-hidden="true"/>
      {avatar && <EchoAvatar phase={voice.phase} energy={energy} model={avatar} compact={compact}/>}
      <div className="voice-caption"><span className={`voice-dot ${active ? "active" : ""}`}/><span>{voice.phase === "idle" ? "ECHO · LOCAL VOICE COMPANION" : voice.detail}</span></div>
    </div>
    <div className="voice-control-area">
      {!ready ? compact && <VoiceSetup setup={setup} compact/> : <>
      <div className="voice-modes" role="group" aria-label="Microphone mode">
        <Button variant="ghost" aria-pressed={voice.mode === "ptt"} onClick={() => chooseMode("ptt")}>Push to talk</Button>
        <Button variant="ghost" aria-pressed={voice.mode === "handsfree"} onClick={() => chooseMode("handsfree")}>Hands-free</Button>
      </div>
      <div className="voice-primary-controls">
        {!active ? <Button className="voice-start" disabled={!ready} onClick={() => void client.current?.start(engine)}><AudioLines size={18}/> Start conversation</Button>
          : voice.phase === "connecting" ? <Button className="voice-start" onClick={() => client.current?.stop()}>Connecting… · Cancel</Button>
          : <>
            {!voice.microphone ? <Button className="voice-start" onClick={() => void client.current?.enableMicrophone().then(on => { if (on) remember(true); })}><Mic size={18}/> Enable microphone</Button>
              : voice.mode === "ptt" ? <Button className={`voice-start voice-hold ${voice.held ? "held" : ""}`} aria-label="Hold to talk" aria-pressed={voice.held}
                onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); client.current?.hold(); }}
                onPointerUp={() => client.current?.release()} onPointerCancel={() => client.current?.release(false)}
                onLostPointerCapture={() => client.current?.release(false)}
                onKeyDown={event => { if ((event.key === " " || event.key === "Enter") && !event.repeat) { event.preventDefault(); client.current?.hold(); } }}
                onKeyUp={event => { if (event.key === " " || event.key === "Enter") { event.preventDefault(); client.current?.release(); } }}
                onBlur={() => client.current?.release(false)}><Mic size={18}/>{voice.held ? "Release to send" : "Hold to talk"}</Button>
              : <Button className="voice-start" onClick={() => client.current?.interrupt()}><Mic size={18}/> Listening · interrupt</Button>}
            {!compact && <><Button variant="ghost" size="icon-lg" aria-label="Interrupt reply" onClick={() => client.current?.interrupt()}><Square size={16}/></Button>
              <Button variant="ghost" size="icon-lg" aria-label="Stop conversation and microphone" onClick={() => client.current?.stop()}><MicOff size={18}/></Button></>}
          </>}
      </div>
      {compact && <VoiceChatSetup preparation={voice.preparation} compact/>}
      <p className="voice-privacy" role="status" data-alert={voice.phase === "error" || !!voice.microphoneError || undefined}>{voice.phase === "error" ? voice.detail : voice.microphoneError || (voice.microphone ? voice.mode === "handsfree" ? "Mic on · speak to start, speak again to interrupt" : "Mic armed · hold button or focused Space key" : "Microphone off · English · runs locally")}</p>
      {voice.microphoneSettings && desktop && <Button variant="outline" className="voice-settings-link" onClick={() => void window.electronAPI?.openMicrophoneSettings?.()}>Open microphone settings</Button>}
      {!compact && textForm}{!compact && transcript}
      {!compact && picker && <div className="voice-look"><span>Echo&apos;s look</span>{picker(true)}</div>}
      </>}
    </div>
    {!compact && <footer className="voice-footer"><span>ECHO / LOCAL COMPANION</span><a href={`/models/imprint-${avatar || "echo"}.glb`} download><Download size={13}/> Download 3D model</a></footer>}
  </section>;
}
