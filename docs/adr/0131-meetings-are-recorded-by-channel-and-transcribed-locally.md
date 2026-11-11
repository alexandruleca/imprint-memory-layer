---
title: ADR 0131 — Meetings are recorded by channel and transcribed locally
description: The dashboard records the microphone and, with the user's consent, what the computer plays, as two channels of one file kept on the device; Imprint transcribes each channel locally so the transcript says who spoke, and stores it as a replaceable source.
---

- Status: Amended
- Date: 2026-11-04
- Amended by:
  - [ADR 0135](0135-the-floating-launcher-is-a-handle-with-its-actions-around-it.md) — a recording can start and stop from the desktop app's floating button, which relays it to the dashboard window
  - [ADR 0173](0173-a-transcript-leaves-out-the-microphones-echo-of-the-call.md) — the transcript leaves out the microphone's echo of the computer's audio, and each turn is a paragraph
  - [ADR 0177](0177-recordings-travel-between-devices-that-both-turned-it-on.md) — the raw audio can leave the device: to paired devices where the user turned recording sync on, both of them
  - [ADR 0180](0180-a-recordings-transcript-tells-voices-apart-within-each-channel.md) — the transcript tells voices apart within each channel, not only by channel, and its speakers can be renamed
- Related: [ADR 0095](0095-audio-files-enter-the-source-ingestion-pipeline.md), [ADR 0096](0096-cpu-whisper-runtime-for-audio-imports.md), [ADR 0060](0060-redact-secrets-before-memories-are-stored.md), [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md), [ADR 0069](0069-echo-opens-the-microphone-only-on-the-users-intent.md)

## Context

The user asked to record the audio of online meetings and calls on their laptop and get transcripts to extract information from. They wanted the raw audio and the transcripts kept in one place so they can be ingested again.

The user named Wispr Flow. It is a dictation tool for the user's own speech and does not record calls.

What Imprint already has:

- **Local transcription.** Audio files are transcribed on the CPU with faster-whisper (ADR 0095, 0096). The pipeline already has a pinned model, size and duration limits, voice detection and redaction.
- **Replaceable named sources.** `ingest_core.store_inline_content` stores a named text and replaces it when the same name comes again.
- **Microphone permission in the desktop app**, granted to Echo's pages after the OS consents (ADR 0069).

A call has two sides: the user's microphone and what the computer plays. Recording them as separate channels tells the transcript who spoke without speaker diarization.

## Decision

**Recording.** The new Recordings page starts a recording only after the user confirms that everyone on the call knows it is being recorded. The API refuses a start without that confirmation.

The dashboard captures:

- the microphone chosen in Voice settings, as the left channel;
- when asked, what the computer plays, as the right channel.

The computer's audio is the audio track of a screen-capture request. Its video is stopped at once. The desktop app answers that request from its dashboard window's Recordings page with the system audio loopback (`setDisplayMediaRequestHandler`, `audio: 'loopback'`), with no picker, and refuses every other screen-capture request. A browser asks the user what to share. Without the computer's audio, only the microphone is recorded, and the page says so.

The recording is Opus in WebM. Pieces upload every 5 seconds to `data/recordings/<id>/audio.webm`, so a closed window keeps what was sent. A recording interrupted that way can be finished from the list. Recording continues while the user moves between pages, with an indicator and a Stop button in the top bar. The desktop app also allows the microphone on `/recordings`.

**Transcription.** Stopping queues a `transcribe-recording` job, one heavy job at a time (ADR 0064). The job:

1. splits the stereo file into a mono 16 kHz WAV per channel and skips a silent one;
2. transcribes each with the audio pipeline (ADR 0095, 0096), with its model, limits, voice detection and redaction;
3. merges the segments by time into a Markdown transcript. Each turn is labelled "You" (the microphone) or "Others" (the computer's audio), with its time.

The transcript is stored as the source `recording/<id>` in the recording's project (default `meetings`), and kept as `transcript.md` beside the audio. Transcribing again replaces the source.

**Storage.** The raw audio stays on this device: sync carries the transcript's memories, not the audio. The user can delete the audio alone, keeping the transcript, or delete the whole recording with its memories.

## Rejected alternatives

- **Wispr Flow**, as the user named. It is dictation for the user's own speech, with transcription done by its service, and records neither side of a call.
- **Recording one mixed channel and diarizing speakers.** Imprint has no diarization model, and mixing loses the one reliable fact about who spoke: which side of the call the sound came from.
- **Recording in the Python server.** Capturing the system's audio needs per-platform native code (WASAPI loopback, ScreenCaptureKit, PulseAudio). The dashboard already runs in Chromium, which provides both captures.

## Consequences

- The user can record a call in the dashboard and search what was said, attributed to their side or the other.
- Without headphones, the microphone also picks up the other side from the speakers. Echo cancellation reduces this, but some speech can then appear under both "You" and "Others".
- Recording other people needs their consent, and the law differs by place. Imprint asks for the confirmation every time; it cannot verify it.
- Transcription is CPU-bound. 9.9 s of two-voice audio took 28.6 s with large-v3-turbo on an Apple M3, model load included. An hour-long call takes correspondingly long in the queue. The 120-minute and 1 GiB audio limits apply to each recording.
- Transcribing needs `audio.enabled`. When it is off, the recording fails with the reason and can be transcribed again once it is on.
- macOS system audio through Electron's loopback is not confirmed. Without it, a desktop recording on macOS keeps the microphone only and says so.

## Invalidation conditions

- If a local diarization model is adopted for audio ingestion, use it within each channel too.
- If Electron's loopback proves unavailable on macOS, capture system audio there with ScreenCaptureKit in the desktop app.
- If transcription moves off the CPU, revisit the duration limits for recordings.

## Validation

- `tests/test_recordings.py`:
  - consent is required, pieces append until the recording stops, and the recorder's duration is bounded by the time that passed;
  - the size limit stops audio;
  - a real stereo WebM/Opus splits into You and Others, and a silent side is skipped;
  - the transcript orders and merges turns;
  - transcription stores a replaceable `recording/<id>` source, and a failure records why;
  - deletion removes the audio alone, or the folder and its memories;
  - the queue runs only a valid id;
  - the API starts, takes audio and stops into a queued transcription.
- `ui-electron/voice-permissions.test.js`: the Recordings page may use the microphone, and only the dashboard window's main frame on `/recordings` asking for audio gets the system audio.
- `imprint/ui/tests/recordings.spec.ts`:
  - recording waits for consent, keeps going across pages, and stops from the top bar after uploading audio;
  - without the computer's audio it records the microphone only and says so;
  - the list shows transcripts, transcribes again, finishes an interrupted recording, and deletes audio or whole recordings.
- macOS on an Apple M3:
  - Chromium recorded its fake microphone and a 660 Hz stand-in for the computer's audio, and the server split the uploaded pieces into the microphone on the left and 660 Hz on the right;
  - two synthesized voices, one per channel, were transcribed by large-v3-turbo into a correctly attributed, time-ordered transcript.
- Not verified: the desktop app's loopback on Windows and macOS hardware, and a real call.
