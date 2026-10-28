---
title: ADR 0092 — Voice generation follows playback
description: Completed playback releases audio credit and active voice replies use an inactivity timeout.
---

- Status: Accepted
- Date: 2026-10-28
- Scope: live voice playback and generation pacing
- Amends: [ADR 0043](0043-live-voice-and-echo-companion.md) — pace streamed speech by playback progress and bound inactivity instead of total turn duration

## Context

The user reported long spoken replies cutting off. Chat speech already splits
text into requests of at most 240 characters in `imprint/voice_api.py`, but the
browser cancels when its scheduled audio exceeds 30 seconds. Faster generation
can exhaust that queue. A fixed 120-second turn budget also cancels healthy long
generation regardless of phrase splitting.

## Decision

Each turn owns a playback window measured in 24 kHz PCM samples. Before sending
another native audio chunk, wait if ten seconds of sent audio remain unplayed.
The window can exceed ten seconds by one native chunk. The client reports
cumulative samples only after scheduled buffers finish. Progress must be
monotonic and cannot exceed sent samples; reports for previous turns are ignored.
Only the separate final `played` acknowledgement commits conversation history.

Use a 120-second inactivity deadline refreshed by emitted non-provisioning
events, preserving the separate first-time chat download budget and the existing
30-minute session lifetime. Cancellation interrupts a playback wait and stops
the owned speech worker. Both reasoning routes use the same playback window;
chat retains sequential bounded TTS requests and its bounded producer queue.

## Rejected alternatives

- Add phrase splitting alone: it already exists and does not pace queued audio
  or prevent the fixed whole-turn timeout.
- Remove the browser queue guard without flow control: it permits growing audio
  buffers when generation outpaces listening.
- Start several native speech requests concurrently: the current session owns
  one speech worker, and additional production does not solve playback backlog.

## Consequences

Generated long answers can continue as the listener drains audio. The updated
dashboard must accompany the API; a client without playback reports eventually
stalls and times out. Generation can pause between chunks, and playback gaps
remain possible when synthesis runs slower than speech. Existing reasoner
context limits, native generation caps and bounded conversation history remain.
No new model, native worker build or dependency is required.

## Invalidation conditions

- If the native worker gains a different streaming format or sample rate, revise
  sample accounting before adopting it.
- If real playback measurements show the ten-second window causes avoidable gaps
  or excess buffering, re-evaluate its size using those measurements.

## Validation

On Linux/WSL, 55 Python tests (voice, chat streaming and ADR records), seven
TypeScript audio-controller tests and 26 Chromium voice tests passed. Controlled
workers, audio and clocks exercise long output, playback credit, inactivity,
cancellation and final history acknowledgement. Chromium additionally verifies
real Web Audio completion reports. TypeScript type checking, targeted lint and
the production dashboard build passed.
These checks do not establish real-model speech completeness, perceived gaps or
native Windows/macOS audio behavior; those require native listening validation.
