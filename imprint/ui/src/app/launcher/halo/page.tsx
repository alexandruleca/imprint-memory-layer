"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowDownToDot, AudioLines, ChevronRight, PanelsTopLeft, Square } from "lucide-react";
import { clock } from "@/components/recording-indicator";
import { getRecordings, getStats } from "@/lib/api";
import { spokenDuration, useLauncherRecorder, useNow, type HaloLayout, type LauncherRecorder } from "@/lib/launcher";
import { readVoicePreferences } from "@/lib/voice-preferences";
import "@/styles/launcher.css";

/** The orb's ring of actions and its recording panels (ADR 0135). The main
 * process places this window beside the orb, toward the side with room, and
 * shows it once the page has drawn what it was sent. */
export default function Halo() {
  const [layout, setLayout] = useState<HaloLayout | null>(null);
  const [closing, setClosing] = useState<HaloLayout | null>(null);
  const { recorder, levels } = useLauncherRecorder();
  const current = useRef<HaloLayout | null>(null);

  useEffect(() => {
    const api = window.electronAPI;
    let alive = true;
    const show = (next: HaloLayout | null) => {
      // A closed layout stays on screen for its closing motion; main then hides the window.
      if (!next) setClosing(current.current);
      else setClosing(null);
      current.current = next;
      setLayout(next);
    };
    const unsubscribe = api?.onHaloLayout?.(show);
    void api?.getHaloLayout?.().then((next) => { if (alive && next && !current.current) show(next); }).catch(() => {});
    return () => { alive = false; unsubscribe?.(); };
  }, []);
  // Drawn: main may show the window now.
  useLayoutEffect(() => { if (layout) window.electronAPI?.haloReady?.(layout.seq); }, [layout?.seq]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); window.electronAPI?.haloAction?.("close"); } };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, []);

  const shown = layout ?? closing;
  return (
    <div className="imprint-halo" data-closing={!layout && closing ? "" : undefined}>
      {shown?.kind === "ring" && <Ring key={shown.seq} layout={shown} recorder={recorder} levels={levels} />}
      {shown?.kind === "record" && <RecordPanel key={shown.seq} layout={shown} recorder={recorder} />}
      {shown?.kind === "live" && <LivePanel key={shown.seq} layout={shown} recorder={recorder} levels={levels} />}
    </div>
  );
}

type Item = { id: "dashboard" | "echo" | "record" | "stop"; label: string; icon: React.ReactNode; on?: boolean; disabled?: boolean };

