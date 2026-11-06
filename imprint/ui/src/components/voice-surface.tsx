"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { ArrowDownToDot, ArrowUpRight, Download, History, MessageSquare, Mic, MicOff, Pin, Play, RotateCcw, Settings2, Square, X, AudioLines } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import { Switch } from "@/components/ui/switch";
import { MicrophoneAccessError, VoiceClient, initialVoice, microphoneAccessAllows, microphoneProblem, replying, type CarriedChat, type VoiceMode, type VoiceSnapshot, type VoiceTurn } from "@/lib/voice-client";
import { downloadWakeModel, cancelWakeDownload, useWakeState, wakeStatus, wakeSummary } from "@/lib/wake";
import { readVoicePreferences, saveVoicePreferences } from "@/lib/voice-preferences";
import { API_BASE } from "@/lib/api-base";
import { useVoiceSetup } from "@/lib/voice-setup";
import { useAudioDevices, type AudioDevice } from "@/lib/audio-devices";
import { VoiceSetup, VoiceChatSetup } from "@/components/voice-setup";
import { AvatarPicker } from "@/components/avatar-picker";
import { getChatSession, getChatSessions, getConfig, setConfigValue } from "@/lib/api";
import type { ChatSession, ConfigChoice } from "@/lib/types";
import "@/styles/voice.css";

const EchoAvatar = dynamic(() => import("./echo-avatar"), { ssr: false });
type RuntimeStatus = { configured: boolean; installed: boolean; supported: boolean; chat_provider: string; workspace: string };
type TurnActions = { retry: (id: number) => void; replay: (id: number) => void; withdraw: (id: number) => void };

/** A turn's state in plain words, with what can be done about it (ADR 0145). */
function TurnStatus({ turn, voice, last, actions }: { turn: VoiceTurn; voice: VoiceSnapshot; last: boolean; actions: TurnActions }) {
  const connected = voice.connected;
  if (turn.state === "queued") return <p className="voice-turn-status">
    <span>Waits for the answer before it</span>
    {connected && <Button variant="ghost" size="xs" aria-label="Remove waiting question" onClick={() => actions.withdraw(turn.id)}><X/> Remove</Button>}
  </p>;
  // What Echo is doing shows under its head; here only that it has not answered yet.
  if (turn.state === "thinking") return turn.answer ? null : <p className="voice-turn-status">Thinking…</p>;
  if (turn.state === "stopped") return <p className="voice-turn-status">Stopped</p>;
  if (turn.state === "failed") return <p className="voice-turn-status" data-alert>
    <span>{turn.error || "Echo couldn't finish this answer."}</span>
    {connected && turn.user && <Button variant="ghost" size="xs" onClick={() => actions.retry(turn.id)}><RotateCcw/> Try again</Button>}
  </p>;
  const speaking = voice.phase === "speaking";
  if (!turn.answer.trim() || (!turn.away && !(last && connected && !speaking))) return null;
  return <p className="voice-turn-status">
    {turn.away && <span>Answered while Echo was away</span>}
    {connected && !speaking && <Button variant="ghost" size="xs" onClick={() => actions.replay(turn.id)}><Play/> {turn.away ? "Play answer" : "Play again"}</Button>}
  </p>;
}

/** The conversation's recent questions and answers, oldest first. The small
 * window shows the newest exchange with one line per waiting question
 * ("latest"); its settings panel lists them all without the notice ("panel"). */
