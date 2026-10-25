---
title: ADR 0071 — Each look speaks in its own voice
description: The voice.avatar look also chooses Echo's voice, the native model's speaker on both reasoning routes plus a robotic effect for Echo; a session keeps the voice it started with.
---

- Status: Accepted
- Date: 2026-10-25
- Scope: implementation; how the voices sound is unvalidated (see Validation)
- Related: [ADR 0043](0043-live-voice-and-echo-companion.md), [ADR 0054](0054-echo-looks-declare-their-motion.md), [ADR 0070](0070-companion-moves-with-the-launcher-and-keeps-options-in-settings.md)

## Context

The owner asked for selectable voices: Kai (male), Iris (female) and Echo (robot). The session that built them had one avatar mesh; the owner confirmed Kai and Iris there as voice profiles, not new avatar assets. It added a separate profile selector, kept in browser storage and sent with each session request.

At the same time the integration branch gave Echo three looks with the same names, chosen through the `voice.avatar` setting ([ADR 0054](0054-echo-looks-declare-their-motion.md)): Echo, a holographic core; Iris, a female head; Kai, a male head.

Speech had one voice. Chat replies were read aloud with "Use the US female voice", and the small model's interleaved replies had no speaker instruction. The native model takes speaker instructions; `lfm_audio.VOICES` names its US and UK female and male speakers.

Two selectors with the same three names would let Kai's face speak with Iris's voice, and would keep one character in two places: the setting and browser storage.

## Decision

**The look is the character.** `voice.avatar` chooses the look and the voice. `voice_api.VOICES` gives each look the native speaker and an effect the dashboard applies:

| Look | Speaker | Effect |
| --- | --- | --- |
| Echo | US male | robot |
| Iris | US female | none |
| Kai | US male | none |

A look without an entry speaks as Echo. A test keeps the table's looks equal to the setting's choices.

**A session keeps the voice it started with.** Creating a session reads `voice.avatar` and pins its voice in the one-use capability with the workspace and the reasoning engine; the request body is unchanged. The stream's `ready` event reports the `voice` and its `effect`. Both routes ask the native model for the speaker: "Perform TTS. Use the {speaker} voice." for Chat replies and "Respond with interleaved text and audio. Use the {speaker} voice." for the small model.

**Echo's effect is applied to reply audio only.** When the session reports `robot`, the dashboard applies a continuous 70 Hz ring modulation with light quantization to reply PCM. It keeps every sample, so playback length and scheduling are unchanged, and continues its phase across streamed chunks. Microphone audio is never processed.

**Changing the look or the reasoning model starts a new session.** During a conversation, either change ends the session and starts another once the old one has released the speech worker: the client polls the status's `busy` flag for up to eight seconds, then creates the new session. Rapid changes keep only the latest. The conversation's transient history starts over.

## Rejected alternatives

- **A separate voice selector**, as first built. It offers the same three names twice, stores one character in two places, and allows a look and voice that do not belong together. One choice keeps them together, and setup, Settings, the Voice page and the companion offer the same list.
- **Sending the voice in the session request.** The client would restate a setting the API already holds; reading it when the session is created keeps one source.
- **Separate speech models or voice assets.** The native model already takes speaker instructions, so no new weights or dependencies are needed.
- **Reading the look on every turn.** The voice would change in the middle of a session without a restart, but the session's capability would no longer describe it; a new session is as quick and the history is transient anyway.

## Consequences

- Installations on the default look now hear Echo's robotic male voice instead of the earlier US female voice; Iris keeps that voice.
- Echo and Kai share the US male speaker and differ only by Echo's effect.
- A change of look or model during a conversation clears its short history and may wait up to eight seconds.
- Starting voice while another window holds the worker now waits those eight seconds before saying so.
- Whether the small model keeps the speaker through interleaved replies depends on the model.

## Invalidation conditions

- If listening tests show the small model ignores the speaker instruction in interleaved replies, that route needs another way to choose the speaker, or per-look voices are limited to Chat replies.
- If people want a look with another look's voice, or one look gains several voices, the voice becomes its own setting.
- If listening tests find Echo's robotic effect hard to understand, Echo uses a milder effect or none.
- If the API learns to hand the speech worker from one session to the next itself, the client's wait on `busy` can go.

## Validation

- `tests/test_voice.py` passed: every look has a voice and an unknown look speaks as Echo; the configured look is pinned to the capability; on both reasoning routes and for every look the `ready` event reports the voice and effect and the native model is asked for the look's speaker.
- `imprint/ui/scripts/voice-audio.test.mjs` passed: the robotic effect keeps every sample, stays within range and gives the same result whether a reply arrives in one chunk or two.
- Playwright against the production dashboard build passed: changing the model and then the look during a conversation waits while the worker is busy, then creates one new session with the latest choice.
- Not executed: the native model speaking with each speaker instruction, and listening tests of each voice, of speaker consistency in interleaved replies and of the robotic effect.
