---
title: ADR 0184 — A recording's audio states its length and can be sought
description: Once a meeting recording stops, its WebM audio is copied packet for packet, without being encoded again, into a file with the Duration and Cues the browser's recorder never writes; recordings made before this, and audio a paired device on an older release sends, are copied the same way, and the copy is a newer version that paired devices take.
---

- Status: Accepted
- Date: 2026-11-12
- Amends:
  - [ADR 0131](0131-meetings-are-recorded-by-channel-and-transcribed-locally.md) — the stored audio is no longer the recorder's pieces as they came: once stopped, it is their copy with a length and a seek index
  - [ADR 0177](0177-recordings-travel-between-devices-that-both-turned-it-on.md) — a device that receives audio without a length copies it the same way, and that newer version goes back to the sender
- Related: [ADR 0173](0173-a-transcript-leaves-out-the-microphones-echo-of-the-call.md) (audio downloads), [ADR 0095](0095-audio-files-enter-the-source-ingestion-pipeline.md) (PyAV, already a dependency), [dashboard](../dashboard.md), [sync](../sync.md)

## Context

The user reported on 2026-11-11 that their meeting recording could not be played: on the Recordings page the player showed no total time and its bar filled fast and kept going; the downloaded `.webm` would not play or seek in Windows Media Player; on their MacBook the player stopped at once. They first suspected recording sync (ADR 0177). It was not sync: the copy on the Linux peer had the same SHA-256 as the original, and the original itself lacked what players need.

The dashboard records with the browser's `MediaRecorder` and uploads a piece every 5 seconds, which Imprint appends to `audio.webm` (ADR 0131). `MediaRecorder` writes WebM as a live stream: it never goes back to write the segment's Duration, and it writes no Cues, the index a player seeks by. On the user's recording (4,946,802 bytes, 408.7 s of stereo Opus):

- PyAV reported no container duration, and the file had neither a Duration element nor a Cues element.
- Chrome reported `duration` as `Infinity`.
- Windows Media Foundation, which Windows Media Player uses, opened it with a duration of `TimeSpan.MaxValue`.

The sound itself was intact: it decoded in full, and Chrome played it.

## Decision

A finished recording's audio is copied into a new WebM file, packet for packet and without decoding, by PyAV's Matroska muxer, which writes the Duration and the Cues. The copy replaces `audio.webm` only when it states a length and the audio did not change while it was made; otherwise the audio stays as it was. Audio that states its length already is left alone. `recordings.index_audio` does this:

- **when a recording stops**, including an interrupted one finished from the list;
- **when the dashboard's API starts**, for every finished recording (`stopped`, `ready`, `failed`) that lacks it, so recordings made before this get it (`recordings.index_all`);
- **when a recording's audio arrives from a paired device** and lacks it, as audio from a release before this does.

The copy is a newer version of the recording (its `updated_at` moves on), so paired devices that sync recordings receive the new audio whole (ADR 0177). Received audio that cannot replace the audio held, as when Windows refuses because the file is open, is dropped and sent again by the next sync. A copy that cannot replace the audio is dropped too, and the next start tries again.

## Rejected alternatives

- **Encoding the audio again into a format every player reads, such as AAC in M4A.** It would change the sound the user recorded and costs encoding time on every recording, while copying keeps every packet as recorded and took 0.02 s for the user's 4.9 MB. Chromium and Windows Media Foundation both read the copied WebM's length (see Validation). It is reopened only if a player the user relies on cannot play the copy.
- **Fixing the file in the browser before upload**, as recorder libraries patch the Duration. The recorder uploads a piece every 5 seconds so that a closed window keeps what was sent (ADR 0131); the header went out in the first piece, and the browser never holds the whole file. Such patches also add no Cues, so seeking would stay unreliable.
- **Fixing the audio only when it is played or downloaded.** The file stored and synced would stay without a length, and every reader, the player, a download and a paired device, would need to go through the fix. Rewriting the stored audio once fixes all of them.

## Consequences

- Players show a recording's length and seek in it. The download of the user's recording opens in Windows' media stack with its length.
- The audio's bytes change once: its packets are the same, but its SHA-256 and size differ (+1,562 bytes on the user's recording). Each paired device that syncs recordings receives the audio whole once more. A device on an older release receives it as any newer version and keeps it.
- A recording interrupted mid-piece keeps every packet that can be read; a last block cut off, which no player could play either, is not copied.
- On Windows, audio open in another process, being played or transcribed, cannot be replaced; the copy waits for the next start of the dashboard's API, or the next sync for received audio.
- PyAV is already part of every install (ADR 0095). Where it cannot load, nothing changes.

## Invalidation conditions

- If the browser's `MediaRecorder` starts writing the Duration and Cues, or the recorder stops recording in the browser, then indexing is a no-op for new recordings and can be retired once older recordings have been indexed.
- If a player the user relies on, such as Safari on macOS, cannot play the copied WebM/Opus file, then reopen the re-encoding alternative for that player.
- If indexing at stop measurably delays stopping (0.02 s for 4.9 MB), then move it into the transcription job, which runs right after.

## Validation

Executed on Windows 11 with the installed runtime's Python 3.13 and PyAV 19.0.2:

- `tests/test_recordings.py` and `tests/test_recording_sync.py` (34 tests) passed: a recording whose audio arrives in pieces without a length gets one at stop, with the same packets and times; the startup sweep indexes earlier recordings and leaves audio that is being recorded, transcribed or not WebM as it was; a replace refused as on Windows leaves the audio and its version unchanged; audio from an older release is indexed on arrival, goes back whole, and both devices then hold the same version; received audio that cannot replace the held audio is sent again.
- On a copy of the user's recording: 6,812 packets before and after, 408.7 s stated, Cues present, 0.02 s. Chrome (headless) reported `duration` 408.746 and seekable to 408.7, against `Infinity` before; Windows Media Foundation opened it with 00:06:48.746, against `TimeSpan.MaxValue` before.
- The same file cut at 50%, 73% and 99.9% of its bytes was indexed with all 74, 110 and 150 packets that could be read.

Not verified: macOS (PyAV 15.1.0 on Apple silicon) and Linux runtimes; playback of the copy in Safari and in the macOS desktop app; the Windows Media Player app itself, beyond the Media Foundation source it uses.