function Turns({ voice, actions, variant = "full" }: { voice: VoiceSnapshot; actions: TurnActions; variant?: "full" | "latest" | "panel" }) {
  const lastDone = voice.turns.findLast(turn => turn.state === "done")?.id;
  const latest = variant === "latest";
  const shown = latest ? voice.turns.filter((turn, index) => index === voice.turns.length - 1 || turn.state === "queued"
    || turn.state === "thinking") : voice.turns;
  if (!shown.length) return null;
  const spokenQuestion = (turn: VoiceTurn) => turn.user || (turn.spoken ? "Your spoken question" : "");
  return <>{voice.resumed && variant !== "panel" && <p className="voice-turn-note" role="status">Picked up where you left off</p>}
  {voice.chat && !voice.resumed && variant !== "panel" && <p className="voice-turn-note">Continuing “{voice.chat.title}”</p>}
  <ol className={latest ? "voice-transcript voice-latest" : "voice-transcript"} aria-label="Conversation">
    {shown.map(turn => latest && turn.state === "queued"
      ? <li key={turn.id} className="voice-turn voice-turn-next" data-state="queued">
        <p className="voice-turn-status"><span>Next: {spokenQuestion(turn)}</span>
          {voice.connected && <Button variant="ghost" size="icon-xs" aria-label="Remove waiting question" title="Remove" onClick={() => actions.withdraw(turn.id)}><X/></Button>}</p>
      </li>
      : <li key={turn.id} className="voice-turn" data-state={turn.state}>
        <p className="voice-user">{spokenQuestion(turn)}</p>
        {turn.answer && <p className="voice-answer">{turn.answer}</p>}
        {!turn.answer && turn.state === "done" && <p className="voice-answer">…</p>}
        <TurnStatus turn={turn} voice={voice} last={turn.id === lastDone} actions={actions}/>
      </li>)}
  </ol></>;
}

/** Earlier chats from Chat history, newest first; choosing one carries it on (ADR 0146). */
function EarlierChats({ compact, onChoose }: { compact: boolean; onChoose: (chat: CarriedChat) => void }) {
  const [open, setOpen] = useState(false);
  const [chats, setChats] = useState<ChatSession[] | null>(null);
  const [failed, setFailed] = useState(false);
  function toggle(next: boolean) {
    setOpen(next);
    if (!next) return;
    setFailed(false);
    void getChatSessions().then(({ sessions }) => setChats((sessions ?? []).slice(0, 8))).catch(() => { setChats([]); setFailed(true); });
  }
  return <Popover open={open} onOpenChange={toggle}>
    <PopoverTrigger render={<Button variant="ghost" size={compact ? "icon-sm" : "lg"} aria-label="Continue an earlier chat" title="Continue an earlier chat"/>}>
      <History size={compact ? 15 : 17}/>{!compact && " Earlier chats"}</PopoverTrigger>
    <PopoverContent align="center" className={`voice-options voice-chats ${compact ? "voice-options-compact" : ""}`} aria-label="Earlier chats">
      <PopoverTitle>Continue an earlier chat</PopoverTitle>
      {chats === null ? <p className="voice-chats-note">Loading…</p>
        : failed ? <p className="voice-chats-note">Chat history couldn&apos;t be loaded.</p>
        : !chats.length ? <p className="voice-chats-note">No chats yet. Conversations with Echo and chats on the Chat page appear here.</p>
        : <ul>{chats.map(chat => <li key={chat.id}>
          <button type="button" onClick={() => { setOpen(false); onChoose({ id: chat.id, title: chat.title }); }}>
            {chat.kind === "voice" ? <AudioLines size={14} role="img" aria-label="Echo conversation"/> : <MessageSquare size={14} role="img" aria-label="Chat"/>}
            <span>{chat.title}</span>
          </button></li>)}</ul>}
    </PopoverContent>
  </Popover>;
}

