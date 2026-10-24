"use client";

import { useRef } from "react";
import { LogoMark } from "@/components/logo-mark";
import "@/styles/launcher.css";

export default function Launcher() {
  const start = useRef<{ x: number; y: number } | null>(null);
  const moved = useRef(false);
  return <div className="imprint-launcher"><button aria-label="Open Imprint" title="Open Imprint · drag to move · right-click for Echo"
    onPointerDown={event => {
      if (event.button !== 0) return;
      moved.current = false; start.current = { x: event.screenX, y: event.screenY };
      event.currentTarget.setPointerCapture(event.pointerId);
      window.electronAPI?.beginLauncherDrag?.(start.current);
    }}
    onPointerMove={event => {
      if (!start.current) return;
      const point = { x: event.screenX, y: event.screenY };
      if (Math.hypot(point.x - start.current.x, point.y - start.current.y) > 4) moved.current = true;
      if (moved.current) window.electronAPI?.moveLauncherDrag?.(point);
    }}
    onPointerUp={event => { start.current = null; window.electronAPI?.endLauncherDrag?.();
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
    onPointerCancel={() => { start.current = null; window.electronAPI?.endLauncherDrag?.(); }}
    onLostPointerCapture={() => { start.current = null; window.electronAPI?.endLauncherDrag?.(); }}
    onClick={event => { if (event.detail === 0 || !moved.current) window.electronAPI?.openDashboard?.(); }}
    onContextMenu={event => { event.preventDefault(); window.electronAPI?.launcherMenu?.(); }}><LogoMark className="size-7"/></button></div>;
}
