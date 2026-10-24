---
title: ADR 0044 — Native Windows and macOS voice
description: Portable model weights, platform-specific private speech workers and native validation gates.
---

- Status: Accepted (implementation; native validation recorded separately)
- Date: 2026-10-24
- Related: [0043](0043-live-voice-and-echo-companion.md)

Follow-up: [ADR 0046](0046-visible-echo-runtime-setup.md) adds explicit UI-driven
provisioning and a managed default cache. The manual source-build path remains
available; system toolchains are not installed automatically.

## Context

The user requested Windows and macOS support after the Linux Echo pilot. The
first live installer reused a Linux-only archive verifier, assumed single-config
CMake output and Unix child startup, and therefore could not run natively on
those platforms. The user also clarified that the floating face must have a
transparent background without a box and be switchable from the Imprint UI.

## Decision

Separate portable, hash-verified GGUF weights from the historical Linux offline
CLI archive. Build the same pinned, authenticated CPU speech server natively for
Windows x64 and macOS Intel/Apple Silicon, retaining Linux x64. Use per-target
build/native directories, an executable suffix/configuration-aware layout and
target/build fingerprints. Reject a cache built for another target. Source
patches use explicit UTF-8 bytes so Windows newline conversion cannot invalidate
their fingerprints. Repeated installation verifies/reuses an intact worker.

Disable CUDA, Metal, external BLAS and OpenMP for this portable CPU baseline.
Windows uses the static MSVC runtime; macOS targets version 13 or later. Model
and source caches remain explicit optional developer installs, not bundled
release assets. No new Python runtime dependencies are introduced.

Use a hidden, directly owned Popen worker on Windows and a separate session on
Unix, without a shell. Popen avoids asyncio subprocess incompatibility with
Windows Selector loops; waits run off the API event loop. Forward the Windows
system/temp environment needed by the loader while excluding provider tokens
and proxy settings. Keep the existing loopback authentication, origin, workspace,
read-only and cancellation boundaries.

A private stdin lifetime pipe makes the worker exit even if its owning API is
abruptly terminated; Windows does not run Python cleanup on TerminateProcess.
macOS microphone consent is requested only for the user's mic action, with
denied/restricted states honored. The app includes microphone/JIT entitlements.
Graphical CI uses explicit disposable Electron profiles on every OS and checks
transparent PNG pixels and dashboard toggle cleanup. macOS TCC dialogs remain
manual validation; automation does not bypass production consent policy.

Use WebGL when available and a software 3D projection of the same glTF geometry
when WebGL2 is unavailable (for example hosted Mac desktops without a GPU).
Retain pose, speech-energy jaw and blink animation on transparent canvas, at a
lower frame-rate cap. Native tests record the actual renderer and use silent
WebAudio output on virtual desktops without speaker devices. This is not acoustic
validation. Limit model threads to available CPUs (at most four) and disable
thread polling so small M1 allocations are not overwhelmed by idle pools.
Recognized software WebGL backends render at 10 fps, while hardware WebGL uses
30 fps and the canvas fallback uses 15 fps. Two software-rendered heads could
starve Windows model verification/warmup and hit the startup deadline; the frame
cap limits actual CPU rendering work rather than extending model timeouts.

Allow up to two minutes for cold startup, including complete cache integrity
verification, with a separate 90-second native readiness limit. Native graphical
tests include both phases in their wait: Windows verification alone took 21
seconds under the software-rendered desktop, so a readiness-only test budget
incorrectly rejected a model that subsequently became ready. Timeout and closing
the companion release the owned session; integrity checks are never skipped.

Native CI builds and exercises real speech, HTTP/WebSocket transport, workspace
binding, authentication and mid-speech cancellation on Windows, macOS Intel,
macOS Apple Silicon and Linux. A workflow definition or cross-compilation is
not passing native evidence. Results and remaining desktop/acoustic limitations
belong in platform validation.

The companion renders only the holographic face and separate controls against
transparent pixels. The dashboard's on/off control owns its lifecycle and
receives close/error updates, so off releases microphone/session rather than
leaving an invisible listener. Showing the companion never opens the microphone
automatically. Desktop policy and UI are validated separately from speech.

## Alternatives

- Launch a WSL speech worker from Windows: introduces a second runtime/OS path
  boundary and does not solve native macOS support.
- Use the unpatched official native servers: loses the private listener policy.
- Require asyncio Proactor on every Windows server: fragile when other launchers
  and threaded frameworks choose a Selector loop.
- Bundle compiler/model caches into desktop installers: premature without a
  dedicated model provisioning/update/uninstall design.

## Consequences and validation

Local source builds require CMake and the platform C++ toolchain. The historical
offline CLI remains Linux-only; live voice uses the new portable build. Native
build/speech CI and desktop tests must pass before claiming platform validation.
OS microphone consent, hardware echo/noise, installer signing and packaged app
permission prompts remain separate gates. See [live voice](../live-voice.md)
and [platform validation](../platform-validation.md).