export function VoiceSurface({ compact = false }: { compact?: boolean }) {
  const [voice, setVoice] = useState<VoiceSnapshot>({ ...initialVoice });
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [engine, setEngine] = useState<"chat" | "lfm">("chat");
  const [text, setText] = useState("");
  const [pinned, setPinned] = useState(false);
  // Docked by the orb while the dashboard is hidden; then Echo can fold into it (ADR 0135).
  const [docked, setDocked] = useState(false);
  const [desktop, setDesktop] = useState(false);
  const [shortcutAvailable, setShortcutAvailable] = useState(true);
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [rememberMicrophone, setRememberMicrophone] = useState(false);
  const [inputDevice, setInputDevice] = useState("");
  const [outputDevice, setOutputDevice] = useState("");
  // Echo's look (the voice.avatar setting); null until it is known, so the
  // default never flashes before a different choice loads.
  const [avatar, setAvatar] = useState<string | null>(null);
  const [looks, setLooks] = useState<ConfigChoice[]>([]);
  const setup = useVoiceSetup();
  // "Hey Echo" (ADR 0137): the desktop app's choice and state, and Turn on's steps.
  const wake = useWakeState();
  const [wakeSheet, setWakeSheet] = useState(false);
  const [wakeBattery, setWakeBattery] = useState(false);
  const [wakeBusy, setWakeBusy] = useState<null | "consent" | "download" | "saving">(null);
  const [wakeProgress, setWakeProgress] = useState(0);
  const [wakeError, setWakeError] = useState("");
  const [idleSeconds, setIdleSeconds] = useState(15);
  // The voice.save_conversations setting (ADR 0146); null until it is known.
  const [saveChats, setSaveChats] = useState<boolean | null>(null);
  // A chat the Chat page sent here to carry on (/voice/?chat=…); it starts on the user's click.
  const [chosenChat, setChosenChat] = useState<CarriedChat | null>(null);
  const engineRef = useRef<"chat" | "lfm">("chat");
  const idleRef = useRef(15);
  const wakeId = useRef<number | null>(null);
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
    client.current.inputDevice = preferences.inputDevice;
    client.current.outputDevice = preferences.outputDevice;
    // Read browser storage and host capabilities only after hydration.
    const timer = setTimeout(() => {
      setDesktop(!!window.electronAPI?.openCompanion);
      setEngine(preferences.engine); setRememberMicrophone(preferences.microphoneEnabled);
      setInputDevice(preferences.inputDevice); setOutputDevice(preferences.outputDevice);
      // A reply that went on without this window is picked up, or shown if it finished (ADR 0145).
      engineRef.current = preferences.engine;
      if (!document.hidden) void client.current?.restore(preferences.engine);
    }, 0);
    // The main process owns the window, so pinning shows the state it reports.
    function companion(state: { pinned: boolean; docked?: boolean; shortcutAvailable?: boolean } | null) {
      if (state) { setPinned(state.pinned); setDocked(!!state.docked); setShortcutAvailable(state.shortcutAvailable !== false); }
    }
    const unsubscribe = window.electronAPI?.onCompanionState?.(companion);
    void window.electronAPI?.getCompanionState?.().then(companion);
    const controller = new AbortController();
    void fetch(`${API_BASE}/api/voice/status`, { signal: controller.signal }).then(r => r.ok ? r.json() : null).then(setRuntime).catch(() => {});
    void getConfig().then(config => {
      const setting = config.settings?.find(s => s.key === "voice.avatar");
      setLooks(setting?.choices ?? []);
      setAvatar(String(setting?.value || "echo"));
      const saving = config.settings?.find(s => s.key === "voice.save_conversations");
      setSaveChats(saving ? saving.value !== false : true);
    }).catch(() => { setAvatar("echo"); setSaveChats(true); });
    const requested = compact ? null : new URLSearchParams(location.search).get("chat");
    if (requested) void getChatSession(requested).then(chat => { if (chat?.id) setChosenChat({ id: chat.id, title: chat.title }); }).catch(() => {});
    // In the floating Echo, "Hey Echo" starts a conversation and hands over its
    // question; when that conversation ends by itself, Echo goes back where it was.
    const voiceClient = client.current;
    const started = new Set<number>(), asked = new Set<number>();
    function woken(event: { type: "start"; id: number } | { type: "question"; id: number; text: string }) {
      if (event.type === "start" && !started.has(event.id)) {
        started.add(event.id); wakeId.current = event.id;
        voiceClient.wakeUp(engineRef.current, idleRef.current);
      } else if (event.type === "question" && !asked.has(event.id)) {
        asked.add(event.id);
        if (event.text.trim()) voiceClient.ask(event.text, engineRef.current);
      }
    }
    voiceClient.onWakeEnd = () => {
      if (wakeId.current !== null) window.electronAPI?.wakeDone?.(wakeId.current, true);
      wakeId.current = null;
    };
    const unsubscribeWake = compact ? window.electronAPI?.onWake?.(woken) : undefined;
    if (compact && window.electronAPI?.getWakeSession) {
      void wakeStatus().then(status => { idleRef.current = status.idle_seconds; setIdleSeconds(status.idle_seconds); }).catch(() => {})
        .then(() => window.electronAPI?.getWakeSession?.()).then(session => {
          if (!session) return;
          woken({ type: "start", id: session.id });
          if (session.text !== null) woken({ type: "question", id: session.id, text: session.text });
        }).catch(() => {});
    }
    // Hidden, the page lets go of the microphone and the conversation; a reply
    // in progress goes on in the API, and showing the page picks it up (ADR 0145).
    function hidden() { if (document.hidden) client.current?.detach(); else void client.current?.restore(engineRef.current); }
    function blur() { client.current?.release(false); }
    function transfer() { if (!compact) client.current?.detach(); }
    document.addEventListener("visibilitychange", hidden);
    window.addEventListener("blur", blur);
    window.addEventListener("imprint:stop-dashboard-voice", transfer);
    return () => { clearTimeout(timer); controller.abort(); unsubscribe?.(); unsubscribeWake?.(); client.current?.detach(); document.removeEventListener("visibilitychange", hidden); window.removeEventListener("blur", blur); window.removeEventListener("imprint:stop-dashboard-voice", transfer); };
  }, [compact]);
  useEffect(() => { engineRef.current = engine; }, [engine]);
  // While this page holds the microphone, the desktop app's wake listener pauses (ADR 0137).
  useEffect(() => {
    const api = window.electronAPI, on = voice.microphone;
    api?.reportMicrophone?.(on);
    return () => { if (on) api?.reportMicrophone?.(false); };
  }, [voice.microphone]);
  const energy = () => client.current?.level || 0;
  // Listed while Companion settings is open; names become readable once the microphone is allowed.
  const devices = useAudioDevices(optionsOpen, voice.microphone);
  const ready = setup.state ? setup.state.installed && setup.state.supported && !setup.state.busy
    && !["error", "blocked", "cancelled"].includes(setup.state.stage) : runtime?.installed && runtime.supported;
  const active = voice.connected || voice.phase === "connecting";
  // Echo is answering, has questions waiting, or is reading an answer aloud.
  const busy = voice.connected && (replying(voice) || voice.phase === "speaking");
  const actions: TurnActions = { retry: id => client.current?.retry(id), replay: id => client.current?.replay(id),
    withdraw: id => client.current?.withdraw(id) };
  /** Turn on "Hey Echo": after the explanation, the OS consent step (ADR 0069), then
   * the one-time model download, then the desktop app starts listening (ADR 0137). */
  async function turnOnWake() {
    const api = window.electronAPI, platform = api?.platform;
    setWakeError(""); setWakeBusy("consent");
    try {
      const access = await api?.requestMicrophoneAccess?.();
      if (access && !microphoneAccessAllows(access.status, platform)) {
        setWakeError(microphoneProblem("permission", new MicrophoneAccessError(access.status), platform));
        return;
      }
      setWakeBusy("download");
      let status = await wakeStatus();
      if (!status.installed) {
        status = await downloadWakeModel();
        while (!status.installed) {
          const { stage, busy, downloaded, total, problem } = status.setup;
          setWakeProgress(total ? Math.round((100 * downloaded) / total) : 0);
          if (!busy && (stage === "error" || stage === "cancelled")) {
            if (stage === "error") setWakeError(problem === "disk" ? "Not enough free space for the 78 MB speech model."
              : "The speech model could not be downloaded. Check your connection and try again.");
            return;
          }
          await new Promise(resolve => setTimeout(resolve, 400));
          status = await wakeStatus();
        }
      }
      idleRef.current = status.idle_seconds; setIdleSeconds(status.idle_seconds);
      setWakeBusy("saving");
      await api?.setWake?.({ enabled: true, battery: wakeBattery ? "pause" : "listen" });
      setWakeSheet(false);
    } catch (error) {
      setWakeError(error instanceof Error ? error.message : "“Hey Echo” could not be turned on.");
    } finally { setWakeBusy(null); }
  }
  function closeWakeSheet() {
    if (wakeBusy === "download") void cancelWakeDownload().catch(() => {});
    setWakeSheet(false);
  }
  /** Any click or key in Echo during a wake-started conversation keeps it open. */
  function takeOver() {
    if (!voice.wake || wakeId.current === null) return;
    client.current?.keepWake();
    window.electronAPI?.wakeDone?.(wakeId.current, false);
    wakeId.current = null;
  }
  // A session keeps the voice and reasoning model it started with (ADR 0071),
  // so changing either reconnects; the API finishes the reply in progress
  // first, then starts the new conversation (ADR 0145).
  async function chooseLook(next: string) {
    const previous = avatar;
    setAvatar(next);
    try { await setConfigValue("voice.avatar", next); } catch { setAvatar(previous); return; }
    if (active) void client.current?.start(engine);
  }
  const picker = looks.length > 1 && avatar
    ? (compactPicker: boolean) => <AvatarPicker choices={looks} value={avatar} onChange={look => void chooseLook(look)} compact={compactPicker}/>
    : null;
  function chooseMode(value: VoiceMode) { client.current?.setMode(value); saveVoicePreferences({ mode: value }); }
  /** Start a conversation, or carry on a chat from Chat history (ADR 0146). */
  function begin(chat?: CarriedChat | null) {
    setChosenChat(null);
    if (!compact && new URLSearchParams(location.search).has("chat")) history.replaceState(null, "", location.pathname);
    void client.current?.start(engine, chat ? { chat: chat.id } : {});
  }
  /** Saving in Chat history: off stops the conversation in progress at once, on saves the next one. */
  async function chooseSaving(on: boolean) {
    const previous = saveChats;
    setSaveChats(on);
    try { await setConfigValue("voice.save_conversations", on); } catch { setSaveChats(previous); }
  }
  // Whether what is said now ends up in Chat history.
  const saved = saveChats !== false && (!(voice.connected || voice.resumable) || voice.saving);
  function chooseEngine(value: "chat" | "lfm") {
    setEngine(value); saveVoicePreferences({ engine: value });
    if (active) void client.current?.start(value);
  }
  /** The remembered microphone choice (ADR 0069): later conversations arm the
   * microphone when they are ready; clearing it turns capture off now. */
  function remember(value: boolean) {
    setRememberMicrophone(value); saveVoicePreferences({ microphoneEnabled: value });
    client.current?.setMicrophonePreference(value);
  }
  function chooseInput(id: string) {
    setInputDevice(id); saveVoicePreferences({ inputDevice: id });
    void client.current?.setInputDevice(id);
  }
  function chooseOutput(id: string) {
    setOutputDevice(id); saveVoicePreferences({ outputDevice: id });
    void client.current?.setOutputDevice(id);
  }
  /** A remembered device that is not plugged in stays listed, so the choice is visible and kept. */
  const deviceOptions = (found: AudioDevice[], chosen: string, systemDefault: string) => <>
    <option value="">{systemDefault ? `System default (${systemDefault})` : "System default"}</option>
    {found.map(device => <option key={device.id} value={device.id}>{device.label}</option>)}
    {chosen && !found.some(device => device.id === chosen) && <option value={chosen}>Not connected · uses the default</option>}
  </>;
  // The full page shows these under its controls; the companion keeps them in its settings panel.
  const textForm = <form className="voice-text" onSubmit={event => { event.preventDefault(); if (text.trim()) { client.current?.ask(text, engine); setText(""); input.current?.focus(); } }}>
    <input ref={input} aria-label="Type a question" placeholder="Or type a thought…" value={text} maxLength={2000} onChange={event => setText(event.target.value)}/>
    <Button type="submit" variant="ghost" size="icon-sm" aria-label="Send question" disabled={!text.trim()}><ArrowUpRight size={17}/></Button>
  </form>;
  const transcript = <Turns voice={voice} actions={actions} variant={compact ? "panel" : "full"}/>;
  function popout() {
    client.current?.detach();
    if (window.electronAPI?.openCompanion) window.electronAPI.openCompanion();
    else window.open("/companion/", "imprint-echo", "popup,width=380,height=560");
  }
  return <section className={`voice-surface ${compact ? "voice-compact" : ""} ${!ready || voice.preparation ? "voice-needs-setup" : ""}`} data-phase={voice.phase}
    onPointerDownCapture={takeOver} onKeyDownCapture={takeOver}
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
            {compact && picker && <div className="voice-look"><span>Echo&apos;s look and voice</span>{picker(true)}</div>}
            <div className="voice-settings">
              <label>Voice reasoning <select aria-label="Voice reasoning model" value={engine} onChange={event => chooseEngine(event.target.value as "chat" | "lfm")}>
                <option value="chat" disabled={runtime?.chat_provider !== "local"}>Same local model as Chat</option><option value="lfm">Small voice model · LFM 1.5B</option>
              </select></label>
              <label>Microphone <select aria-label="Microphone" value={inputDevice} onChange={event => chooseInput(event.target.value)}>
                {deviceOptions(devices.inputs, inputDevice, devices.defaultInput)}
              </select></label>
              {devices.outputSupported && <label>Speaker <select aria-label="Speaker" value={outputDevice} onChange={event => chooseOutput(event.target.value)}>
                {deviceOptions(devices.outputs, outputDevice, devices.defaultOutput)}
              </select></label>}
              {!devices.labelled && <small>Device names appear once the microphone is allowed.</small>}
              <label className="voice-check"><Checkbox aria-label="Use microphone for conversations" checked={rememberMicrophone} onCheckedChange={remember}/>Use microphone for conversations</label>
              <small>Remembered on this device. Starting a conversation turns the microphone on, after the system allows it; push-to-talk stays silent until you hold it.</small>
              {voice.mode === "handsfree" && <label>Speech threshold <input type="range" aria-label="Speech detection threshold" min="8" max="60" defaultValue="16" onChange={event => { if (client.current) client.current.sensitivity = +event.target.value / 1000; }}/><small>Raise if room noise interrupts</small></label>}
              <label className="voice-check voice-wake-switch"><Switch aria-label="Listen for “Hey Echo”" checked={!!wake?.enabled} disabled={!desktop || !ready || wakeBusy !== null}
                onCheckedChange={on => { if (!on) { void window.electronAPI?.setWake?.({ enabled: false }); return; } setOptionsOpen(false); setWakeError(""); setWakeSheet(true); }}/>Listen for “Hey Echo”</label>
              <small className="voice-wake-status" role="status">{!desktop ? "“Hey Echo” needs the Imprint desktop app." : !ready ? "Set up Echo first."
                : wake?.enabled ? wakeSummary(wake) : "Off. Turn it on to wake Echo by voice."}</small>
              {desktop && wake?.enabled && <div className="voice-option-actions">
                {wake.reason === "paused" ? <Button variant="ghost" onClick={() => window.electronAPI?.controlWake?.("resume")}>Resume listening</Button>
                  : wake.listening && <Button variant="ghost" onClick={() => window.electronAPI?.controlWake?.("pause")}>Pause for 1 hour</Button>}
                {wake.reason === "blocked" && <Button variant="ghost" onClick={() => void window.electronAPI?.openMicrophoneSettings?.()}>Open microphone settings</Button>}
              </div>}
              <label className="voice-check"><Switch aria-label="Save conversations in Chat history" checked={saveChats !== false} disabled={saveChats === null}
                onCheckedChange={on => void chooseSaving(on)}/>Save conversations in Chat history</label>
              <small>{saveChats === false ? "Off. Echo forgets a conversation once you end it or leave it for 30 minutes."
                : "As text on this device, where Chat can open and delete them; audio is never saved. Turning this off stops saving at once."}</small>
            </div>
            {compact && ready && textForm}{compact && ready && transcript}
            {compact && <div className="voice-option-actions">
              {(active || voice.resumable) && <Button variant="ghost" onClick={() => client.current?.stop()}><MicOff size={14}/> End conversation</Button>}
              {desktop && <Button variant="ghost" aria-label={pinned ? "Unpin companion" : "Keep companion on top"} aria-pressed={pinned} className="voice-pin" onClick={() => window.electronAPI?.pinCompanion(!pinned)}><Pin size={14} fill={pinned ? "currentColor" : "none"}/> {pinned ? "On top" : "Keep on top"}</Button>}
              <Button variant="ghost" aria-label="Close companion" onClick={() => { client.current?.detach(); if (window.electronAPI?.closeCompanion) window.electronAPI.closeCompanion(); else window.close(); }}><X size={14}/> Close</Button>
            </div>}
            {compact && desktop && <small className="voice-hint">Drag Echo to move it; with the dashboard hidden, Echo and the Imprint button move together, and Echo can fold into the button. {shortcutAvailable ? `${shortcut} recalls Echo.` : "Recall Echo from the Imprint tray menu."}</small>}
          </PopoverContent>
        </Popover>
        {compact && desktop && docked && <Button variant="ghost" size="icon-sm" aria-label="Collapse Echo into the Imprint button" title="Collapse into the Imprint button"
          onClick={() => { client.current?.detach(); window.electronAPI?.collapseCompanion?.(); }}><ArrowDownToDot size={16}/></Button>}
      </div>
    </header>
    {!compact && <div className="voice-intro"><span className="voice-eyebrow">A LITTLE PRESENCE. A FAMILIAR VOICE.</span><h1>Your thoughts,<br/> <em>in conversation.</em></h1><p>A quiet companion for your local Imprint memory.</p></div>}
    {!ready && !compact && <VoiceSetup setup={setup}/>}
    {!ready && !compact && picker && <div className="echo-look"><p className="echo-setup-eyebrow">CHOOSE HOW ECHO LOOKS AND SOUNDS</p>{picker(false)}</div>}
    {!compact && <VoiceChatSetup preparation={voice.preparation}/>}
    <div className="voice-stage">
      <div className="voice-halo" aria-hidden="true"/>
      {avatar && <EchoAvatar phase={voice.phase} energy={energy} model={avatar} compact={compact}/>}
      <div className="voice-caption"><span className={`voice-dot ${active ? "active" : ""}`}/><span>{voice.phase === "idle" ? "ECHO · LOCAL VOICE COMPANION" : voice.detail}</span></div>
      {voice.wake && <p className="voice-wake-caption" role="status">Heard “Hey Echo” · ends after {idleSeconds} s of quiet or when you say “stop”</p>}
    </div>
    <div className="voice-control-area">
      {!ready ? compact && <VoiceSetup setup={setup} compact/> : <>
      <div className="voice-modes" role="group" aria-label="Microphone mode">
        <Button variant="ghost" aria-pressed={voice.mode === "ptt"} onClick={() => chooseMode("ptt")}>Push to talk</Button>
        <Button variant="ghost" aria-pressed={voice.mode === "handsfree"} onClick={() => chooseMode("handsfree")}>Hands-free</Button>
      </div>
      <div className="voice-primary-controls">
        {!active ? <>
            {chosenChat
              ? <Button className="voice-start" disabled={!ready} onClick={() => begin(chosenChat)}><AudioLines size={18}/> <span className="voice-start-label">Continue “{chosenChat.title}”</span></Button>
              : <Button className="voice-start" disabled={!ready} onClick={() => begin()}><AudioLines size={18}/> {voice.resumable ? "Continue conversation" : "Start conversation"}</Button>}
            {chosenChat && <Button variant="ghost" size={compact ? "icon-sm" : "icon-lg"} aria-label="Start a new conversation instead" title="Start a new conversation instead" onClick={() => setChosenChat(null)}><X size={16}/></Button>}
            {!chosenChat && <EarlierChats compact={compact} onChoose={begin}/>}
            {voice.resumable && !compact && !chosenChat && <Button variant="ghost" size="icon-lg" aria-label="End conversation" title="End conversation" onClick={() => client.current?.stop()}><MicOff size={18}/></Button>}
          </>
          : voice.phase === "connecting" ? <Button className="voice-start" onClick={() => client.current?.detach()}>Connecting… · Cancel</Button>
          : <>
            {!voice.microphone ? <Button className="voice-start" onClick={() => void client.current?.enableMicrophone().then(on => { if (on) remember(true); })}><Mic size={18}/> Enable microphone</Button>
              : voice.mode === "ptt" ? <Button className={`voice-start voice-hold ${voice.held ? "held" : ""}`} aria-label="Hold to talk" aria-pressed={voice.held}
                onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); client.current?.hold(); }}
                onPointerUp={() => client.current?.release()} onPointerCancel={() => client.current?.release(false)}
                onLostPointerCapture={() => client.current?.release(false)}
                onKeyDown={event => { if ((event.key === " " || event.key === "Enter") && !event.repeat) { event.preventDefault(); client.current?.hold(); } }}
                onKeyUp={event => { if (event.key === " " || event.key === "Enter") { event.preventDefault(); client.current?.release(); } }}
                onBlur={() => client.current?.release(false)}><Mic size={18}/>{voice.held ? "Release to send" : "Hold to talk"}</Button>
              : <span className="voice-start voice-listening"><Mic size={18}/> {voice.phase === "listening" ? "Hearing you" : "Listening"}</span>}
            {/* Stopping is the one way to cancel a reply, so it sits beside the talk button (ADR 0145). */}
            {busy && <Button variant="outline" size={compact ? "icon-sm" : "lg"} className="voice-stop" aria-label="Stop reply" title="Stop reply"
              onClick={() => client.current?.stopReply()}><Square size={compact ? 13 : 15} fill="currentColor"/>{!compact && " Stop reply"}</Button>}
            {!compact && <Button variant="ghost" size="icon-lg" aria-label="End conversation" title="End conversation" onClick={() => client.current?.stop()}><MicOff size={18}/></Button>}
          </>}
      </div>
      {compact && <VoiceChatSetup preparation={voice.preparation} compact/>}
      {compact && <Turns voice={voice} actions={actions} variant="latest"/>}
      <p className="voice-privacy" role="status" data-alert={voice.phase === "error" || !!voice.microphoneError || undefined}>{voice.phase === "error" ? voice.detail
        : voice.microphoneError || (voice.elsewhere && !active ? "Echo is open in another window."
        : voice.microphone ? voice.mode === "handsfree" ? "Mic on · speak anytime; say “stop” to stop a reply" : "Mic armed · hold button or focused Space key"
        : `Microphone off · English · runs locally · ${saved ? "saved in Chat" : "not saved"}`)}</p>
      {voice.microphoneSettings && desktop && <Button variant="outline" className="voice-settings-link" onClick={() => void window.electronAPI?.openMicrophoneSettings?.()}>Open microphone settings</Button>}
      {!compact && textForm}{!compact && transcript}
      {!compact && picker && <div className="voice-look"><span>Echo&apos;s look and voice</span>{picker(true)}</div>}
      </>}
    </div>
    {!compact && <footer className="voice-footer"><span>ECHO / LOCAL COMPANION</span><a href={`/models/imprint-${avatar || "echo"}.glb`} download><Download size={13}/> Download 3D model</a></footer>}
    <Dialog open={wakeSheet} onOpenChange={open => { if (!open) closeWakeSheet(); }}>
      <DialogContent className="voice-wake-sheet">
        <DialogHeader>
          <DialogTitle>Listen for “Hey Echo”?</DialogTitle>
          <DialogDescription>Say “Hey Echo” and your question, and Echo answers without a click.</DialogDescription>
        </DialogHeader>
        <ul className="voice-wake-points">
          <li>The microphone stays on while the Imprint app runs. The Imprint button shows a dot, and your system shows its microphone indicator, the whole time.</li>
          <li>When you speak, the first two seconds are checked on this computer by a small speech model, then discarded. Nothing is stored or sent anywhere.</li>
          <li>After “Hey Echo”, the rest of what you say becomes your question. Echo listens hands-free until {idleSeconds} seconds of quiet or until you say “stop”. Like any conversation with Echo, it is saved as text in Chat history unless you turn that off in Companion settings.</li>
          <li>Listening pauses during recordings, Echo conversations, screen lock and sleep. Pause it or turn it off from the button, the tray or the top bar.</li>
          <li>Turning it on downloads a 78 MB English speech model once.</li>
        </ul>
        <label className="voice-check voice-wake-battery"><Checkbox aria-label="Pause while on battery" checked={wakeBattery} onCheckedChange={value => setWakeBattery(value === true)}/>Pause while on battery</label>
        {wakeBusy === "download" && <div className="voice-wake-progress" role="status">Downloading the speech model… {wakeProgress}%<progress max={100} value={wakeProgress}/></div>}
        {wakeError && <p className="voice-wake-error" role="alert">{wakeError}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={closeWakeSheet}>Cancel</Button>
          <Button onClick={() => void turnOnWake()} disabled={wakeBusy !== null}>
            {wakeBusy === "consent" ? "Asking the system…" : wakeBusy === "download" ? "Downloading…" : wakeBusy === "saving" ? "Turning on…" : "Turn on"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </section>;
}
