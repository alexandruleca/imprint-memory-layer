"use client";

import { useCallback, useEffect, useState } from "react";
import { API_BASE } from "@/lib/api-base";

export type VoiceSetupState = {
  stage: string; busy: boolean; installed: boolean; supported: boolean;
  downloaded: number; total: number; problem: string | null; platform: string;
};
export function formatDownloadBytes(bytes: number) {
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(2)} GB` : `${Math.round(bytes / 1e6)} MB`;
}
export const setupLabels: Record<string, string> = {
  checking: "Checking your computer", verifying: "Verifying saved files", downloading: "Downloading voice models",
  engine: "Downloading the speech engine", preparing: "Preparing the speech engine", building: "Building the speech engine",
  finishing: "Checking everything is ready", cancelling: "Cancelling setup", cancelled: "Setup stopped",
  ready: "Echo is ready", blocked: "One more thing before setup", error: "Setup needs another try", idle: "Meet your local voice companion",
};

export function useVoiceSetup() {
  const [state, setState] = useState<VoiceSetupState | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const response = await fetch(`${API_BASE}/api/voice/setup`, { signal });
    if (!response.ok) throw Error("Couldn't check Echo setup. Try again.");
    const value = await response.json();
    if (typeof value.stage === "string" && typeof value.installed === "boolean") setState(value);
    setError("");
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try { await refresh(controller.signal); }
      catch { if (!controller.signal.aborted) setError("Connection lost. Reconnecting to Echo setup…"); }
      if (!controller.signal.aborted) timer = setTimeout(poll, 1500);
    }
    void poll();
    const changed = () => { void refresh(controller.signal).catch(() => {}); };
    window.addEventListener("imprint:voice-setup", changed);
    return () => { controller.abort(); clearTimeout(timer); window.removeEventListener("imprint:voice-setup", changed); };
  }, [refresh]);
  async function action(cancel = false) {
    setPending(true); setError("");
    try {
      const response = await fetch(`${API_BASE}/api/voice/setup${cancel ? "/cancel" : ""}`, { method: "POST" });
      const value = await response.json();
      if (!response.ok) throw Error(value.detail || "Couldn't start Echo setup. Try again.");
      setState(value);
      window.dispatchEvent(new Event("imprint:voice-setup"));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Couldn't connect to Echo setup."); }
    finally { setPending(false); }
  }
  return { state, error, pending, start: () => action(), cancel: () => action(true) };
}
