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

The **Same local model as Chat** route may download the configured chat model
on your first question. Voice shows its real download bytes and loading stages
rather than a generic thinking state. This setup has a separate 30-minute
budget; normal reply generation times out after two minutes without progress.
Active long replies continue within the session's 30-minute lifetime.
Stopping voice clears playback and the voice request, while the existing chat
provisioning job finishes safely and can be joined by a later question.
Choosing **Small voice model · LFM 1.5B** avoids the additional chat model.

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
process instead of changing saved configuration. The first conversation starts
with typed questions; **Enable microphone** turns the microphone on and remembers
that choice for later conversations, as described below.
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

## Microphone permissions and recovery

Echo opens the microphone only when you ask for it: **Enable microphone**, or
**Start conversation** after you have chosen to use the microphone. In the desktop
app each of these first asks the operating system, then opens the device. Opening
Echo, recalling it, setup and typing never start capture.

The first successful **Enable microphone** is remembered on this device (in the
browser or desktop profile, not in settings, memories or sync), together with
push-to-talk or hands-free and the reasoning model. Later conversations turn the
microphone on once they are ready, in the remembered mode; push-to-talk stays
silent until you hold it. Clear **Use microphone for conversations** in
**Companion settings** to turn capture off at once and go back to typing; the
conversation stays open. The remembered choice is only your intent: the
operating system can still refuse, and is asked every time. Stop, or clearing
the choice while the system is still asking, means the microphone does not open
when the answer arrives.

- **macOS:** the first microphone action should show the system prompt.
  Affected `.pkg` installations (including observed v0.25.0) instead crash before
  consent because the enclosing app lacks microphone purpose text. Reinstalling
  the same package does not fix it; a corrected installer is required
  ([ADR 0075](adr/0075-microphone-purpose-in-the-macos-installer-wrapper.md)). If access was
  denied, use **Open microphone settings**, allow Imprint under **Privacy &
  Security → Microphone**, then **Quit** and reopen Imprint; closing the
  dashboard only hides it. macOS asks again after each update, because each
  release has a new ad-hoc signature ([ADR 0055](adr/0055-ad-hoc-signed-macos-desktop-bundle.md)).
  A restriction set by a device policy needs your administrator.
- **Windows:** Imprint reads the desktop microphone privacy setting; Windows has
  no per-app prompt for desktop apps. **Open microphone settings** opens
  **Privacy & security → Microphone**; turn on **Microphone access** and **Let
  desktop apps access your microphone**, then try again.
- **Linux:** there is no system consent to ask, so the microphone itself decides.
  If it is refused, check your desktop's microphone privacy settings and audio
  input; there is no common settings link.
- **Browser dashboard:** allow the microphone in the browser's site settings. Use
  the local dashboard address or HTTPS; other pages cannot capture audio.

A missing microphone, a device another app holds and audio processing that fails
to load each get their own message, with the browser's error name, and only a
refusal mentions permission. The message stays in Voice and the floating
companion until the next attempt, and typing keeps working.
[ADR 0069](adr/0069-echo-opens-the-microphone-only-on-the-users-intent.md)
records these choices.

## Talk and interrupt

Chat replies are split into phrases of at most 240 characters and synthesized
sequentially as text arrives. Playback reports completed PCM samples to the API;
generation waits when it has sent ten seconds of unplayed audio (with at most
one additional native chunk). This keeps preparing speech ahead of the listener
without hitting the browser's defensive 30-second queued-audio limit. The same
playback flow control applies to the small voice model's interleaved output.
Reload the dashboard after updating so its playback reports match the API.
[ADR 0092](adr/0092-voice-generation-follows-playback.md) records the protocol.

