---
title: ADR 0180 — A recording's transcript tells voices apart within each channel
description: Meeting transcripts name who spoke within the microphone and within the computer's audio, with a numpy and ONNX Runtime port of pyannote's speaker-diarization-3.1 pipeline that matches pyannote's own output; each sentence goes to one voice, the microphone's echo of the call is left out by its timing, and speakers can be renamed, with the names and parts synced with the recording.
---

- Status: Accepted
- Date: 2026-11-11
- Amends:
  - [ADR 0131](0131-meetings-are-recorded-by-channel-and-transcribed-locally.md) — the transcript tells voices apart within each channel, not only by channel, and its speakers can be renamed
  - [ADR 0177](0177-recordings-travel-between-devices-that-both-turned-it-on.md) — a recording also carries its speakers' names and the transcript's parts, so either device can rename them
- Related: [ADR 0173](0173-a-transcript-leaves-out-the-microphones-echo-of-the-call.md) (the echo is left out first), [ADR 0178](0178-whisper-large-v3-is-the-default-transcription-model.md) (the transcription model), [ADR 0176](0176-deletions-reach-paired-devices-that-hold-the-memory.md) (a rename replaces memories on paired devices), [ADR 0095](0095-audio-files-enter-the-source-ingestion-pipeline.md) (imported audio keeps a neutral speaker), [dashboard](../dashboard.md)

## Context

ADR 0131 labels a recording's two channels: "You" is the microphone, "Others" is what the computer played. The user reported that "You" sometimes labels another person, and asked that the others be told apart instead of bunched together. A channel holds more than one voice: people in the room reach the microphone, and a call carries every remote participant in the computer's audio.

Whisper segments are not split by speaker: one segment can hold the end of one person's sentence and the start of another's.

The research before this decision recommended sherpa-onnx 2.0.1 with pyannote's segmentation-3.0 model. Its wheels are small (2.1–2.3 MB, plus sherpa-onnx-core at 9.5–16.7 MB), but sherpa-onnx-core bundles its own ONNX Runtime 1.28.2 and on Windows copies `onnxruntime.dll` into the virtual environment's `Scripts` folder, where Imprint's own ONNX Runtime also loads. The user asked whether sherpa-onnx had any quality advantage over an implementation of Imprint's own. Both were measured (Validation).

## Decision

With `audio.diarize` on (the default), transcribing a recording tells voices apart within each channel:

1. Whisper returns word timestamps for recordings.
2. The microphone's echo of the computer's audio is left out (ADR 0173).
3. Each channel is diarized by [`diarize.py`](../../imprint/diarize.py), a numpy and ONNX Runtime port of pyannote.audio 3.4.0's `SpeakerDiarization` pipeline in the `pyannote/speaker-diarization-3.1` configuration. The port uses segmentation-3.0 (MIT) and WeSpeaker ResNet34-LM (CC-BY-4.0), both as ONNX exports pinned by revision, size and SHA-256 and downloaded on first use (about 32 MB together). It keeps pyannote's clustering threshold (0.7045654963945799) and minimum cluster size (12).
4. Each word takes the voice whose turn it overlaps most, or else the nearest turn within 0.5 s, or else the voice around it. Each sentence then goes to the voice its words hold longest. Sentences end at Whisper's punctuation and at the segment's end. A segment that one voice said keeps its text. A segment split between voices is rebuilt from its words and redacted again.
5. On the microphone, a voice that speaks while the computer's audio speaks for at least 70% of its time is that audio heard through the speakers. Its parts are left out and counted with the echoes of ADR 0173. This applies only when the computer's audio was recorded.

Default names follow the channel:

- On the microphone, of the voices that remain, the one with the most speech is "You", and any others are "In the room 1", "In the room 2" and so on.
- In the computer's audio, a single voice is "Others", and several are "Speaker 1", "Speaker 2" and so on, by when each first speaks.

Each speaker has a stable key (`mic-1`, `call-2`, …). The recording keeps the keys and names in its details and the transcript's parts in `segments.json`.

On the Recordings page the user can rename speakers. A rename writes the transcript again from the saved parts, without transcribing again, and replaces its memories here and on paired devices (ADR 0176). With recording sync on (ADR 0177), the names and the parts travel with the recording. Arriving names must have a known key form and are cleaned like a typed name; arriving parts must have the shape renaming reads.

If diarization cannot run (a model download fails, say), or `audio.diarize` is off, the transcript labels the two channels "You" and "Others", as before.

## Rejected alternatives

- **sherpa-onnx's offline diarization with the same two models.** At every threshold tried it failed:
  - At 0.5 it scored a diarization error rate (DER) of 48.9% on pyannote's `sample.wav`, against 12.7% for pyannote. It found 2, 1 and 3 speakers in three two-speaker clips, 2 in a four-speaker clip, and split a one-speaker recording into 4.
  - At 0.7 and 0.9 the English clips collapsed to one speaker.

  It also brings a second ONNX Runtime into the same environment, so it would need a child process of its own.
