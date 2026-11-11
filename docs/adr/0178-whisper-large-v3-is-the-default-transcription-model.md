---
title: ADR 0178 — Whisper large-v3 is the default transcription model
description: Transcription defaults to the full Whisper large-v3 conversion, pinned like the others, for fewer mistakes in Romanian at about twice Turbo's time on a CPU; Large v3 Turbo and Small stay as choices.
---

- Status: Accepted
- Date: 2026-11-11
- Amends: [ADR 0096](0096-cpu-whisper-runtime-for-audio-imports.md) — the default model is large-v3; Large v3 Turbo becomes a choice beside Small
- Related: [ADR 0095](0095-audio-files-enter-the-source-ingestion-pipeline.md) (the transcription pipeline), [ADR 0131](0131-meetings-are-recorded-by-channel-and-transcribed-locally.md) (recordings), [audio ingestion](../audio-ingestion.md)

## Context

Meetings recorded in Imprint are mostly in Romanian. Read after the first live meeting, the Large v3 Turbo transcript had mistakes in names and words. The user asked for a more accurate model, even if slower, as the default, with the other two still offered.

Whisper Turbo keeps large-v3's encoder but cuts the decoder from 32 layers to 4. OpenAI's language breakdown for Turbo (openai/whisper discussion 2363) gives these word error rates for Romanian:

| | Common Voice 15 | FLEURS |
| --- | --- | --- |
| large-v3 | 10.8 | 8.2 |
| Turbo | 16.1 | 9.5 |

That is about a third fewer mistakes on Common Voice, whose varied microphones are the closest of these to meetings, and about 14% fewer on FLEURS. English differs little (9.3 against 10.6, and 4.1 against 4.4).

## Decision

Add `large-v3` to `audio.model`, pinned like the other models in `audio_ingest.py`: `Systran/faster-whisper-large-v3` at revision `edaa852ec7e145841d8ffdb056a99866b5f0a478` (MIT, ungated, the conversion faster-whisper's own `large-v3` name points to), with the size and SHA-256 of `model.bin`, `config.json`, `preprocessor_config.json`, `tokenizer.json` and `vocabulary.json`. It is the default. Large v3 Turbo and Small stay as choices. Inference is unchanged: CPU, int8, four threads, beam 5, voice activity detection, word timestamps.

## Rejected alternatives

- **Keeping Turbo as the default.** It is the faster choice and stays available, but the user asked for accuracy first.
- **large-v2.** Reading OpenAI's chart, its Romanian error rates are about 13.7 (Common Voice) and 12.4 (FLEURS), worse than large-v3's.
- **Distilled large-v3 models.** English only.
- **Romanian fine-tunes.** TransferRapid's Turbo fine-tune reports a large Romanian gain, but it is CC-BY-NC-4.0, was selected on its test set and is not converted to CTranslate2. BuzzASR's large-v3 fine-tune scores about the same as plain large-v3 on FLEURS. Others found have no license. Training on one language also risks English.

## Consequences

- Recordings and audio files are transcribed with fewer mistakes in Romanian.
- Transcription takes about twice as long. Measured on this project's Windows machine (Ryzen AI Max+ 395, int8, four threads): 185 s for 180 s of Romanian read speech against 92 s with Turbo, and 33 s against 14 s for 150 s of a real call's audio. A one-hour meeting, both channels, takes on the order of an hour.
- The first transcription downloads about 2.88 GiB, against about 1.51 GiB for Turbo; memory use peaks around 3 GB.
- Installations that never chose a model switch to large-v3, so their audio files are transcribed again the next time they are indexed. Recordings keep their transcript until the user chooses **Transcribe again**.
- On a 150-second excerpt of the real call, the two models' text was close, so that excerpt neither confirms nor refutes the published gain. The gain rests on OpenAI's figures.

## Invalidation conditions

- If a GPU runtime for faster-whisper becomes available on users' machines, reconsider the speed trade-off and the int8 choice.
- If measured on the user's meetings large-v3 is not more accurate than Turbo, make Turbo the default again.
- If a permissively licensed Romanian model in CTranslate2 format proves better on held-out data, offer it.

## Validation

- `tests/test_audio_ingestion.py` covers the settings and profile; the full suite passes on Windows 11 with Python 3.13.
- The pinned files were downloaded and their SHA-256 recomputed. Both models ran on 180 s of Romanian read speech and on 150 s of the real call's computer-audio channel, with the pipeline's parameters.
- Not run: a word-error-rate measurement on the user's own meetings, for want of reference transcripts.