This extends playback of generated replies; it does not extend the reasoner's
context or output-token budget. The small voice model still has its existing
512-token generation cap. Voice history remains bounded to three completed
exchanges, with at most 3,000 characters retained per assistant reply.
The same-chat route shares Chat's [sequential section analysis](local-models.md#large-chat-questions-and-evidence)
for oversized retrieved evidence and shows its progress while it works; its
worker count is configured under Settings → Chat. Typed voice questions retain
their existing 2,000-character limit.

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
cannot be forcibly interrupted safely. A chat model download continues in its
provisioning job, which a later question can join.

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
controls have small backdrops for readability. Drag it by the head or the header;
the controls and the settings panel never drag it. Typing a question works before
a conversation starts: sending it starts one and delivers the question once Echo
is ready.
Its position and monitor are restored on normal open and kept inside the available display area. Press
**Ctrl+Alt+Shift+E** on Windows/Linux or **⌘⌥⇧E** on Mac to bring it to the cursor’s
display and focus it. The tray’s **Float Echo** action also recalls it if the OS
reserves the shortcut. Recall never enables the microphone. Close Echo independently
of the dashboard.
[Anime hologram preview](assets/voice/echo-anime-companion.png).
Opening it stops the dashboard voice session; start the companion explicitly.
The tray also exposes Float Echo. In an ordinary browser this opens a compact
popup; desktop transparency, dragging and pinning require the Electron shell.

Only talking stays on the companion's screen: **Push to talk**/**Hands-free**,
the current action and the status line. The **Companion settings** button opens
the rest: Echo's look, the reasoning model, the hands-free speech threshold, the
question field and transcript, **Interrupt**, **Stop**, **Keep on top** (which
shows whether Echo is pinned) and **Close**, plus how to move and recall Echo.
Settings close with Escape or a click outside; while they are open, a press on
the head closes them instead of moving the window. The Voice page has the same
button for its reasoning model and threshold.

Echo has three looks, and each speaks in its own voice. Choose one during
first-time setup, under **Echo's look and voice** on the Voice page or in the companion's
settings, or as `voice.avatar` in Settings, and change it anytime; the companion
uses it the next time it opens.

| Look | Voice |
| --- | --- |
| Echo | US male, with a robotic effect |
| Iris | US female |
| Kai | US male |

The native model is asked for the look's speaker on both reasoning routes: Chat
text read aloud and the small model's interleaved replies. Echo's robotic effect
is a continuous 70 Hz ring modulation with light quantization, applied to reply
audio in the dashboard; it keeps reply length and never touches the microphone.
No extra model, service or dependency is involved. A conversation keeps the voice
and reasoning model it started with: changing either during one ends it and
starts a new one once the old one has released the speech worker (waiting up to
eight seconds), and the short conversation history starts over. How natural each
voice sounds, whether the small model keeps the speaker through interleaved
replies, and how intelligible the robotic effect is still need listening tests.
[ADR 0071](adr/0071-each-look-speaks-in-its-own-voice.md) records this.

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

While the dashboard is closed, hidden or minimized, an open companion docks
beside the floating Imprint button, or above it on a narrow display. Dragging
either the button or the head moves both; the pair is kept inside one display's
work area and both positions are saved. The grouped companion stays on top with
the button. Clicking the button reopens the dashboard and restores the
companion's own **Keep on top** choice; the head then moves on its own again.
Closing the companion leaves the button. Recalling Echo while the dashboard is
hidden docks it beside the button again.
[ADR 0070](adr/0070-companion-moves-with-the-launcher-and-keeps-options-in-settings.md)
records the grouping, the drag handles and the settings panel.

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
text writes; clipboard reads remain denied. **Enable microphone** asks the main
process for OS consent first, over IPC that only those same Voice and companion
main frames may use; the main process checks the sender again after the user
answers. macOS asks only when consent is not determined and honors denied or
restricted status; Windows reports its desktop privacy setting. The only other
desktop IPC opens the fixed macOS or Windows microphone settings page. The app's
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

Two fixture smokes run before it in native CI, without voice models.
`ui-electron/smoke-microphone.mjs` checks the production permission handlers and
the OS consent IPC with synthetic devices and no fake permission prompt.
`ui-electron/smoke-companion-controls.mjs` drives Companion settings (the native
reasoning select, a look, the remembered microphone checkbox), a remembered
synthetic capture on the next conversation, and grouped drags of the launcher and
head. Neither establishes how a physical microphone, the macOS prompt or the
voices actually sound; those remain manual checks.
