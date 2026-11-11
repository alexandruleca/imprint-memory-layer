---
title: ADR 0173 — A transcript leaves out the microphone's echo of the call
description: A recording's transcript drops the microphone passages that repeat what the computer played at the same moment, says how many it left out, and puts each turn in a paragraph of its own.
---

- Status: Accepted
- Date: 2026-11-11
- Amends: [ADR 0131](0131-meetings-are-recorded-by-channel-and-transcribed-locally.md) — the transcript leaves out the microphone's echo of the computer's audio, and each turn is a paragraph
- Related: [ADR 0095](0095-audio-files-enter-the-source-ingestion-pipeline.md) (the transcription pipeline), [dashboard](../dashboard.md)

## Context

The first live meeting recorded with ADR 0131 (seven minutes, a call played through the computer's speakers) was hard to read in the dashboard for two reasons.

- The transcript put one turn per line. Markdown joins single line breaks into one paragraph, so the viewer showed the whole meeting as one block of text.
- From minute five, most of what the others said appeared twice: once from the computer's audio ("Others") and, seconds later and worded a little differently by Whisper, from the microphone ("You"), which heard the speakers. The recorder asks for echo cancellation (`imprint/ui/src/lib/recorder.ts`), but Chromium cancels only the audio its own process plays, not a call in another application.

Measured on that transcript's 47 turns: for each microphone turn, the share of its words also found, in order, in what the computer played within ten seconds was between 0.00 and 0.29 for the turns that were the user's own speech, 0.40 to 0.53 for four turns that mixed the user's words with an echo or were an echo Whisper garbled badly, and 0.62 to 1.00 for the five clear echoes.

## Decision

Before the transcript is written, a microphone segment of four or more words is left out when at least 60 % of its words, in order, appear in what the computer played between five seconds before it and five seconds after it. Shorter microphone segments are always kept: a "yes" said while the others say "yes" is not an echo. The transcript's opening line says how many passages were left out. Each turn is a paragraph of its own. The dashboard's transcript viewer shows transcripts made before this change one turn per paragraph too.

The audio is unchanged, so **Transcribe again** gives any recording the new transcript.

## Rejected alternatives

- **Echo cancellation at capture.** Already requested; it cannot remove audio another application plays, which is the case that produced the echo.
- **Plain text similarity between turns (a ratio over both turns' words).** Measured on the same transcript, it scored a clear echo (0.29) below two turns that mixed the user's words with an echo (0.34 and 0.40), because the two channels group speech into turns differently and a ratio counts the longer side's extra words against it. Containment of the microphone's words in the computer's audio separated them.
- **A lower threshold, so turns that mix speech and echo go too.** They also hold what the user said; at 0.40 to 0.53 they overlap the real speech's range closely enough that dropping them would lose the user's words. Keeping them leaves some echoed words in.

## Consequences

- Transcripts of calls played through speakers read once per thing said; the memories made from them hold less duplicated text.
- A microphone passage that mixes the user's words with the echo stays, echo included.
- If the user reads aloud what the call is saying at that moment, those words can be left out as an echo; the computer's audio still holds them.
- The threshold was tuned on one Romanian meeting. Whisper's wording of an echo differs more in some languages and rooms than others.
- Whisper's invented lines in near-silence (a sign-off phrase, a word in another language) are not addressed here; setting `audio.language` to the meeting's language prevents the ones in other languages.

## Invalidation conditions

- If capture can cancel other applications' audio (system-wide echo cancellation, or a loopback reference the browser accepts), cancel the echo at capture and drop this step.
- If transcripts show real speech left out, or pure echoes kept, across more meetings, retune the share, the window or the minimum length from those transcripts.

## Validation

- Windows 11, Python 3.13 (the installed runtime's venv): `tests/test_recordings.py` covers the echo rule (an echo left out; a reply, a short passage and the same words said later kept), the count in the opening line, and paragraphs per turn; the full suite passes.
- Applied to the turns of the live meeting's transcript (turn times standing in for segment times): it leaves out exactly the five pure echoes and none of the user's turns.
- Not run: a re-transcription of the live meeting with this change, since the installed runtime is the previous release.
