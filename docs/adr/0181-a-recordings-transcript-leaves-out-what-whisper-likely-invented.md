---
title: ADR 0181 — A recording's transcript leaves out what Whisper likely invented
description: A meeting transcript drops the sentences Whisper's own signals mark as invented, by the anomaly score of their words or by a language the rest of the recording does not use decoded with little confidence, and says how many it dropped; a recording that mixes languages keeps each one it uses.
---

- Status: Accepted
- Date: 2026-11-11
- Amends: [ADR 0131](0131-meetings-are-recorded-by-channel-and-transcribed-locally.md) — the transcript leaves out the sentences Whisper likely invented
- Related: [ADR 0173](0173-a-transcript-leaves-out-the-microphones-echo-of-the-call.md) (the echo left out), [ADR 0180](0180-a-recordings-transcript-tells-voices-apart-within-each-channel.md) (voices; the sentences this record reuses), [ADR 0178](0178-whisper-large-v3-is-the-default-transcription-model.md) (the model), [ADR 0095](0095-audio-files-enter-the-source-ingestion-pipeline.md) (imported audio is unchanged), [dashboard](../dashboard.md)

## Context

The user's meeting transcript of 2026-11-11 held text nobody said:

- outros Whisper learned from video subtitles: "Nu uitați să vă abonați la canal, să vă mulțumim și la rețeta următoare!", "Să ne vedem la următoarea mea rețetă!" and "Thank you.";
- fragments in other languages in a Romanian conversation: "Да.", "accettare ok", "não é?".

Whisper large-v3 is known to do this on audio without speech. A 2025 study found that 40.3% of its outputs on non-speech audio were hallucinations ([Barański et al., ICASSP 2025](https://arxiv.org/abs/2501.11378)). Imprint already removes silence with Silero VAD before Whisper ([ADR 0096](0096-cpu-whisper-runtime-for-audio-imports.md)), but these passages came from noise, the call's echo and short sounds that passed it.

The user ruled out detecting the language once per recording or channel: their conversations mix Romanian and English.

## Decision

Before the echo is compared (ADR 0173), each segment of a recording is split into sentences by Whisper's punctuation (as in ADR 0180), and a sentence is left out when either:

1. **Its words are anomalous.** Whisper's word anomaly score is the one faster-whisper's `hallucination_silence_threshold` uses: 1 for a word with probability under 0.15, plus the amount a word is shorter than 0.133 s (×15) or longer than 2 s. A sentence is left out when its score, over its first 8 words, reaches 0.65 per word (`ANOMALY_SHARE`).
2. **It is in a language the recording does not use, and Whisper was unsure of it.** Its window's detected language holds less than 15% of the recording's words (`LANGUAGE_SHARE`), and its segment's average log-probability is below −0.5 (`UNSURE`). A recording that mixes Romanian and English holds both above 15%, so neither is foreign to it.

A segment that loses some sentences is split around them, and its kept text is redacted again. Segments without word timings are kept. The transcript's header says how many sentences were left out, and `segments.json` keeps the count, so a speaker rename (ADR 0180) writes it again.

Whisper returns each word's probability and each segment's average log-probability for recordings. Imported audio files (ADR 0095) are unchanged.

## Rejected alternatives

- **Detecting the language once per recording or channel.** The user rejected it: a conversation can mix Romanian and English, and a fixed language would mistranscribe the other.
- **A list of known invented phrases** (the "bag of hallucinations" of Barański et al.). It needs a list for each language, and it would also drop a "Thank you." that someone said. On the user's meeting, Whisper's own signals caught every invented passage without a list.
- **Speech activity from the voice turns (ADR 0180).** "Thank you." had no speech turn under it, but "Nu uitați să vă abonați…" lay 77% within turns: it was invented over the call's echo. Real short replies ("Asta?", "Da?", "Mm-hmm") had less than half of their time within turns. It would also need diarization to be on.
- **A segment's average log-probability alone.** "Nu uitați să vă abonați…" scored −0.35, within the range of real speech (−0.10 to −0.46).
- **faster-whisper's `hallucination_silence_threshold`.** It uses the same anomaly score inside decoding, but it removes a segment only when silence longer than the threshold surrounds it. Imprint's VAD has already cut silence before Whisper. It was not measured.

## Consequences

- A real sentence that Whisper transcribed with stretched or improbable words is left out too. The header counts what was left out, and the audio stays to listen to.
- A short real reply in a language the recording barely uses, which Whisper was also unsure of, is left out. On the user's meeting that was one "Mm-hmm" that Whisper took for English.
- Invented text that Whisper wrote with confident, well-timed words in the recording's own language stays.
- The thresholds rest on one meeting: the invented passages scored 0.71 to 1.77 per word, and the highest real sentence 0.55.

## Invalidation conditions

- If a real sentence is left out on another recording, then raise `ANOMALY_SHARE` toward the gap measured here (0.55 to 0.71), or require both rules.
- If invented passages remain in recordings with confident, well-timed words, then add the bag of hallucinations for those phrases only, with speech activity to keep phrases someone said.
- If Whisper or the VAD is replaced, then measure the thresholds again.

## Validation

Whisper large-v3, with Imprint's options, was run on the user's meeting of 2026-11-11 (408 s, both channels, Windows host), keeping each word's probability and each segment's average log-probability. `without_invented` then ran on that output:

| Sentence left out | Channel | Rule | Score per word | Window language, log-probability |
|---|---|---|---|---|
| Nu uitați să vă abonați la canal, să vă mulțumim și la rețeta următoare! | microphone | anomalous words | 1.77 | ro, −0.35 |
| Să ne vedem la următoarea mea rețetă! | microphone | anomalous words | 0.71 | ro, −0.36 |
| Да. | microphone | foreign and unsure | 0.00 | ru, −0.64 |
| accettare / ok | computer | foreign and unsure | 0.00 | it, −0.56 |
| Mm-hmm, mm-hmm. | computer | foreign and unsure | 0.00 | en, −0.53 |
| Thank you. | computer | both | 0.75 | en, −0.90 |
| não é? | computer | both | 0.75 | pt, −0.92 |

- The other 125 of the meeting's 133 sentences were kept. The highest anomaly scores among them were 0.55 ("Nici eu n-am auzit dingul."), 0.50 ("Cum adică?") and 0.50 ("Bă, e o eroare la Google."). Romanian held 1,373 of the 1,384 words.
- `tests/test_recordings.py` covers both rules, a split segment, a mixed Romanian and English recording, segments without word timings, and the count in the header after a rename.
- Not run: a recording that mixes Romanian and English in practice, or any meeting other than this one.
