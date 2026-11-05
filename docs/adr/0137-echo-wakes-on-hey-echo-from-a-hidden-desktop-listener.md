---
title: ADR 0137 — Echo wakes on "Hey Echo" from a hidden desktop listener
description: On the user's choice, the desktop app keeps a hidden, always-indicated listener window whose energy gate sends the first 2 s of each burst of speech to a local worker running Silero VAD and Whisper tiny.en with the phrase as hotwords; a wake shows Echo without focus with the rest of the sentence as its first question.
---

- Status: Accepted
- Date: 2026-11-05
- Scope: desktop app only; measured on an Apple M3 with synthetic speech (see Validation); real voices, rooms, and Windows and Linux hardware are unvalidated
- Amends:
  - [ADR 0043](0043-live-voice-and-echo-companion.md) — its rule that hidden pages stop capture: one hidden window, the wake listener, captures while it exists, always with an indicator; its deferred trained VAD (Silero) is used, for the wake detector only
  - [ADR 0069](0069-echo-opens-the-microphone-only-on-the-users-intent.md) — a standing "Hey Echo" choice is the user's intent for that listener's microphone; a conversation the wake starts turns the microphone on hands-free for its duration, after the same OS consent step
  - [ADR 0096](0096-cpu-whisper-runtime-for-audio-imports.md) — adds the English `tiny.en` model, pinned the same way, downloaded only when the user turns "Hey Echo" on
  - [ADR 0135](0135-the-floating-launcher-is-a-handle-with-its-actions-around-it.md) — the orb shows an "ear" dot while the listener exists, and a pulse when it hears the phrase
- Related: [ADR 0131](0131-meetings-are-recorded-by-channel-and-transcribed-locally.md), [ADR 0032](0032-one-onnx-runtime-per-environment.md), [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md), [live voice](../live-voice.md)

## Context

The user asked for "a small VAD that can wake up the echo voice … if I say Hey Echo". Echo listened only after a click: push-to-talk by default, or a hands-free energy gate (`VoiceGate`) inside a conversation the user started (ADR 0043, 0069). Folding Echo into the orb hides its window, which ends its conversation and microphone, and ADR 0135 rejected keeping Echo listening while folded because it would leave a live microphone with no visible surface. The orb's window has no microphone permission.

What every install already had: faster-whisper 1.2.1 with CTranslate2 and ONNX Runtime (CPU) in `requirements/cpu.txt` (ADR 0096), and inside faster-whisper's package the Silero VAD v6 model (`assets/silero_vad_v6.onnx`, 1.2 MB). Echo's own recognizer is LFM2.5-Audio-1.5B on a private worker held only while a conversation is open.

A design pass measured candidates on an Apple M3 with 144 positive and 234 hard-negative clips from 18 macOS `say` voices, each also with white noise at 10 dB SNR, 20.5 minutes of unrelated reading that says "Echo" dozens of times, and a 12-minute busy-room stream. The pipeline measured: an energy gate finds a burst of speech, Silero confirms at least 0.2 s of speech in its first 2 s, Whisper transcribes those 2 s, a text matcher looks for a greeting followed by "Echo":

| Recognizer | Positives woke (clean / noisy) | Bare "Hey Echo" | Hard negatives woke | CPU per probe (1 thread) |
|---|---|---|---|---|
| `tiny.en`, phrase as `hotwords` | 141/144 / 135/144 | 52/54 | 0/216 ("Hey Eggo" always wakes it) | 0.31–0.45 s |
| `tiny.en`, no biasing | 100–108/144 / 63–65/144 | 31–35/54 | 0/216 | 0.32 s |
| `base.en`, no biasing / with hotwords | 112–121/144 / 113/144 | 38–51/54 | 0 / 1–5 of 216 | 0.73 s |
| sherpa-onnx open-vocabulary keyword spotter | 56–74/144 / 17–20/144 | 29–35/54 | 0/216 | 2.2–2.7 % of a core, continuously |

No false wake in the 20.5 minutes of reading with `tiny.en` and hotwords. A never-shown Electron 45 window with `backgroundThrottling: false` reported itself visible, started its AudioContext without a gesture under `autoplayPolicy: 'no-user-gesture-required'`, and delivered all 50 capture frames a second; with Chromium's fake microphone its renderer and audio service used 0.5–0.8 % of one core.

## Decision

**A hidden listener window, only in the desktop app, only on the user's choice.** The main process creates a window loading `/wake/` that is never shown, and destroys it, releasing the microphone, whenever listening should not run: the choice is off or blocked, the app is not ready, a pause is set, a meeting is recorded, an Echo page holds the microphone, the screen is locked, the computer sleeps, it runs on battery and the user chose to pause there, or macOS reports a critical thermal state. The window keeps its timers unthrottled and may start audio without a gesture. Electron grants it the microphone only on `/wake`, audio only, main frame only; `/wake` grants nothing in any other window. The page asks the OS consent step (ADR 0069) before opening the device. It uses the microphone chosen in Voice settings, without echo cancellation or gain control and with noise suppression.

