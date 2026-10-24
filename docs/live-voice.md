---
title: Echo voice companion
description: Floating particle avatar, push-to-talk, optional hands-free voice detection and local streamed speech.
---

## Current pilot

Echo is a floating, abstract holographic face for Imprint. The user selected the
**Echo** particle treatment from the [concept sheet](assets/voice/holographic-studies.png),
English first, LFM2.5-Audio-1.5B, push-to-talk and optional hands-free interruption.
[ADR 0043](adr/0043-live-voice-and-echo-companion.md) records the implementation.

The dashboard's **Voice** page supplies a real 3D particle head, streamed local
audio, typed or microphone questions, transient transcripts and two reasoning
routes:

- **Same local model as Chat** (default): LFM recognizes the utterance; Imprint's
  existing local chat model reasons using its existing read tools; LFM synthesizes
  phrases as chat text arrives. Remote chat providers are rejected for this route.
- **Small voice model · LFM 1.5B**: LFM recognizes the utterance, Imprint retrieves
  compact evidence using the actual MCP search implementation, and LFM generates
  interleaved text and audio. Retrieval is fixed by Imprint; the audio model does
  not issue arbitrary MCP calls.

Voice is **read-only**. The chat adapter enforces a read-tool allowlist and directs
writes to the dashboard. The small-model prompt likewise has no write interface.
Evidence is untrusted reference material; prompting is not a guarantee against
model prompt injection or incorrect answers. No recorded audio, transcripts or
conversation files are persisted by the live session. Explicit offline exports
from the [earlier CLI](lfm-audio-prototype.md) remain separate.

## Build the optional speech runtime

Platform follow-up ([ADR 0044](adr/0044-native-desktop-voice-platforms.md)) adds
native source builds for Windows x64 and macOS Intel/Apple Silicon. The live
installer now downloads portable weights without the Linux CLI archive, builds
per-target executables and verifies their target/source/build fingerprints.
Native results are tracked in platform validation; a platform being enabled
does not by itself mean it has passed its native checks.

On Windows use CMake and Visual Studio C++ Build Tools (x64); on macOS use CMake
and Xcode Command Line Tools, with macOS 13 or later. Run the same command below
using the native Python executable. Windows paths such as `C:\imprint-voice`
can avoid excessively long build paths. CPU builds do not require CUDA or Metal.

The speech worker is currently enabled on **Linux x64 CPU, including WSL2**.
There is no PyTorch dependency and no implicit model download on Start. Install a
C++ compiler and CMake, then from the repository root run:

```bash
/path/to/imprint-python -m imprint.voice_runtime \
  --runtime-dir /path/to/private/lfm-audio --jobs 4
```

`--cmake /path/to/cmake` selects an explicit CMake binary. The command reuses the
verified 1.07 GB Q4 weights from the offline experiment, downloads a hash-pinned
source archive, applies Imprint's private-worker patch and builds a static native
speech server. Compiler/system-library requirements are local build dependencies,
not additions to Imprint's base Python profile. The source/build cache can be
several hundred MB. Notices remain in that cache. Model licensing remains Liquid
AI's custom LFM Open License v1.0; this is not a claim of OSI-approved licensing.

