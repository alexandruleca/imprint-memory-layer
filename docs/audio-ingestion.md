---
title: Audio ingestion
description: Local multilingual transcripts, timestamps, caching and queued file ingestion.
---

Audio files use the same ingestion and refresh pipeline as documents. Import a
recording with **Add to memory → Audio** on the dashboard, select its server-side
path and choose **Run**. It runs in the existing queue with progress and Cancel.
The file must be inside an approved workspace root, as for documents.

```bash
imprint ingest /path/to/meeting.m4a
imprint ingest /path/to/recordings
imprint refresh /path/to/recordings
```

Supported audio extensions are WAV, MP3, M4A, AAC, FLAC, OGG, Opus, AIFF/AIF and
WMA. Folder ingestion includes them while `audio.enabled` is true (the default).
This version accepts local file paths, not audio URLs or browser uploads. Video
files and automatic speaker diarization are not implemented.

## Models and languages

**Settings → Audio ingestion** controls the model, language and file limits.
The default **Large v3 Turbo** downloads approximately **1.51 GiB** once. The
lighter **Small** model downloads approximately **464 MiB**, with lower observed
accuracy on the Romanian smoke fixture. Opening the dashboard/settings never
starts a download; using an uncached audio file does. Weights are shared across
workspaces and stored under `models/` in the Imprint data directory. Recordings
and decoded PCM are never copied into that model cache.

Both models are multilingual Whisper models, run locally on CPU with int8
inference. PyAV's wheels include the decoder libraries: no system FFmpeg,
compiler, CUDA toolkit or microphone permission is required. Network access is
used to fetch the selected pinned model on first use; transcription itself
uses local files only. Missing Python runtime packages produce an instruction
to run `imprint bootstrap`. Release installation/update installs the locked
runtime dependencies; model weights remain separate downloads.

**Auto** detects the language of each decoding window. **Romanian** (`ro`) and
**English** (`en`) pin it explicitly. Output preserves the recognized language;
it is not translated into English. Auto can also recognize other languages
supported by the multilingual model. Mixed-language content within one window
can still be misrecognized; the reported language describes that window.

```bash
imprint config set audio.language ro
imprint config set audio.model small
imprint config set audio.enabled false
```

## Transcripts and source reading

Each recognized segment becomes a timestamped conversation event with a neutral
`speaker` role, language and relative start/end seconds. **Unidentified speaker**
means the recognizer has not identified who spoke. It never invents alternating
user/assistant turns, speaker names or a recording date. The source reader shows
the recording filename, segment text, language and time ranges. Original media
playback/seeking is not supplied by this version.

Transcription is an estimate of the recording, not an exact transcript. Native
checks recognized Romanian and English, but a Romanian fixture still contained
a numeral error even with the larger model. Review names, numbers and important
quotes against the recording before relying on them. These small functional
checks do not establish multilingual accuracy or speaker separation.

The pipeline redacts transcript text before indexing and stores the canonical
transcript in the existing source snapshots. That completed snapshot is also
the transcript cache, scoped to its workspace and the file hash/transcription
profile. Identical files can reuse a completed transcript in the same workspace.
Changing bytes, model, language or relevant runtime/settings invalidates reuse.
`imprint ingest ... --force` bypasses the transcript cache and transcribes again.
Deleting/purging a source removes that source's snapshot, so it cannot supply a
cached transcript afterward. Another retained source for identical bytes may
still have its own snapshot. Raw media is not added to memories or sync; its
original location remains provenance.

## Limits, progress and failure

The defaults are **1,024 MiB** per file and **120 minutes** of decoded audio.
Change `audio.max_file_size_mb` and `audio.max_duration_minutes` in Settings or
with `imprint config set`. Limits must be positive. Decode duration is checked
against metadata and actual decoded samples.

The decoder keeps 30-second windows in memory, with a one-second overlap. Word
timestamps remove repeated words at window joins. This bounds decoded-waveform
memory; transcript text is limited to 2,000,000 characters and 50,000 segments.
One transcription model runs at a time in the ingest process. Progress shows
actual model-download bytes, model loading, and transcription seconds; unknown
duration uses an indeterminate indicator. Cancellation uses the queue's existing
owned process-group termination, including its force-stop escalation for native
inference.

Failed decoding, model loading, checksum checks or transcription retain the prior
indexed source and make ingestion fail visibly. No partially generated transcript
is published. No recognized speech is an explicit “nothing to index” result;
previous chunks are retained under the existing document no-text policy.

The decoder accepts audio demuxers and local file/pipe protocols only. Playlist
demuxers and external MP4 data references are disabled. Model assets are
revision- and SHA256-pinned; interrupted downloads remain partial model files
and are replaced on retry. A corrupt completed weight file produces a repair
error and is never loaded.

## Validation

Run the ordinary Python tests, plus an optional native check with your own short
English and Romanian fixtures:

```bash
PYTHONPATH=. /path/to/runtime/python scripts/smoke-audio-ingest.py \
  --english /path/to/english.wav --romanian /path/to/romanian.wav \
  --model-cache /path/to/dedicated/transcription-model-cache
```

The native check uses a disposable data directory, verifies model assets and
checks both languages plus a mixed recording across a window boundary. It does
not save or print transcripts. Run with each operating system's native Python
before claiming that platform validated. Linux/WSL execution and remaining gates
are recorded in [platform validation](platform-validation.md).

[ADR 0095](adr/0095-audio-files-enter-the-source-ingestion-pipeline.md) records the
source/caching semantics. [ADR 0096](adr/0096-cpu-whisper-runtime-for-audio-imports.md)
records the runtime and model policy.
