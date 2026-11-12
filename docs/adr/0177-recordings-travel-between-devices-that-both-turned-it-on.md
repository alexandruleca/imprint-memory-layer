---
title: ADR 0177 — Recordings travel between devices that both turned it on
description: With sync.recordings on at both ends, off by default, a finished meeting recording's audio, details and transcript reach paired devices by themselves, the audio in resumable checked 1 MiB pieces, and deleting a recording or its audio on one device does the same on the other.
---

- Status: Amended
- Date: 2026-11-11
- Amends:
  - [ADR 0131](0131-meetings-are-recorded-by-channel-and-transcribed-locally.md) — the raw audio can leave the device: to paired devices where the user turned recording sync on, both of them
  - [ADR 0176](0176-deletions-reach-paired-devices-that-hold-the-memory.md) — deletions also carry deleted recordings, kept once per device rather than per workspace
- Amended by:
  - [ADR 0180](0180-a-recordings-transcript-tells-voices-apart-within-each-channel.md) — a recording also carries its speakers' names and the transcript's parts, so either device can rename them
  - [ADR 0184](0184-a-recordings-audio-states-its-length-and-can-be-sought.md) — a device that receives audio without a length copies it the same way, and that newer version goes back to the sender
- Related: [ADR 0116](0116-paired-devices-send-only-what-the-other-lacks-in-stored-chunks.md) (stored chunks), [ADR 0018](0018-single-sync-implementation-and-end-to-end-encryption.md) (encryption), [ADR 0168](0168-skills-and-personas-are-kept-once-and-synced-to-paired-devices.md) (data that belongs to no workspace), [sync](../sync.md)

## Context

A meeting recording's audio stays on the device that made it (ADR 0131); only the transcript's memories sync. The user asked for the audio to sync too, through the relay, securely. They chose:

1. opt-in, on both devices;
2. once opted in, automatic;
3. deleting a recording on one device deletes it on the other.

Recordings are large, from a few MB to about 100 MB for two hours. Sync sends records in streams, each acknowledged before the next (ADR 0116). An attempt ends after ten minutes without progress, the relay caps a message at 80 MiB and the peers cap a frame at 64 MiB, and nothing resumed from a byte offset. The transport is already end-to-end encrypted, with fresh keys per session and authenticated, ordered frames (ADR 0018). The relay sees only ciphertext, but it does see sizes, timing and the pair's room.

## Decision

**A setting, `sync.recordings`, off by default**, under Settings → Device sync. A device offers recordings in its handshake only when it is on, and sends them only to a device that offers them, so both must turn it on. Recordings hold other people's voices; the consent given for recording is not consent to copy, so copying is the user's choice on each device. Once on, finished recordings travel by themselves with every sync. Recordings belong to the device, not to a workspace, so every paired workspace carries them, like skills (ADR 0168).

**The manifest** lists each finished recording (stopped, transcribed or failed) by id, with the time it last changed. Every change stamps that time, deleting the audio included. Two extra entries tell the sender what to send: `<id>#audio`, the size of the audio held, and `<id>#part`, the bytes of a download in progress.

**The audio travels in 1 MiB pieces**, each its own stored and acknowledged stream, counted in bytes so progress keeps the attempt alive. It starts where the other device's partial file ends, or not at all when the other device holds the audio already. The receiver appends the pieces to a partial file and keeps it as the recording's audio only when its size and SHA-256 match. A piece out of order or a mismatch is dropped and logged, not failed, so the rest of the sync goes on and the next one resumes from what is held. The details are checked before they are written: id format, field types, a status that can arrive, and a project name made safe. A recording larger than `audio.max_file_size_mb` is not received.

**Deletions.** Deleting the audio is a newer version of the recording with no audio, so the other device deletes its copy too. Deleting the recording records a deletion as ADR 0176 does, kept once for the device. The other device removes its folder when it started before the deletion and never stores the recording again.

## Rejected alternatives

- **Sending the whole file as one record.** One 100 MB record exceeds the frame and relay limits. Without progress for its duration, a slow upload ends the attempt, and a dropped connection starts it over.
- **On by default, or on when either device turns it on.** It would copy other people's voices to a device whose user did not choose it.
- **Re-sending the audio whenever the recording changes.** A transcription run again changes only the details and the transcript; the audio is the same.
- **Failing the sync on a bad piece.** A recording that keeps failing would also block the memories of that pairing.

## Consequences

- With it on at both ends, a call recorded on one device can be heard, downloaded or transcribed again on the other.
- Large first transfers take as long as the link needs; they resume across attempts.
- The relay still learns roughly how long a recording is and when it travelled, from sizes and timing.
- Audio received counts toward the receiving device's disk; no total cap exists besides the per-file limit.
- A device on an older release neither offers nor receives recordings.

## Invalidation conditions

- If recordings fill disks in practice, add a total cap per device and a choice of which recordings to keep.
- If users want only some recordings shared, add a per-recording choice on top of the setting.
- If the relay's view of sizes and timing matters, pad the pieces or send cover traffic.

## Validation

- Windows 11, Python 3.13 (the installed runtime's venv). `tests/test_recording_sync.py`, with two data folders and a piece size of 4 bytes, covers:
  - what the manifest lists, and nothing when the setting is off;
  - a recording arriving whole in pieces and an interrupted download resuming;
  - audio the other device holds not being sent again;
  - deleted audio, and a deleted recording, going on the other device, and a deleted recording not being stored again;
  - corrupt, out-of-order and unsafe pieces being refused without failing;
  - pieces passing through sync's own stream format and importer.
- The sync tests and the full suite pass.
- Not run: a transfer between two real installations through the relay, or a 100 MB recording.
