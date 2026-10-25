---
title: Echo voice companion
description: Floating anime hologram, push-to-talk, optional hands-free voice detection and local streamed speech.
---

## Current pilot

Echo is a floating anime holographic head for Imprint. The user selected the
**Echo** particle treatment from the [concept sheet](assets/voice/holographic-studies.png),
English first, LFM2.5-Audio-1.5B, push-to-talk and optional hands-free interruption.
[ADR 0043](adr/0043-live-voice-and-echo-companion.md) records the implementation.

The dashboard's **Voice** page supplies a real modeled 3D anime head, streamed local
audio, typed or microphone questions, transient transcripts and two reasoning
routes:

- **Same local model as Chat** (default): LFM recognizes the utterance; Imprint's
  existing local chat model reasons using its existing read tools; LFM synthesizes
  phrases as chat text arrives. Remote chat providers are rejected for this route.
- **Small voice model · LFM 1.5B**: LFM recognizes the utterance, Imprint retrieves
  compact evidence using the actual MCP search implementation, and LFM generates
  interleaved text and audio. Retrieval is fixed by Imprint; the audio model does
  not issue arbitrary MCP calls.

Voice is **read-only**. The chat adapter offers the model only the read tools,
rejects any write call, and directs writes to the dashboard. The small-model
prompt likewise has no write interface.
Evidence is untrusted reference material; prompting is not a guarantee against
model prompt injection or incorrect answers. No recorded audio, transcripts or
conversation files are persisted by the live session. Explicit offline exports
from the [earlier CLI](lfm-audio-prototype.md) remain separate.

## First-time setup in Imprint

Open **Voice** or turn on **Floating Echo**. If the speech files are missing,
**Set up Echo, once** shows the approximately **1.07 GB** English model download
and the **2 GB** of free space needed during setup in a release installation.
Choose **Download & set up Echo** to begin. Opening the page never starts a download
automatically. The native speech engine is included: no CMake or C++ compiler is
needed. Imprint verifies the engine before downloading the models. Damaged engine
files produce an update/reinstall message. Developer checkouts without an engine
bundle retain the source-build path and need CMake, a compiler and 4 GB free space.
[ADR 0047](adr/0047-bundled-private-voice-engine.md) records the release policy.

The UI shows actual downloaded model bytes and verification, followed by engine
preparation and final checks. Engine preparation has an indeterminate indicator;
it does not fabricate a completion percentage.
This can take a few minutes. A top-bar indicator links back to setup while you
continue using other dashboard pages. Completion enables **Start conversation**;
it never opens the microphone or starts a conversation automatically.

The **Same local model as Chat** route may separately install chat support and
download the configured chat model on your first question. Voice shows its real
download bytes, installation and loading stages rather than a generic thinking
state. This setup has a separate 30-minute budget; normal reply generation is
bounded to two minutes once tokens begin. Stopping voice clears playback and the
voice request, while the existing chat provisioning job finishes safely and can
be joined by a later question. Choosing **Small voice model · LFM 1.5B** avoids
the additional chat model.

[Dashboard preview](assets/voice/echo-first-time-setup.png) ·
[Download progress](assets/voice/echo-download-progress.png) ·
[Transparent companion preview](assets/voice/echo-first-time-setup-compact.png).

**Cancel setup** stops its owned download/build process tree. Verified files are
kept; the unfinished file downloads again on retry. Closing the companion or
leaving Voice does not cancel setup. Closing the API stops it. A connection error
keeps reconnecting to its status; retry joins an existing job rather than
starting another download.

With no `voice.runtime_dir` override, files live in `voice/` under Imprint's data
directory. An existing setting/environment override is respected; setup does not
rewrite unrelated configuration. This dedicated optional cache is excluded from
distributions. [ADR 0046](adr/0046-visible-echo-runtime-setup.md) records the
provisioning policy. The CLI path below remains available for advanced setup.

## Build the optional speech runtime manually

