"use client";

import Link from "next/link";
import { ArrowDownToLine, Check, HardDrive, ShieldCheck, ArrowUpRight, CircleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Spinner } from "@/components/loaders";
import { formatDownloadBytes, setupLabels, useVoiceSetup } from "@/lib/voice-setup";

const guide = "https://github.com/alexandruleca/imprint-memory-layer/blob/feat/local-voice-avatar/docs/live-voice.md#first-time-setup-in-imprint";
const problems: Record<string, string> = {
  cmake: "Install CMake, then check again. Imprint checks your build tools before downloading the voice models.",
  compiler: "A C++ build tool is missing. Install the tools for your computer below, then check again.",
  disk: "Free at least 4 GB on the setup drive, then try again. This includes temporary space for preparing Echo.",
  in_use: "Another Imprint process is setting up this voice cache. Wait for it to finish, then check again.",
  build: "The speech engine could not be prepared. Check the build tools below, then retry. Verified model files are kept.",
  download: "Setup couldn't finish. Check your connection and free disk space, then retry. Verified downloads are kept.",
  setup: "Echo setup couldn't start. Restart Imprint and try again. Your existing downloads are kept.",
};
const steps = ["Download", "Prepare", "Ready"];

export function VoiceSetup({ setup, compact = false }: { setup: ReturnType<typeof useVoiceSetup>; compact?: boolean }) {
  const { state, error, pending, start, cancel } = setup;
  if (state?.installed && !state.busy && !["error", "blocked", "cancelled"].includes(state.stage)) return null;
  const busy = !!state?.busy;
  const downloading = state?.stage === "downloading" || state?.stage === "verifying";
  const phase = state?.stage === "ready" ? 2 : ["engine", "preparing", "building", "finishing"].includes(state?.stage || "") ? 1 : 0;
  const percent = state?.total ? Math.min(100, Math.round(state.downloaded / state.total * 100)) : 0;
  const supported = state?.supported !== false;
  const title = busy || state?.problem || state?.stage === "cancelled" ? setupLabels[state?.stage || "idle"] : "Set up Echo, once";
  return <section className={`echo-setup ${compact ? "echo-setup-compact" : ""}`} aria-labelledby={compact ? "echo-setup-compact-title" : "echo-setup-title"} aria-busy={busy}>
    <div className="echo-setup-heading"><span className="echo-setup-icon">{busy ? <Spinner/> : state?.problem ? <CircleAlert aria-hidden/> : <ArrowDownToLine aria-hidden/>}</span>
      <div><p className="echo-setup-eyebrow">{busy ? "FIRST-TIME SETUP" : "YOUR VOICE, RUNNING LOCALLY"}</p><h2 id={compact ? "echo-setup-compact-title" : "echo-setup-title"}>{supported ? title : "Echo isn't available on this computer"}</h2></div>
      {!busy && supported && <span className="echo-setup-size">1.07 GB</span>}
    </div>
    {supported && <>
      <p className="echo-setup-description">{busy
        ? phase === 0 ? "Downloading and checking the files Echo needs. You can keep using Imprint." : "Preparing Echo for your computer. This can take a few minutes; you can keep using Imprint."
        : "Download the English voice models and prepare Echo for this computer. Future conversations use the saved files."}</p>
      <ol className="echo-setup-steps" aria-label="Echo setup stages">{steps.map((step, index) => <li key={step} data-current={index === phase && busy} data-done={index < phase}>
        <span>{index < phase ? <Check size={12} aria-hidden/> : index + 1}</span>{step}
      </li>)}</ol>
      {busy && <div className="echo-setup-progress">
        <div className="echo-setup-progress-label"><span role="status" aria-live="polite">{setupLabels[state!.stage] || "Preparing Echo"}</span><span>{downloading ? `${percent}%` : "In progress"}</span></div>
        <Progress aria-label={downloading ? "Voice model download" : "Echo preparation"} value={downloading ? percent : null}/>
        <p>{downloading ? `${formatDownloadBytes(state!.downloaded)} of ${formatDownloadBytes(state!.total)}` : phase === 0 ? "Checking saved files, build tools and available space before downloading." : "Model downloads are saved. This step prepares the local speech engine."}</p>
      </div>}
      {state?.problem && <p role="alert" className="echo-setup-error">{problems[state.problem] || problems.download}</p>}
      {state?.stage === "cancelled" && <p className="echo-setup-description">Verified files are saved. Continue whenever you’re ready; the current unfinished file downloads again.</p>}
      {error && <p role="alert" className="echo-setup-error">{error}</p>}
      {!busy && <div className="echo-setup-facts"><span><HardDrive size={13} aria-hidden/> Allow 4 GB free for setup</span><span><ShieldCheck size={13} aria-hidden/> Mic stays off</span></div>}
      <div className="echo-setup-actions">
        {busy ? <Button variant="outline" size="sm" disabled={pending || state?.stage === "cancelling"} onClick={() => void cancel()}>{state?.stage === "cancelling" ? "Cancelling…" : "Cancel setup"}</Button>
          : <Button className="echo-setup-download" disabled={pending || !state || !!error} onClick={() => void start()}>{pending ? <Spinner/> : <ArrowDownToLine size={16} aria-hidden/>}{pending ? "Starting setup…" : state?.stage === "blocked" ? "Check again" : state?.stage === "error" ? "Retry setup" : state?.stage === "cancelled" ? "Continue setup" : "Download & set up Echo"}</Button>}
        <a href={guide} target="_blank" rel="noreferrer">Setup details <ArrowUpRight size={12} aria-hidden/></a>
      </div>
      {!busy && <details className="echo-setup-tools"><summary>Build tools & download details</summary><p>{state?.platform === "win32" ? "Windows: CMake and Visual Studio C++ Build Tools (x64)." : state?.platform === "darwin" ? "Mac: CMake and Xcode Command Line Tools." : "Linux: CMake and a C++ compiler."} Imprint checks for these before downloading. Models come from Liquid AI on Hugging Face; engine source comes from GitHub. The engine needs extra download and temporary disk space. Setup does not start a conversation.</p></details>}
    </>}
  </section>;
}

export function VoiceSetupIndicator() {
  const { state } = useVoiceSetup();
  if (!state || (!state.busy && !["error", "blocked", "cancelled", "ready"].includes(state.stage))) return null;
  const text = state.busy ? `Echo · ${state.stage === "downloading" ? `${Math.round(state.downloaded / state.total * 100)}%` : "Setting up…"}`
    : state.stage === "ready" ? "Echo ready" : state.stage === "cancelled" ? "Echo setup stopped" : "Echo setup needs attention";
  return <Link href="/voice/" className="app-no-drag inline-flex min-w-0 items-center gap-2 rounded-full border border-border px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground" aria-label={`${text}. View voice setup`}>
    {state.busy ? <Spinner className="size-3"/> : state.stage === "ready" ? <Check size={13} aria-hidden/> : <CircleAlert size={13} aria-hidden/>}<span className="truncate">{text}</span>
  </Link>;
}
