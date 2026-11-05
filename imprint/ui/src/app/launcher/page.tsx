"use client";

import { useEffect, useRef, useState } from "react";
import { LogoMark } from "@/components/logo-mark";
import { clock } from "@/components/recording-indicator";
import { spokenDuration, useLauncherRecorder, useNow, type LauncherState } from "@/lib/launcher";
import "@/styles/launcher.css";

const RIM = 2 * Math.PI * 28; // the status rim's circumference in the 72 px window

/** The orb: Imprint's handle on the desktop while the dashboard is hidden
 * (ADR 0135). A click opens its ring of actions, a double-click the dashboard,
 * a drag moves it with everything docked to it, and its rim shows a recording,
 * the running job and where Echo is docked. */
export default function Launcher() {
  const host = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<LauncherState | null>(null);
  const [seenError, setSeenError] = useState("");
  const [announcement, setAnnouncement] = useState("");
  // Say what changed, once, for screen readers.
  const { recorder, levels } = useLauncherRecorder((next, previous) => {
    if (next.phase === previous.phase) return;
    if (next.phase === "recording") setAnnouncement("Recording started");
    else if (next.phase === "stopping") setAnnouncement("Stopping, saving the last audio");
    else if (next.phase === "idle" && previous.phase === "stopping") setAnnouncement(next.error ? `Recording stopped: ${next.error}` : "Saved, transcription queued");
  });
  const recording = recorder.phase === "recording";
  const now = useNow(recording);
  // The glow follows the louder side; its CSS transition smooths the 10 Hz samples.
  const level = Math.max(...levels);
  const error = recorder.phase === "idle" ? recorder.error : "";
  const currentError = useRef(error);
  useEffect(() => { currentError.current = error; });
  const start = useRef<{ x: number; y: number; touch: boolean } | null>(null);
  const moved = useRef(false);
  const held = useRef<ReturnType<typeof setTimeout> | null>(null);
  const menuShown = useRef(false);

  useEffect(() => { if (host.current) host.current.dataset.ready = "true"; }, []);
  useEffect(() => {
    const api = window.electronAPI;
    let alive = true, echo: boolean | null = null;
    const update = (next: LauncherState) => {
      if (!alive) return;
      if (echo !== null && echo !== next.echo) setAnnouncement(next.echo ? "Echo opened, microphone off" : "Echo collapsed, microphone off");
      echo = next.echo;
      // An error is acknowledged once the user opens the ring after it.
      if (next.ring && currentError.current) setSeenError(currentError.current);
      setState(next);
    };
    const unsubscribe = api?.onLauncherState?.(update);
    void api?.getLauncherState?.().then((next) => { if (next) update(next); }).catch(() => {});
    return () => { alive = false; unsubscribe?.(); };
  }, []);
  const warning = !!error && error !== seenError;

  const elapsed = recording ? (now - recorder.startedAt) / 1000 : 0;
  const activity = state?.activity ?? null;
  const busy = recorder.phase === "starting" || recorder.phase === "stopping";
  const rim = recording ? "recording" : warning ? "warning" : busy || (activity && activity.percent === null) ? "spinning" : activity ? "progress" : "";
  // The name changes once a minute, so a screen reader is not interrupted every second.
  const minutes = Math.floor(elapsed / 60);
  const name = recording ? `Imprint. Recording, ${minutes ? spokenDuration(minutes * 60).replace(/ 0 seconds$/, "") : "under a minute"}`
    : warning ? "Imprint. Recording stopped, open for details"
    : activity ? `Imprint. ${activity.text}${activity.percent === null ? "" : `, ${activity.percent} percent`}` : "Imprint";
  const tooltip = recording ? `Recording ${clock(elapsed)} · click to stop or see levels`
    : warning ? `Recording stopped: ${error}`
    : `Imprint${activity ? ` · ${activity.text}` : ""} · click for actions · double-click for the dashboard · drag to move`;

  function endPress() {
    start.current = null;
    if (held.current) { clearTimeout(held.current); held.current = null; }
    window.electronAPI?.endLauncherDrag?.();
  }

  return (
    <div ref={host} className="imprint-launcher" data-rim={rim || undefined} data-ring={state?.ring || undefined}
      style={{ "--level": level.toFixed(2) } as React.CSSProperties}>
      <svg className="orb-rim" viewBox="0 0 72 72" aria-hidden="true">
        {rim === "recording" && <circle className="orb-rim-recording" cx="36" cy="36" r="28" />}
        {rim === "warning" && <circle className="orb-rim-warning" cx="36" cy="36" r="28" />}
        {rim === "progress" && activity?.percent !== null && activity && (
          <circle className="orb-rim-progress" cx="36" cy="36" r="28" strokeDasharray={`${(RIM * activity.percent) / 100} ${RIM}`} transform="rotate(-90 36 36)" />
        )}
        {rim === "spinning" && <circle className="orb-rim-spinning" cx="36" cy="36" r="28" strokeDasharray={`${RIM * 0.105} ${RIM}`} />}
        {state?.echo && state.echoSide !== 0 && (
          // A short arc on the side Echo is docked on.
          <circle className="orb-rim-notch" cx="36" cy="36" r="28" strokeDasharray={`${RIM / 10} ${RIM}`}
            transform={`rotate(${state.echoSide > 0 ? -18 : 162} 36 36)`} />
        )}
      </svg>
      <button type="button" className="orb" aria-label={name} title={tooltip} aria-haspopup="menu" aria-expanded={!!state?.ring}
        aria-describedby="orb-help"
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          moved.current = false; menuShown.current = false;
          start.current = { x: event.screenX, y: event.screenY, touch: event.pointerType !== "mouse" };
          event.currentTarget.setPointerCapture(event.pointerId);
          window.electronAPI?.beginLauncherDrag?.({ x: event.screenX, y: event.screenY });
          // Press and hold (touch and pen) opens the menu, as right-click does.
          if (start.current.touch) held.current = setTimeout(() => {
            held.current = null;
            if (!moved.current) { menuShown.current = true; window.electronAPI?.launcherMenu?.(); }
          }, 500);
        }}
        onPointerMove={(event) => {
          if (!start.current) return;
          const point = { x: event.screenX, y: event.screenY };
          if (Math.hypot(point.x - start.current.x, point.y - start.current.y) > (start.current.touch ? 8 : 4)) {
            moved.current = true;
            if (held.current) { clearTimeout(held.current); held.current = null; }
          }
          if (moved.current) window.electronAPI?.moveLauncherDrag?.(point);
        }}
        onPointerUp={(event) => {
          endPress();
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerCancel={endPress}
        onLostPointerCapture={endPress}
        onClick={(event) => {
          if (event.detail !== 0 && (moved.current || menuShown.current)) return;
          // The first click opened the ring; the second of a double-click opens the dashboard.
          if (event.detail >= 2) window.electronAPI?.openDashboard?.();
          else window.electronAPI?.toggleLauncherRing?.(event.detail === 0 ? "first" : null);
        }}
        onKeyDown={(event) => {
          const arrows: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
          const arrow = arrows[event.key];
          if (arrow && event.altKey) {
            event.preventDefault();
            const step = event.shiftKey ? 64 : 16;
            window.electronAPI?.nudgeLauncher?.({ x: arrow[0] * step, y: arrow[1] * step });
          } else if (arrow) {
            event.preventDefault();
            window.electronAPI?.toggleLauncherRing?.(event.key === "ArrowUp" || event.key === "ArrowLeft" ? "last" : "first");
          } else if (event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey)) {
            event.preventDefault();
            window.electronAPI?.launcherMenu?.();
          }
        }}
        onContextMenu={(event) => { event.preventDefault(); if (!menuShown.current) window.electronAPI?.launcherMenu?.(); }}>
        {recording ? <span className={`orb-timer ${elapsed >= 3600 ? "orb-timer-long" : ""}`}>{clock(elapsed)}</span> : <LogoMark className="size-7" />}
      </button>
      {warning && <span className="orb-badge" aria-hidden="true">!</span>}
      <span id="orb-help" hidden>Press Enter for actions. Alt and the arrow keys move the button.</span>
      <div role="status" aria-live="polite" className="sr-only">{announcement}</div>
    </div>
  );
}
