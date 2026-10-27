---
title: ADR 0088 — Bytes a crash never wrote are counted omissions
description: A conversation reading drops the NUL bytes an interrupted write leaves in a transcript and counts each run among its omissions, instead of failing the whole transcript as malformed.
---

- Status: Accepted
- Date: 2026-10-27
- Amends: [ADR 0082](0082-retain-readable-conversation-evidence.md) — a line made malformed only by NUL bytes no longer fails the reading
- Related: [ADR 0080](0080-conversations-are-relearned-when-their-reading-changes.md), [ADR 0081](0081-verifiable-redacted-source-snapshots.md)

## Context

ADR 0082 fails a malformed transcript rather than claim a complete reading of it. On the owner's machine 9 of 112 Claude Code transcripts in WSL failed that way (`Malformed transcript JSON at line N`), so none of their content was ever learned. Each had exactly one bad line, and every one was a run of NUL bytes: 384 to 3,861 bytes long, in the middle of a transcript or at its end. In two of them a valid record followed the run on the same line.

That is what a crash leaves behind: the file system had allocated a write's bytes but never wrote them, so they read back as zeros, and the client went on appending afterwards. JSON text never contains a raw NUL; a string escapes it as `\u0000`. The bytes are not a damaged record but the absence of one, which no reading can recover.

## Decision

`transcripts.records` drops NUL bytes from a JSONL line before parsing it. A record that follows a NUL run is read. Each line that held NUL bytes counts in the reading's `omissions['zero_filled_lines']`, next to the opaque reasoning and binary attachments a reading already counts. A line that is still not JSON fails a strict reading, as ADR 0082 decided.

## Rejected alternatives

- **Keep failing.** A crash during one write made the rest of the conversation unlearnable, so 9 conversations contributed nothing.
- **Skip every line that does not parse.** That would also hide format changes and genuine corruption, which ADR 0082 fails on purpose.

## Consequences

- All 112 transcripts are read, giving 15,056 chunks; earlier the same day the 103 that could be read gave 13,652.
- A reading declares how many unwritten regions it skipped, so it still never claims to be an exact copy of its transcript.
- Readings of the affected transcripts change, so ADR 0080 learns them again.

## Invalidation conditions

- If a supported client writes NUL bytes as part of its records, they are content; read them instead of dropping them.
- If damage other than unwritten bytes turns out to be common, decide separately how a reading should treat it.

## Validation

- `tests/test_source_snapshots.py`: a NUL-only line and a NUL run followed by a record give both surrounding records, no NUL in the reading and two counted omissions; text that is still not JSON fails.
- The owner's 112 WSL transcripts were read again with this change: none failed.
