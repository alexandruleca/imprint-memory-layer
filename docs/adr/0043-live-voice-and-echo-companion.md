---
title: ADR 0043 — Local live voice and the Echo companion
description: Explicit speech runtime, same-chat reasoning, optional VAD and a floating particle head with private session ownership.
---

- Status: Accepted (particle asset superseded by [0048](0048-anime-echo-and-desktop-recovery.md)) (experimental live pilot)
- Date: 2026-10-24
- Related: [0042](0042-lfm-audio-cpu-experiment.md), [0024](0024-enforce-local-service-boundaries.md), [0025](0025-desktop-owned-background-service.md)

Follow-up: [ADR 0044](0044-native-desktop-voice-platforms.md) extends the worker
to native Windows and macOS and adds the dashboard toggle, borderless transparent
treatment and software 3D rendering. The Linux-only rationale below records the
original pilot scope; current validation is recorded in platform validation.

## Context

The user authorized implementation after the offline LFM CPU experiment, selected
the Echo holographic particle face, and requested a floating companion with
push-to-talk plus optional hands-free/VAD interruption. The original requirement
to reuse the local chat reasoner remains useful; LFM's own backbone is a separate
smaller alternative. The bundled upstream audio server has wildcard CORS and no
authentication suitable for private Imprint evidence. HTTP middleware alone does
not protect a WebSocket endpoint.

## Decision

Add an explicitly built Linux x64 CPU speech worker, pinned to source commit
`ec9d1fdd9cc18643c5e161b65b8053f6f6f34a4b` with a SHA256-verified source archive,
reusing ADR 0042's verified model weights. Apply a fail-closed source patch that
forces literal loopback, private bearer authentication, no browser origins/CORS,
bounded payload/tokens and safe disconnect cancellation. Keep compiler/source/
model caches external and optional; do not extend installer platform support.
Check source/patch and locally built executable fingerprints before starting.

Imprint's API issues a short-lived, one-use same-origin capability with captured
workspace/engine. A separately checked WebSocket owns one native worker/session;
shutdown and disconnect stop that owned process. Microphone WAVs are bounded,
validated privately and discarded. The browser sees only Imprint's API and never
the native worker's port or capability. No raw audio, transcript or native prompt
log is persisted by live sessions.

Default reasoning goes through the dashboard's existing **local** chat model and
its read tools, using LFM ASR/TTS as speech components. An optional small-model
route retrieves bounded MCP evidence per utterance and lets LFM answer directly.
Enforce read-only tools for voice and preserve session workspace over model tool
arguments. Remote providers are rejected for the same-chat route. Voice does not
inherit memory-write authority just because a generative model asks for it.

Push-to-talk is default; mic access is a separate action from starting a typed
conversation. Hands-free uses opt-in continuous capture and adaptive energy VAD
with hysteresis, pre-roll, silence endpointing and requested browser AEC. Onset
flushes playback immediately and invalidates generation IDs. The API stops its
native speech process on interruption; chat uses cooperative cancellation before
later tokens/tools. Only fully played replies enter transient bounded history.
Hidden/minimized pages stop capture/session; blur discards held recordings.

Use a lazy-loaded Three.js dependency for the real Echo asset: deterministic
particles/contours, Apache-2.0 canonical facial anatomy, jaw/blink morphs and a
reproducible GLB generator. Include source attribution and license with the model.
Playback energy animates the jaw; no phoneme-sync or Audio2Face claim. Cap display
resolution/frame rate, honor reduced motion and retain controls without WebGL.

Expose a separate transparent sandboxed Electron companion window with scoped IPC,
drag handles, optional always-on-top and independent close. Audio-only permissions
are limited to current owned voice surfaces/main frames. Preserve existing
same-origin clipboard text writes while denying clipboard reads. Add macOS usage text
without claiming native macOS validation. Browser popup fallback has browser window
limitations.

## Alternatives

- Expose the bundled unauthenticated native listener: rejected because browser
  origin and evidence confidentiality must be enforced at every listener.
- Keep per-turn CLI startup and full WAV playback: cannot supply streamed
  playback or controlled mid-generation interruption without repeated startup.
- Use LFM as the only reasoner: smaller, but loses the user's same-chat-model goal.
- Claim native full-duplex reasoning: VAD/cancellation supplies interruptible
  utterance turns, not PersonaPlex-equivalent joint listening/speaking inference.
- Start with trained neural VAD, Audio2Face or a proprietary avatar SDK: additional
  models/runtime cost is premature before acoustic validation of the pilot.
- Make hands-free the default: conflicts with deliberate microphone control.

## Consequences

The pilot supplies tangible live controls and a real exportable avatar, with
separate small-model and same-chat routes. Rebuilding the owned native worker on
interruption costs warm-up on the next turn. Chat prefill/model loading and reads
already executing may finish after cooperative cancellation; later output/tools
are blocked. In-process chat provisioning follows the existing safe completion
policy. Energy VAD can mistake room noise/speaker echo for speech, so acoustic
tests remain necessary. Optional developer caches are not managed release assets.

## Validation

Real Linux/WSL CPU HTTP/WebSocket smoke verifies ASR, retrieval-conditioned speech,
first audio, native authentication/origin denial, workspace pinning, mid-speech
owned-process cancellation and a fresh turn afterward. A fixture chat reasoner
verifies routing with real LFM synthesis; it does not establish Qwen end-to-end
latency. Python tests exercise ownership, replay/expiry/origin boundaries,
cancellation, read-only tools and WAV limits. Chromium tests load the real GLB,
capture from a synthetic mic through AudioWorklet, check UI interruption/stale
chunks and compact layout. Electron lifecycle/permission tests use controlled
doubles; a real Electron 45 Linux/WSLg graphical smoke also passed using a
disposable profile, synthetic mic and software WebGL (local test `--no-sandbox`,
not packaged-sandbox validation). Native Windows/macOS remain untested. See
[live voice](../live-voice.md) and [platform validation](../platform-validation.md).