**Silence stays in the page; only the first 2 s of each burst leaves it, over loopback.** The page runs the energy gate. For each burst of speech it sends the first 2 s (pre-roll included), or the whole burst if shorter but at least 0.3 s, as a 16 kHz PCM16 WAV to `WS /api/voice/wake/stream`, one probe at a time. The API relays it to a worker process (`python -m imprint.wake_worker`) it starts for that socket and stops with it, at a lower priority. The worker confirms speech with Silero, transcribes with Whisper `tiny.en` (`int8`, one thread, beam 1, English, the phrase as `hotwords`, at most 10 tokens) and matches a greeting (`hey`, `hi`, `ok`, `hello`, and Whisper's `hay`, `he`, `here`) immediately followed by Echo or a close spelling (`Ecco`, `Eko`, `Ico`, or merged as `Hayeko`) within the first four words. It answers yes or no and keeps nothing: audio is parsed in memory, never written, and no transcript of a probe that did not wake Echo is returned or logged. Probes are budgeted at 2.5 CPU-seconds a minute (1.25 on battery); over budget, only bursts after at least 1 s of quiet are checked, up to twice the budget.

**A wake shows Echo without focus and hands it the rest of the sentence.** On yes, the page tells the main process at once, which records where Echo was (closed, folded or open), pulses the orb, shows Echo (docked by the orb when the dashboard is hidden) without taking focus, and tells it to start a conversation, or to carry on the one open there. That conversation is hands-free and turns the microphone on when ready, whatever the remembered mode. The page keeps capturing to the end of the burst, sends it once, and the worker transcribes it; the text after the wake phrase becomes Echo's first question, shown in its transcript. The listener stays until the question is handed over (or 35 s), then pauses while Echo's microphone is on.

**It ends by itself.** A wake-started conversation ends after `voice.wake.idle_seconds` (15 s by default) without speech once Echo's reply has played, or when a whole utterance asks it to ("stop", "that's all", "thanks, Echo"…), which the API recognizes before reasoning and answers with `end`. Echo then goes back where it was. Any click or key in Echo first makes it an ordinary conversation.

**The choice is the device's, and every indicator follows the listener.** Turning it on is in Echo's Companion settings, after an explanation sheet, then the OS consent step, then the one-time download of the pinned model (78 MB) with its progress. `wake.json` in the desktop profile holds `{enabled, pausedUntil, battery}`; it is not a setting, a memory or synced data. Whenever the listener window exists, the orb shows its ear dot, the dashboard's top bar shows a chip with Pause (1 hour) and Off, and the tray's tooltip and first menu line say so; all three come from one state in the main process. A refused microphone or a missing model blocks listening until the user turns it on again; a lost service is retried after 30 s. A plain browser does not offer it.

## Rejected alternatives

- **Listening in Echo's window while folded, in the orb's window, or in the dashboard window.** ADR 0135 rejected the first (a live microphone with no surface, and Echo's page stops on hide by design); the orb has no microphone by design; the dashboard navigates, so capture there would need the microphone on every page.
- **Capturing in the Python server.** ADR 0131 rejected native capture for the same reasons: per-platform audio code, no Chromium noise suppression, and it would bypass Electron's permission handler and the OS consent step.
- **Running Silero continuously in the API instead of the gate in the page.** Silero is cheap (77 µs per 32 ms frame, 0.24 % of a core), but the page would stream all audio out while nobody speaks.
- **Echo's LFM2.5-Audio as the recognizer.** It transcribed a 1.5 s probe correctly in 0.18 s, but held 1,616 MB resident after loading and used 0.72 CPU-seconds per probe on 4 threads, and one worker serves conversations.
- **The installed `large-v3-turbo`.** 14.9 s and 37.7 CPU-seconds per 1.5 s probe.
- **`base.en`, with or without biasing.** 2.3x the CPU of `tiny.en`, and not better: with the prompt it often left "Hey Echo" out of its transcript and woke on 1–5 hard negatives.
- **Whisper without biasing and a looser matcher.** Unbiased `tiny.en` wrote "Hey, I go", "Here you go" or "Hayeko" for a third of bare "Hey Echo"s; accepting such text would wake on ordinary speech.
- **openWakeWord with a custom "hey echo" model.** None exists; its pre-trained models and its standard 17.28 GB negative training features are CC BY-NC-SA 4.0, and every new phrase needs training. Its runtime cost (2.35 % of a core with two stand-in models) is paid every second.
- **sherpa-onnx open-vocabulary keyword spotting.** Lower recall on the same clips (above), a second ONNX Runtime inside its wheel (ADR 0032), and a model trained on GigaSpeech, whose audio terms are non-commercial.
- **Picovoice Porcupine** needs an AccessKey; **the Web Speech API** sends audio to Google in Chrome and has no recognizer in Electron; **OS recognizers** need native code per platform and a second macOS permission.
- **A browser-dashboard listener.** No indicator outside its tab, and the Voice page stops on hide.

## Consequences

- Echo can be woken by voice from any app, with the question in the same sentence, without a click.
- While listening, the operating system's microphone indicator stays on (macOS menu bar and Control Center; Windows notification area); the docs say so.
- Cost: about 0.5–0.8 % of one core for the hidden page in a quiet room, plus 0.31–0.45 CPU-seconds per burst of nearby speech, bounded at about 5 % of a core in non-stop talk; the worker held 355 MB right after loading and probing in a standalone measurement.
- "Hey Eggo" and similar sounds wake Echo. Other media and call participants saying "Hey Echo" can too; recordings pause the listener, other calls do not (Pause for 1 hour exists for that).
- A Bluetooth headset chosen as the microphone switches to its low-quality hands-free profile for as long as listening runs. No warning is shown yet.
- No chime plays on a wake; the orb's pulse and Echo appearing are the only acknowledgement.
- Only English; the phrase is "Hey/Hi/OK Echo", not the looks' names.

## Invalidation conditions

- If real recordings of users show recall below 90 % or more than one false wake an hour of ordinary room audio, add a confidence floor on the probe or a second-stage classifier, or revisit the recognizer.
- If a permissively licensed "hey echo" keyword model appears with lower always-on cost and equal recall, replace the per-burst Whisper probe with it.
- If Electron stops running audio or AudioWorklets in a never-shown window, the listener becomes a minimal shown, transparent, click-through window.
- If the operating system attributes the hidden window's capture to anything other than the app, revisit the consent step and usage text (ADR 0069, 0075).
- If people hit the Bluetooth hands-free profile or miss an audible acknowledgement, add the headset warning or the chime that were left out.

## Validation

- `tests/test_wake.py` (Python 3.12, macOS arm64): the matcher on every measured positive and hard negative plus "Here you go", "Hey, I go", "He echoed" and "Hey, co-workers"; the question after the phrase; end phrases; probe WAV bounds parsed in memory with temporary files forbidden; the budget; the pin; status checks sizes, the worker verifies hashes and never downloads, and the download reports to its caller, not the job progress record; the worker's JSON-line protocol and its exits; the routes' origin checks, one listener at a time, skipped probes over budget and on battery, an utterance only after its own wake, size limits, and a real worker that fails to load reporting why. With `IMPRINT_WAKE_MODEL_DIR` set to the measured `tiny.en`, the real model woke on "Hey Echo, what did we decide yesterday?", transcribed the question, ignored "The echo in this room is terrible." and silence, and a real worker process answered through the API's `WakeWorker`.
- `tests/test_voice.py`: a wake-started turn that says "Thanks, Echo." ends without reasoning; the same words outside a wake session are a question.
- `node --test` in `ui-electron` (macOS): the stored choice and when the listener runs; the listener window's options; that only it gets the microphone and only on `/wake`; recordings, Echo's microphone, lock, sleep, heat, battery, pause and off destroying it and its return; the tray, top bar and orb state equal to the window's existence at every step; a wake showing Echo inactive, keeping the listener until the question is handed over, and returning Echo folded or closed; a refused microphone blocking until turned on again.
- Playwright (`imprint/ui/tests/wake.spec.ts`, Chromium with a synthetic microphone): one 16 kHz probe of at most 2 s per burst, then the question after a wake; a missing model reported; a plain browser listens to nothing; the explanation sheet, consent, download progress and turn-on; a refused microphone; a wake-started conversation sending its question with `wake`, ending on `end`, and a click keeping it; a wake during a typed conversation carrying it on hands-free; the top-bar chip and the orb's ear and pulse.
- `ui-electron/smoke-wake.mjs` passed in real Electron 45 on macOS (Apple M3) with the dashboard bundle and Chromium's fake microphone playing macOS `say` speech, both with fixture answers and with the real wake worker and `tiny.en`: the hidden listener ran unseen and the orb showed its ear; the worker woke on the first probe (0.38–0.41 CPU-s) and transcribed "What did we decide yesterday?"; Echo appeared 3–4 s after the listener started (the speech begins 1 s into the recording) without focus and received it as a wake turn, ended when the fixture answered "stop", closed again, and listening resumed. Over 20 s of quiet the listener's renderer used 0.54–0.60 % and the audio service 0.28–0.29 % of one core, with a 95–109 MB working set; the worker's resident memory read 48–150 MB at that point (355 MB right after loading and probing when measured on its own).
- Not verified: a physical microphone and real voices, accents and rooms; hours of real audio for the false-wake rate; the macOS consent prompt and indicator with a signed build; Windows and Linux hardware (the native voice workflow runs the smoke there with fixture answers); battery drain.