Platform follow-up ([ADR 0044](adr/0044-native-desktop-voice-platforms.md)) adds
native source builds for Windows x64 and macOS Intel/Apple Silicon. The developer source
installer downloads portable weights without the Linux CLI archive, builds
per-target executables and verifies their target/source/build fingerprints.
Release installations use the prebuilt engine described above.
Executed native results and remaining manual gates are recorded in
[platform validation](platform-validation.md#native-echo-and-transparent-desktop-2026-10-24).

On Windows use CMake and Visual Studio C++ Build Tools (x64); on macOS use CMake
and Xcode Command Line Tools, with macOS 13 or later. Run the same command below
using the native Python executable. Windows paths such as `C:\imprint-voice`
can avoid excessively long build paths. CPU builds do not require CUDA or Metal.

The live speech worker has native CPU targets for **Windows x64, macOS Intel and
Apple Silicon (13+), and Linux x64 including WSL2**. Platform validation records
executed results separately from enabled targets.
There is no PyTorch dependency and no implicit model download on Start conversation. Install a
C++ compiler and CMake, then from the repository root run:

```bash
/path/to/imprint-python -m imprint.voice_runtime \
  --runtime-dir /path/to/private/lfm-audio --jobs 4
```

For example, in a Visual Studio Developer PowerShell (substitute the Python
executable from your Imprint installation):

```powershell
& 'C:\path\to\Imprint\.venv\Scripts\python.exe' -m imprint.voice_runtime --runtime-dir "$env:LOCALAPPDATA\Imprint\voice" --jobs 4
imprint config set voice.runtime_dir "$env:LOCALAPPDATA\Imprint\voice"
```

On macOS, use the installation's native Python and a private cache directory:

```bash
/path/to/imprint/.venv/bin/python -m imprint.voice_runtime --runtime-dir "$HOME/Library/Caches/Imprint/voice" --jobs 4
imprint config set voice.runtime_dir "$HOME/Library/Caches/Imprint/voice"
```

`--cmake /path/to/cmake` selects an explicit CMake binary. The command reuses the
verified 1.07 GB Q4 weights from the offline experiment, downloads a hash-pinned
source archive, applies Imprint's private-worker patch and builds a static native
speech server in a per-platform directory. It handles Windows `.exe`/Release
output and explicit macOS architectures. Source patches always use UTF-8.
Compiler/system-library requirements are local build dependencies,
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

Cold startup verifies the entire weight cache before loading the model. A slow
or busy disk can make this much slower than a warm speech turn. Startup has a
two-minute total limit and a separate 90-second native readiness limit; Cancel
or closing the companion releases the session while it loads. A timeout suggests
closing other applications and retrying, without weakening the integrity check.

This optional cache is excluded from distributions. The UI's default is the
dedicated `voice/` directory under local data; an explicit external directory
remains operator-owned. Delete only its dedicated directory when no longer
needed. Windows ARM, Linux ARM and GPU inference are not enabled by this pilot.
The earlier offline CLI remains Linux-only; the live installer never downloads
or executes that historical Linux CLI archive.

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

The **Floating Echo** switch in Imprint's top bar opens or closes the companion
from any dashboard page, following manual close and error events. Off destroys
the renderer and releases its microphone/session; on never enables the mic
automatically. **Float Echo** can also open it. The face floats directly over the
desktop with transparent pixels, no enclosing card, border or panel; individual
controls have small backdrops for readability. Drag it by the head, the header or the grip; the controls never drag it. Optionally pin it on top. Typing a question works before a conversation starts: sending it starts one and delivers the question once Echo is ready.
Its position and monitor are restored on normal open and kept inside the available display area. Press
**Ctrl+Alt+Shift+E** on Windows/Linux or **⌘⌥⇧E** on Mac to bring it to the cursor’s
display and focus it. The tray’s **Float Echo** action also recalls it if the OS
reserves the shortcut. Recall never enables the microphone. Close Echo independently
of the dashboard.
[Anime hologram preview](assets/voice/echo-anime-companion.png).
Opening it stops the dashboard voice session; start the companion explicitly.
The tray also exposes Float Echo. In an ordinary browser this opens a compact
popup; desktop transparency, dragging and pinning require the Electron shell.

Echo has three looks. Choose one during first-time setup, under **Echo's look**
on the Voice page or as `voice.avatar` in Settings, and change it anytime; the
companion uses it the next time it opens.

- **Echo** ([glTF model](../imprint/ui/public/models/imprint-echo.glb)), the default: an original holographic core.
  Segmented rings turn at their own speeds, faster while Echo thinks; the core swells as it speaks; and a lens on the
  core's face follows the pointer.
- **Iris** ([glTF head](../imprint/ui/public/models/imprint-iris.glb)): a head-only adaptation of the CC0 beta
  **Victoria Rubin** model, with straight-cut bangs, indigo locks folded at the jaw, its ribbon and side tail removed,
  and a generated pale lavender iris without pupils.
- **Kai** ([glTF head](../imprint/ui/public/models/imprint-kai.glb)): a head-only adaptation of the CC0 beta
  **HairSample_Male** model, with its spiky hair and eyes recolored orange and warm brown and its angry
  expression partly applied to the brows and eyes.

Echo looks at the pointer anywhere in the window, and anywhere on screen in the desktop app. Its eyes
turn first and its head follows more slowly. Each look declares this motion in its glTF node extras:
`gaze` nodes aim at the pointer within yaw and pitch limits, `spin` nodes turn, and `pulse` nodes grow
with Echo's voice. The heads' eyes turn where the source models' eye bones turn them, within the
look-at range those models declare.

The renderer uses a locally bundled Three.js module. Both heads keep modeled eyes/eyelids/lips and
`jawOpen`/`blink` expressions, and get a cool holographic surface and fine connected wire
contours on the skin and every hair layer. The user’s Hinata and Ichigo
references guide the anime direction; these are credited sample adaptations, not original sculpts
or licensed character models. Source checksums, the publisher’s CC0
release and attribution accompany the models. Rebuild with
`node imprint/ui/scripts/build-echo-avatar.mjs [echo|iris|kai] [path/to/pinned-source.vrm]`;
without arguments it rebuilds all three. Echo's core is generated and needs no source.
The authoring command may fetch the pinned source; the app loads only local files.
No camera, face tracking, external avatar service or NVIDIA Audio2Face is needed.
[ADR 0048](adr/0048-anime-echo-and-desktop-recovery.md) records the design and controls; [ADR 0054](adr/0054-echo-looks-declare-their-motion.md) the looks' motion and pointer follow.

Closing or minimizing the dashboard hides it to the tray and leaves a small
transparent floating Imprint icon. Drag it anywhere, click it to restore the
dashboard, or right-click for **Show Echo** and **Quit Imprint**. Explicit Quit
retains the existing owned-service shutdown policy. On Mac the menu-bar tray
image is two-thirds of its previous size. The compact companion fits its viewport
without a page scrollbar; long transcript text is bounded and full setup details
remain on the Voice page.

Speech energy drives the jaw from actual playback audio; it is not phoneme/viseme
alignment. Idle motion and occasional blinks are subtle; controls show the listening/thinking state.
Reduced-motion preferences reduce rendering/movement and pixel ratio is capped at
1.5. Hardware WebGL rendering is capped near 30 fps. Recognized software WebGL
backends (including SwiftShader/llvmpipe) use a 10 fps cap so their rasterizer
threads do not overwhelm local model startup. Software rendering drops to 2 fps
while connecting/thinking; the dashboard head holds its last frame while the
floating companion is open. Reduced-motion idle rendering uses 1 fps. When WebGL2 is unavailable, a 15 fps
software renderer projects the same textured glTF geometry, pose and jaw/blink morphs onto
transparent canvas; it retains a real animated 3D head without a GPU or static
image substitution. If both rendering methods fail, accessible voice controls
remain. Other glTF viewers may choose different point sizes and
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
voice releases the worker; API shutdown also stops it. A private lifetime pipe
also stops the worker on abrupt API death, including Windows termination where
Python cleanup cannot run. Hidden Windows startup works with either Selector or
Proactor event loops. Electron permissions allow
only audio on the current owned Voice/companion main frame, denying camera and
other permission requests except the dashboard's existing same-origin clipboard
text writes; clipboard reads remain denied. macOS checks OS microphone consent,
asks only when not determined, and honors denied/restricted status. The app's
bundle is signed ad hoc, so its Info.plist and microphone usage text are sealed
to its identity ([ADR 0055](adr/0055-ad-hoc-signed-macos-desktop-bundle.md));
an ad-hoc identity changes with each update, so macOS asks again after one.
Interactive TCC consent and Developer ID signing/notarization require separate
validation.

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
reasoning. See [platform validation](platform-validation.md#native-echo-and-transparent-desktop-2026-10-24)
and the checked-in evidence for executed results.

The native Electron 45.0.0 graphical smoke checks the actual companion renderer,
transparent screenshot pixels, separate window, pin and dashboard on/off session
cleanup. Its reproducible driver is
`ui-electron/smoke-voice.mjs`; `scripts/smoke-voice-desktop.py` supplies a disposable
API/profile and checks PNG transparency. Native CI combines speech and graphical
checks on Windows x64, macOS Intel/Apple Silicon and Linux x64. Linux automation
uses `--no-sandbox`; it does not establish packaged sandbox behavior.
macOS automation uses a typed session rather than bypassing production
TCC policy for a fake mic; interactive consent remains a manual gate. Optional
`--native-logs` on either smoke retains native logs from disposable synthetic
fixtures only for diagnosis. Production request logs remain discarded. Graphical
CI uses a silent WebAudio output sink because hosted machines need not expose
physical speakers; the audio clock/graph still run. Real output devices are a
separate manual check. Worker thread counts respect available CPU allocation,
with at most four threads and polling disabled; this avoids overwhelming small
hosted M1 allocations during simultaneous audio-model work.