- **pyannote.audio itself.** It needs PyTorch, a far larger install than ONNX Runtime, which Imprint already ships. It ran at 0.42× real time on the same 10 minutes the port runs at 0.068×.
- **Diarizing the two channels mixed.** The channels already separate this computer's microphone from what it played. Telling voices apart within each channel keeps "You" on the microphone and lets one person on a call never be mistaken for the user. This option was not measured.
- **Assigning whole Whisper segments to voices.** A segment can span two speakers, and its words would then go to one of them.
- **Assigning each word to its own voice.** This was the first version, and it was run on the user's meeting. pyannote's turns sometimes change voice a second into a sentence: one call turn ends at 250.03 s, inside "Mă gândesc și eu la chestia asta, apropo de asta…". Each such sentence was split between Speaker 1 and Speaker 2.
- **Leaving the echo to the text comparison of ADR 0173 alone.** On the user's meeting, the microphone's second voice had 80.4 s of turns, 95% of them while the call spoke; the user's voice had 8%. The text comparison removed 8 passages. 6 parts (10.9 s) remained as "In the room 1", because the echo had been transcribed in other words: "Și laptop-ul tău devine doar o lume de față cu teatru." for the call's "Din laptopul să devină doar o interpretață către asta".

## Consequences

- Transcribing takes longer. Diarization adds about 0.07× the channel's duration on a quiet machine, and 0.11× under load, per channel. It peaks at about 334 MiB for 10 minutes and 418 MiB for 20 minutes, in the transcription job's process.
- The first transcription downloads the two models (about 32 MB).
- The microphone's "You" is the voice that spoke most. When someone in the room speaks more than the user, the default names are swapped until the user renames them.
- A sentence of the user's whose words diarization gives mostly to the echo voice is left out with it. With each word to its voice, a lone "poți," of the user's went to that voice; as a sentence it stays with the user. A person in the room who speaks only while the call speaks would be left out too.
- A sentence two people really split goes to one of them.
- Diarization can find too many speakers. On pyannote's sample it found 3 where there are 2. The user can give two keys the same name, and their turns then merge in the transcript.
- Overlapped words go to the voice with the most overlap, so simultaneous speech is attributed to one speaker.
- Recordings transcribed before this decision have no saved parts. Their speakers can be renamed only after transcribing again.
- `THIRD_PARTY_LICENSES.md` attributes WeSpeaker ResNet34-LM (CC-BY-4.0) and pyannote's segmentation model, and `diarize.py` carries pyannote.audio's MIT notice.
- Audio files imported as sources (ADR 0095) are unchanged: their segments keep a neutral speaker.

## Invalidation conditions

- If sherpa-onnx or another maintained runtime reproduces pyannote's turns with these models, then replacing the port with it should be weighed, since the port is code Imprint maintains.
- If a later pyannote pipeline is ported and lowers DER on the user's own meetings, then it replaces this one, with `diarize.PIPELINE_VERSION` raised.
- If the microphone's "You" is often another voice, then the user's own voice should be enrolled and matched instead of taking the voice that spoke most.
- If a voice in the room is left out as the call's echo, then raise `ECHO_VOICE` or compare the voice with the call's voices too.
- If diarization's time or memory makes long recordings fail within `audio.max_duration_minutes`, then reopen the window batching and clustering bounds.

## Validation

The port was checked against pyannote.audio 3.4.0 running its PyTorch models in a separate environment, on Windows:

| Check | Result |
|---|---|
| Speaker turns, six files | identical: the DER between the two outputs is 0.0000 at a 1 ms resolution |
| Segmentation frames | identical |
| Embeddings | relative L2 difference below 1e-5 |
| Clustering | dendrograms identical to SciPy's, flat-cluster numbering included; all 63 minimum, maximum and fixed speaker-count cases match pyannote |
| Kaldi fbank | maximum absolute difference 3.9e-4 against torchaudio |
| `sample.wav` DER (no collar, overlap scored) | 12.7% (miss 2.0, false alarm 1.9, confusion 8.8), the same as pyannote's; both find 3 speakers where there are 2 |
| Three English two-speaker clips, one Chinese four-speaker clip | 2, 2 and 2 speakers; 5 on the Chinese clip, as pyannote finds |
| 10 minutes of Romanian speech, one speaker | 1 speaker; 0.068× real time on a quiet machine, 0.11× under load (pyannote: 0.42×); peak working set 334 MiB |
| 20 minutes, many speakers | 0.11× real time; 1,367 embeddings clustered in 0.2 s; peak 418 MiB including the 73 MiB input |

- `tests/test_diarize.py` covers the pipeline's steps on Windows, and with `IMPRINT_DIARIZATION_MODELS` and `IMPRINT_DIARIZATION_SAMPLE` set, the real models.
- `tests/test_recordings.py` covers word assignment, default names, the fallback, renaming and the API. `tests/test_recording_sync.py` covers the names and parts on a paired device.
- The user's meeting of 2026-11-11 (408 s, both channels), transcribed end to end on the Windows host with large-v3, in a separate data folder, with nothing stored:

  | Run | Speakers | Turns | Mid-sentence handovers | Echo passages left out | Time |
  |---|---|---|---|---|---|
  | Each word to its voice | You, In the room 1, Speaker 1, Speaker 2 | 53 | 8 | 8 | 11m22s, of which diarization 36 s and 34 s |
  | Each sentence to its voice, echo voice left out (this decision) | You, Speaker 1, Speaker 2 | 45 | 0 | 12 | 9m16s, of which diarization 52 s for both channels |

  A mid-sentence handover is a turn that ends without sentence punctuation followed by another speaker's turn that starts in lower case. The call's two voices are plausible for the call, but who said what has not been checked against the people on it. Whisper's hallucinations in quiet passages ("Nu uitați să vă abonați la canal…", "Thank you.") remain in both runs. Diarization does not cause them.
- Not run: diarization on macOS or Linux, or a transcription started from the dashboard.
