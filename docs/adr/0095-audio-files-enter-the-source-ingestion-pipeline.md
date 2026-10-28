---
title: ADR 0095 — Audio files enter the source ingestion pipeline
description: Local recordings become timestamped unidentified-speaker conversations using the existing source lifecycle and cache.
---

- Status: Accepted
- Date: 2026-10-28
- Scope: local audio files in ingest, refresh and the dashboard queue
- Amends: [ADR 0094](0094-the-dashboard-reads-whole-sources-from-their-snapshots.md) — render neutral-speaker audio events and title recordings by filename
- Related: [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md), [ADR 0040](0040-documents-with-nothing-to-index.md), [ADR 0081](0081-verifiable-redacted-source-snapshots.md), [ADR 0094](0094-the-dashboard-reads-whole-sources-from-their-snapshots.md), [ADR 0096](0096-cpu-whisper-runtime-for-audio-imports.md)

## Context

The user requested audio ingestion like document ingestion, initially Romanian
and English conversations, with video as a later direction. The existing
extractor/source lifecycle already enforces approved roots, content hashes,
redaction, complete-source snapshots, replacement and queue cancellation.
Transcription supplies speech text and timing; it does not establish who spoke.

## Decision

Register local audio extensions in the document extractor path and include them
in folder scans while `audio.enabled` is true. The dashboard adds an Audio action
which queues the existing `ingest` command; CLI ingestion/refresh use the same
code. Add audio language/model/size/duration settings through the central schema.

Store ASR segments as an additive form of existing `conversation` snapshots,
with `source_type=audio`, neutral `speaker` events, language and relative
`audio_start`/`audio_end` seconds. There is no inferred user/assistant identity
or speaker name. Recording filenames title these conversations. Chunk/source
spans retain complete canonical redacted text; the reader shows segments and
timing without requiring a new storage table or source-format version.

Reuse completed snapshots as the transcript cache, selected by workspace,
original file hash and transcription profile. The profile includes model
revision, decoder/inference versions, language and relevant limits/privacy
settings. `--force` retranscribes. Audio source-state checks include the profile,
so a model/language change cannot silently reuse old chunks. A separate copy of
an identical source can retain its own cache after another source is deleted.
When text is identical, reuse embeddings/classification but refresh extracted
metadata and source type alongside spans; otherwise an old audio profile would
force needless retranscription on every later refresh.

Decode bounded 30-second windows with one second of overlap. Deduplicate overlap
using word timestamps. Default limits are 1,024 MiB and 120 minutes, with
additional 2,000,000-character/50,000-segment transcript limits. Report actual
download bytes and transcription seconds through existing progress. Publish
only after transcription and source fingerprint checks succeed. Failures retain
the prior index; no recognized speech follows the existing no-text policy.
Queue cancellation stops the owned process group, with escalation for native
inference. Raw media/decoded PCM are not copied into transcript storage or sync.

## Rejected alternatives

- Store an additional transcript-cache database: duplicates personal text and
  adds deletion/workspace lifecycle paths that snapshots already provide.
- Label alternating speech as user/assistant or Speaker 1/2: transcription alone
  supplies no evidence for those identities or speaker changes.
- Decode a whole long recording into one waveform: unnecessary memory growth;
  only a bounded window is needed for recognition.
- Add video/visual ingestion in the same change: the user requested audio first;
  video track extraction and visual understanding require separate validation.

## Consequences

Audio participates in the existing source refresh, repair, purge and read paths.
There is no raw-media persistence or new microphone permission boundary.
Transcripts are machine estimates and can contain factual/numeral errors.
Window joins can still omit or misrecognize speech. Auto language metadata is
per window, not a guarantee that every word belongs to that language. The
current source reader does not play or seek the original recording. Older
clients can read the canonical text but may not render the new speaker role.

## Invalidation conditions

- If measured boundary errors lose important speech, revise windowing/merging
  and invalidate the transcription profile before regenerating sources.
- If speaker diarization is added and validated, extend the neutral speaker
  metadata using its actual evidence rather than alternating guessed roles.
- If transcription must survive an embedding failure for retry, evaluate a
  separately managed cache only with equivalent purge and workspace guarantees.

## Validation

On Linux/WSL, 79 targeted Python checks and 20 Chromium checks passed, together
with the production dashboard build, TypeScript checking, targeted lint, runtime
smoke and Go setup/runtime tests. Tests cover native bounded WAV/MP3/M4A/FLAC decoding, timestamped source
indexing, completed-snapshot reuse, forced retranscription, failed replacement,
purge, workspace isolation and updated metadata on reused vectors. Native CPU fixtures exercise English, Romanian
and a mixed recording across the decoding boundary. Browser checks cover audio
queueing, settings and timestamped source reading. Native Windows/macOS execution and broad accuracy
remain unverified.