function Ring({ layout, recorder, levels }: { layout: HaloLayout; recorder: LauncherRecorder; levels: [number, number] }) {
  const recording = recorder.phase === "recording";
  const saving = recorder.phase === "stopping" || recorder.phase === "starting";
  const now = useNow(recording);
  const elapsed = recording ? (now - recorder.startedAt) / 1000 : 0;
  const items: Item[] = [
    { id: "dashboard", label: "Open dashboard", icon: <PanelsTopLeft size={18} /> },
    { id: "echo", label: layout.echo ? "Collapse Echo" : "Show Echo", icon: <AudioLines size={18} />, on: layout.echo },
    recording ? { id: "stop", label: "Stop recording and transcribe", icon: <Square size={14} className="fill-current" /> }
      : { id: "record", label: recorder.phase === "starting" ? "Starting…" : saving ? "Saving…" : "New recording", icon: <span className="halo-record-dot" />, disabled: saving },
  ];
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const capsule = useRef<HTMLButtonElement | null>(null);
  const menu = useRef<HTMLDivElement>(null);
  const [named, setNamed] = useState<number | null>(null);
  const satellites = layout.satellites ?? [];

  // Keyboard users land on the first or last action; a recording's details come first, never Stop.
  useEffect(() => {
    if (layout.focus === "first") (recording ? capsule.current : buttons.current[0])?.focus();
    else if (layout.focus === "last") buttons.current[2]?.focus();
    else menu.current?.focus();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Opened by the pointer, the ring closes after 6 s without it nearby.
  const idle = useRef<ReturnType<typeof setTimeout> | null>(null);
  function stillHere() {
    if (layout.focus) return;
    if (idle.current) clearTimeout(idle.current);
    idle.current = setTimeout(() => window.electronAPI?.haloAction?.("close"), 6000);
  }
  useEffect(() => { stillHere(); return () => { if (idle.current) clearTimeout(idle.current); }; }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function act(item: Item) {
    if (item.disabled) return;
    window.electronAPI?.haloAction?.(item.id);
  }
  function move(event: React.KeyboardEvent) {
    const order = [...(recording ? [capsule.current] : []), ...buttons.current].filter(Boolean) as HTMLElement[];
    const at = order.indexOf(document.activeElement as HTMLElement);
    const next = { ArrowDown: at + 1, ArrowRight: at + 1, ArrowUp: at - 1, ArrowLeft: at - 1, Home: 0, End: order.length - 1 }[event.key]
      ?? (event.key === "Tab" ? at + (event.shiftKey ? -1 : 1) : null);
    if (next === null) return;
    event.preventDefault();
    order[(next + order.length) % order.length]?.focus();
  }

  const { orb, radius = 68, side } = layout;
  const angles = satellites.map((s) => s.angle);
  const arc = (degrees: number) => {
    const radians = (degrees * Math.PI) / 180;
    return `${(orb.x + radius * Math.cos(radians) * side).toFixed(1)} ${(orb.y + radius * Math.sin(radians)).toFixed(1)}`;
  };
  const from = Math.min(...angles) - 14, to = Math.max(...angles) + 14;
  const stopFocused = named === 2 && recording;

  return (
    <div ref={menu} role="menu" aria-label="Imprint actions" tabIndex={-1} className="halo-ring" onKeyDown={move} onPointerMove={stillHere}
      style={{ "--orb-x": `${orb.x}px`, "--orb-y": `${orb.y}px` } as React.CSSProperties}>
      <svg className="halo-orbit" width={layout.bounds.width} height={layout.bounds.height} aria-hidden="true">
        <path d={`M ${arc(from)} A ${radius} ${radius} 0 0 ${side > 0 ? 1 : 0} ${arc(to)}`} />
      </svg>
      {/* The orb's own window sits above this one; this spot only catches a press if it does not. */}
      <button type="button" tabIndex={-1} aria-hidden="true" className="halo-orb-spot" onClick={() => window.electronAPI?.haloAction?.("close")} />
      {items.map((item, index) => {
        const at = satellites[index];
        if (!at) return null;
        return (
          <button key={item.id} ref={(node) => { buttons.current[index] = node; }} type="button" role="menuitem"
            aria-label={item.label} aria-disabled={item.disabled || undefined} data-on={item.on || undefined} data-stop={item.id === "stop" || undefined}
            className="halo-satellite" style={{ left: at.x - 20, top: at.y - 20, "--dx": `${orb.x - at.x}px`, "--dy": `${orb.y - at.y}px`, "--i": index } as React.CSSProperties}
            onPointerEnter={() => setNamed(index)} onPointerLeave={() => setNamed((n) => (n === index ? null : n))}
            onFocus={() => setNamed(index)} onBlur={() => setNamed((n) => (n === index ? null : n))}
            onClick={() => act(item)}>
            {item.icon}
          </button>
        );
      })}
      {satellites.map((at, index) => {
        if (index === 2 && recording) return null;
        if (named !== index) return null;
        return <span key={`label-${index}`} className="halo-label" aria-hidden="true" data-side={side > 0 ? "right" : "left"}
          style={{ left: at.slot.x, top: at.slot.y + (at.slot.height - 26) / 2, width: at.slot.width }}><span>{items[index].label}</span></span>;
      })}
      {recording && satellites[2] && (
        <button ref={capsule} type="button" role="menuitem" className="halo-capsule" data-stop={stopFocused || undefined}
          aria-label={`Recording details, ${spokenDuration(elapsed)}`}
          style={{ left: satellites[2].slot.x, top: satellites[2].slot.y, width: satellites[2].slot.width }}
          onFocus={() => setNamed(null)} onClick={() => window.electronAPI?.haloAction?.("details")}>
          <span className="halo-live-dot" aria-hidden="true" />
          <span className="halo-capsule-time">{stopFocused ? "Stop and transcribe" : clock(elapsed)}</span>
          {!stopFocused && <span className="halo-mini-meters" aria-hidden="true">
            {levels.map((level, i) => <span key={i}><i style={{ width: `${Math.round(level * 100)}%` }} /></span>)}
          </span>}
          <ChevronRight size={14} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}

function usePanelSize(panel: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    const node = panel.current;
    if (!node) return;
    // The layout height: the opening motion scales the panel, which getBoundingClientRect would include.
    const report = () => window.electronAPI?.haloSize?.(node.offsetHeight + 24);
    report();
    const observer = new ResizeObserver(report);
    observer.observe(node);
    return () => observer.disconnect();
  }, [panel]);
}

function Collapse() {
  return (
    <button type="button" className="halo-collapse" aria-label="Collapse into the Imprint button" title="Collapse (Esc)"
      onClick={() => window.electronAPI?.haloAction?.("close")}>
      <ArrowDownToDot size={16} aria-hidden="true" />
    </button>
  );
}

const REMEMBERED = "imprint.launcher.recording";
function remembered(): { project?: string; others?: boolean } {
  try { return JSON.parse(localStorage.getItem(REMEMBERED) || "{}"); } catch { return {}; }
}

function RecordPanel({ layout, recorder }: { layout: HaloLayout; recorder: LauncherRecorder }) {
  const panel = useRef<HTMLElement>(null);
  const consentBox = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState("");
  // The last project and computer-audio choice on this device; consent is never remembered.
  const [last] = useState(remembered);
  const [project, setProject] = useState(last.project ?? "");
  const [projects, setProjects] = useState<string[]>([]);
  const [others, setOthers] = useState(last.others ?? true);
  const [consent, setConsent] = useState(false);
  const [tried, setTried] = useState(false);
  const [placeholder] = useState(() => {
    const d = new Date(), pad = (n: number) => String(n).padStart(2, "0");
    return `Meeting ${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  });
  usePanelSize(panel);

  useEffect(() => {
    let alive = true;
    void getRecordings().then((data) => { if (alive && !last.project) setProject((p) => p || data.default_project); }).catch(() => {});
    void getStats().then((stats) => { if (alive) setProjects(stats.projects.map(([name]) => name).filter(Boolean).sort()); }).catch(() => {});
    // The confirmation is the one required step, so it takes focus.
    consentBox.current?.focus();
    return () => { alive = false; };
  }, [last.project]);

  const starting = recorder.phase === "starting";
  const error = tried && recorder.phase === "idle" ? recorder.error : "";
  function start(withOthers = others) {
    if (!consent || !project.trim() || recorder.phase !== "idle") return;
    try { localStorage.setItem(REMEMBERED, JSON.stringify({ project: project.trim(), others: withOthers })); } catch { /* per-device convenience only */ }
    setTried(true);
    window.electronAPI?.startRecording?.({ title: title.trim(), project: project.trim(), others: withOthers, consent: true });
  }
  const microphone = readVoicePreferences().inputDevice ? "the microphone chosen in Voice settings" : "the default microphone";

  return (
    <div className="halo-panel-host" data-origin={layout.origin}>
      <section ref={panel} role="dialog" aria-label="New recording" className="halo-panel" style={{ transformOrigin: layout.origin }}
        onKeyDown={(event) => { if (event.key === "Enter" && !(event.target instanceof HTMLButtonElement)) { event.preventDefault(); start(); } }}>
        <header><span className="halo-record-dot" aria-hidden="true" /><h1>New recording</h1><Collapse /></header>
        <label className="halo-field">Title
          <input value={title} maxLength={200} placeholder={placeholder} disabled={starting} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="halo-field">Project
          <input value={project} maxLength={256} list="halo-projects" disabled={starting} onChange={(e) => setProject(e.target.value)} />
        </label>
        <datalist id="halo-projects">{projects.map((p) => <option key={p} value={p} />)}</datalist>
        <label className="halo-switch">
          <button type="button" role="switch" aria-checked={others} disabled={starting} onClick={() => setOthers(!others)}><span /></button>
          <span>Include what this computer plays<small>The other people on the call</small></span>
        </label>
        <label className="halo-consent">
          <input ref={consentBox} type="checkbox" checked={consent} disabled={starting} onChange={(e) => setConsent(e.target.checked)} />
          <span><b>Everyone on this call knows it is being recorded.</b> Recording people without their consent is illegal in many places.</span>
        </label>
        {error && (
          <div role="alert" className="halo-error">
            <p>{error}</p>
            <div>
              {others && <button type="button" onClick={() => { setOthers(false); start(false); }}>Record my microphone only</button>}
              {(window.electronAPI?.platform === "darwin" || window.electronAPI?.platform === "win32") && (
                <button type="button" onClick={() => window.electronAPI?.haloAction?.("microphone-settings")}>Microphone settings</button>
              )}
              <button type="button" onClick={() => start()}>Try again</button>
            </div>
          </div>
        )}
        <button type="button" className="halo-start" disabled={!consent || !project.trim() || recorder.phase !== "idle"} onClick={() => start()}>
          <span className="halo-record-dot" aria-hidden="true" /> {starting ? "Starting…" : "Start recording"}
        </button>
        <p className="halo-hint">{consent ? `Records ${microphone}${others ? ", plus this computer's audio" : ""}.` : "Confirm that everyone knows, then start."}</p>
      </section>
    </div>
  );
}

function LivePanel({ layout, recorder, levels }: { layout: HaloLayout; recorder: LauncherRecorder; levels: [number, number] }) {
  const panel = useRef<HTMLElement>(null);
  const recording = recorder.phase === "recording";
  const now = useNow(recording);
  usePanelSize(panel);
  useEffect(() => { panel.current?.querySelector<HTMLElement>(".halo-stop")?.focus(); }, []);
  const elapsed = recording ? (now - recorder.startedAt) / 1000 : 0;
  return (
    <div className="halo-panel-host" data-origin={layout.origin}>
      <section ref={panel} role="dialog" aria-label="Recording" className="halo-panel" style={{ transformOrigin: layout.origin }}>
        <header><span className="halo-live-dot" aria-hidden="true" /><h1>{recorder.title || "Recording"}</h1><Collapse /></header>
        <p className="halo-meta"><span className="tabular-nums">{clock(elapsed)}</span> · {recorder.project}</p>
        <div className="halo-meters">
          {(["You", "Others"] as const).map((who, i) => (
            <div key={who}><span>{who}</span><span className="halo-meter"><i style={{ width: `${Math.round(levels[i] * 100)}%` }} /></span></div>
          ))}
        </div>
        {!recorder.others && <p className="halo-hint">Only your microphone is being recorded.</p>}
        <button type="button" className="halo-stop" disabled={!recording} onClick={() => window.electronAPI?.stopRecording?.()}>
          <Square size={14} className="fill-current" aria-hidden="true" /> {recording ? "Stop and transcribe" : "Saving…"}
        </button>
        <button type="button" className="halo-link" onClick={() => window.electronAPI?.haloAction?.("dashboard")}>Open Recordings in the dashboard</button>
        <p className="halo-hint">Recording continues while this is collapsed.</p>
      </section>
    </div>
  );
}