The source is pinned to `tdakhran/llama.cpp` commit
`ec9d1fdd9cc18643c5e161b65b8053f6f6f34a4b`, the LFM audio integration linked by
the [upstream integration PR](https://github.com/ggml-org/llama.cpp/pull/18641).
Its streaming protocol differs from the older bundled executable. Imprint does
**not** launch that unmodified bundled HTTP server for browser sessions.

Configure the API's runtime directory and rebuild the UI:

```bash
imprint config set voice.runtime_dir /path/to/private/lfm-audio
cd imprint/ui
npm ci
npm run build
```

Restart Imprint's API/desktop shell, open **Voice**, and click **Start conversation**.
For a disposable development session, set `IMPRINT_VOICE_RUNTIME_DIR` on the API
process instead of changing saved configuration. Start only loads the local model
and enables typed questions. **Enable microphone** is a separate explicit action.
When testing, use a temporary `IMPRINT_DATA_DIR`; never run setup or fixture
retrieval against real user profiles.

This experimental cache is operator-owned, outside distributions and the main
installer/uninstaller. Delete its dedicated directory deliberately when no longer
needed. Windows/macOS/ARM speech workers and GPU inference are not enabled by
this pilot. Running the browser against a WSL API does not establish a native
Windows speech runtime.

## Talk and interrupt

**Push to talk** is the default. Enable the mic, then hold the button with a
pointer or hold Space/Enter while the button is focused. Release sends the
utterance. Between holds the audio track is disabled. Losing focus discards a
held recording. This is a focused-window shortcut, not a system-wide hotkey.

**Hands-free** enables the mic track continuously while the visible session is
active. An adaptive energy VAD keeps 240 ms of pre-roll, requires about 100 ms of
speech onset (140 ms while playing a reply), and ends the utterance after about
600 ms of silence. Each question is capped below 30 seconds. The threshold slider
helps in noisy rooms; this is an energy detector, not a trained speech classifier.

Browser audio constraints request echo cancellation, noise suppression and gain
control. Their availability and acoustic performance depend on the browser,
device and room. Speaker echo/noise can still trigger the energy VAD. Headphones
are useful for the initial hands-free trial; real speaker/room testing is still
required before calling hands-free robust.

On speech onset, push-to-talk press, Interrupt or a new typed question, the client
immediately stops all scheduled audio, invalidates the generation ID and asks the
API to cancel. Late chunks cannot restart playback. The API cancels the task and
terminates only its owned speech process, including native prefill, then reloads
it for a fresh turn. A normal completed turn keeps the model warm. Chat generation
receives a cancellation event, checks it before subsequent tokens/tools and closes
its token iterator; in-process model loading/prefill or an already executing read
cannot be forcibly interrupted safely. Installing chat dependencies may continue
under the existing provisioning policy, to avoid leaving a half-installed runtime.

Only fully played replies enter the bounded in-memory conversation history.
Interrupted, generated-but-unheard replies do not. Switching workspaces in another
window does not redirect the current voice session; stop and restart to adopt a
new workspace. One voice window owns the worker at a time. A session expires after
30 minutes and disconnects after two minutes without controls. Hidden/minimized
pages stop the microphone and connection; ordinary window blur only cancels a
held push-to-talk recording, so a visible floating hands-free companion can remain
useful while another application has focus.

## Floating companion and 3D asset

**Float Echo** opens a separate transparent, frameless Electron window. Drag its
header/grip, optionally pin it on top, and close it independently of the dashboard.
Opening it stops the dashboard voice session; start the companion explicitly.
The tray also exposes Float Echo. In an ordinary browser this opens a compact
popup; desktop transparency, dragging and pinning require the Electron shell.

The renderer uses a locally bundled Three.js module and a reproducible
[glTF binary head](../imprint/ui/public/models/imprint-echo.glb). The asset contains
29,512 particles, sparse eyelid/lip/nose contours, a faint facial depth shell and
`jawOpen`/`blink` morph targets. An Apache-2.0 MediaPipe canonical facial template
provides anatomy; Imprint authors the particle treatment, skull/neck, contours and
deformations. Licenses and attribution accompany the model. No camera, face
tracking runtime, online asset service or NVIDIA Audio2Face is needed.

Speech energy drives the jaw from actual playback audio; it is not phoneme/viseme
alignment. Idle motion, occasional blinks and listening/thinking tint are subtle.
Reduced-motion preferences reduce rendering/movement, pixel ratio is capped at
1.5, and normal rendering is capped near 30 fps. A WebGL failure leaves accessible
voice controls available. Other glTF viewers may choose different point sizes and
show the depth shell differently.

Regenerate the actual asset after editing its source:

```bash
cd imprint/ui
node scripts/build-echo-avatar.mjs
```

The concept sheet is ideation, not a promise of identical render quality. The
[generation record](assets/voice/concept-prompt.md) preserves its exact prompt and
the distinction between the raster concept and the real mesh/particle asset.

## Local boundaries and lifecycle

The browser connects only to Imprint's API. HTTP session creation and WebSocket
upgrade both validate the real loopback peer, Host and exact same-origin Origin;
the WebSocket has its own checks because HTTP middleware does not cover it. A
single-use capability expires in 30 seconds, is bound to origin/workspace/engine,
and is sent in the first WebSocket message, never in a URL. Browser extensions,
cross-port origins and container-peer exceptions are not admitted to voice.

The native worker listens only on literal `127.0.0.1` with a fresh private bearer
capability in its environment. It rejects all browser Origin headers and supplies
no wildcard CORS. The API alone knows the capability; it is not logged, returned
to the renderer or included in process arguments. Child environments omit provider
secrets/proxy settings; model request logs are discarded because upstream can echo
prompts. Model/source/binary integrity is checked before starting a worker.

Frames, microphone WAVs, generation duration, native tokens, request bodies,
history and pending capabilities are bounded. Temporary WAV validation files are
private and removed immediately. Slow socket sends time out. Closing/disconnecting
voice releases the worker; API shutdown also stops it. Electron permissions allow
only audio on the current owned Voice/companion main frame, denying camera and
other permission requests except the dashboard's existing same-origin clipboard
text writes; clipboard reads remain denied. macOS microphone usage text is present, but native
macOS permission behavior has not been validated.

## Validation and remaining gates

```bash
PYTHONPATH=. /path/to/imprint-python scripts/smoke-live-voice.py \
  --runtime-dir /path/to/private/lfm-audio \
  --output-dir /path/to/new-private/live-smoke
```

This runs a real loopback HTTP/WebSocket service and model, synthetic TTS→ASR,
retrieval-conditioned streamed replies, mid-speech cancellation, a fresh turn after
cancellation and real TTS around a fixture chat reasoner. It uses disposable data
and a retrieval storage fixture. It does not benchmark the actual Qwen chat model,
real microphones, speaker echo, pronunciation quality or full duplex audio-model
reasoning. See [platform validation](platform-validation.md#echo-live-voice-pilot-2026-10-24)
and the checked-in evidence for executed results.

The native Electron 45.0.0 graphical smoke also passed on Linux/WSLg with a
disposable profile, synthetic mic and software WebGL. It checked the actual
companion renderer, separate window, pin, permission, Stop and Close. The test
used `--no-sandbox` for the local WSL run; it does not establish packaged sandbox
behavior or native Windows/macOS support. Its reproducible driver is
`ui-electron/smoke-voice.mjs` and requires an already-running disposable API.
