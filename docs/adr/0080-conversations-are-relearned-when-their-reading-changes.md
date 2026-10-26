---
title: ADR 0080 — Conversations are learned again when their reading changes
description: Learning replaces a conversation's memories with what reading it gives now, keeping chunks already stored, and the watermark names the learner version so a change in reading relearns every conversation once.
---

- Status: Amended
- Date: 2026-10-26
- Amended by: [ADR 0081](0081-verifiable-redacted-source-snapshots.md) — completion checks include the source profile and published manifest
- Related: [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md) (safe source replacement), [ADR 0020](0020-explicit-pairing-and-automatic-workspace-sync.md) (sync merges without deleting), [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md) (new memories wait for LLM tagging), [ADR 0077](0077-stop-hook-hands-turns-to-one-waiting-learner.md) (the Stop hook's learner), [ADR 0079](0079-learn-only-what-the-claude-code-user-typed.md) (the change in reading that needs this), [chunking](../chunking.md)

## Context

Two paths store a conversation's memories, and neither followed a change in how transcripts are read:

- **`imprint learn`** skips a transcript whose stored watermark equals its modification time. It deletes a modified transcript's memories and embeds the transcript again in full.
- **The Stop hook's learner** stores the chunks not stored yet and never removes one.

A chunk's ID is derived from its content, project, source and position, and positions run across the whole transcript. ADR 0079 changes the chunks of nearly every agentic transcript without changing the files. Two problems would follow:

- `learn` would skip those transcripts and keep their old memories.
- The Stop hook's learner would store the new chunks next to the old ones.

## Decision

**A learn replaces a conversation's memories with what reading it gives now.** `index_transcript` stores a transcript's chunks through `vectorstore.replace_source`, ADR 0005's replacement for sources:

- Chunks already stored keep their embedding and their classification.
- After every chunk is stored, the conversation's other chunks in that project are deleted.
- `replace_source` now stores in batches and reports progress after each, as conversation learning did itself.
- `learn` no longer deletes a modified transcript's memories first.

**The watermark names the learner version.** The completion watermark records the modification time and `learner_version` (`cli_conversations.LEARNER_VERSION`, now 2). `learn`'s pre-filter counts a memory another learner version wrote as having no watermark, so its transcript is learned again. A change that gives transcripts different memories bumps the version.

## Rejected alternatives

- **Delete a conversation's memories and learn it in full when its version differs.** This was implemented first. It embeds every chunk again, and through ADR 0067 queues every chunk for LLM tagging again, including chunks that did not change. It also broke the retry of an interrupted learn (commit 5683d08): the run's stored chunks would be deleted instead of kept. `tests/test_background_tagging.py` caught the lost retry.
- **A one-time migration that clears conversation watermarks.** It covers this change only; the next change in reading would need another migration.

## Consequences

- **After an update, every conversation is learned again once,** by `imprint learn` or by each session's Stop hook.
  - Only chunks whose content or position changed are embedded and wait for LLM tagging.
  - Transcripts without client-written messages keep every chunk.
  - In transcripts with client-written messages, most chunks after the first such message change position, so most of those are embedded again.
- **Modified transcripts cost less to learn:** only their new or changed chunks are embedded, not the whole transcript. An interrupted learn resumes as before.
- **Not removed:**
  - A transcript whose reading now gives no chunks keeps its old memories; nothing replaces them.
  - Old chunks in a project other than the one the transcript's chunks are now placed in stay, since cleanup is scoped to source and project.
- **On paired devices, removed chunks can return.** Sync is a merge that does not carry deletions (ADR 0020), so a peer that holds old chunks can send them back. Their transcript then has no watermark, and the next learn removes them again.

## Invalidation conditions

- If chunk IDs stop depending on their position in the transcript, more chunks survive a change in reading: the cost described above drops.
- If sync comes to carry deletions, removed chunks stop returning from peers.
- If learning comes to depend on more than the transcript and the learner (for example a setting that changes chunking), the watermark must name that too, or a change to it is never applied to stored conversations.

## Validation

Executed on 2026-10-26 on Linux (WSL2), Python 3.12:

- `tests/test_background_tagging.py`, against an in-memory Qdrant:
  - A transcript read differently keeps its unchanged chunk, with that chunk's LLM classification, and embeds only the changed one; the replaced chunk is gone.
  - Every memory carries the learner version, and the watermark is valid for that version only.
  - An interrupted learn keeps its stored chunks and the retry embeds only the rest.
- `tests/test_learn_ingestion.py`:
  - The watermark is published only after every batch, and chunk indices are unique.
  - Memories of another or no learner version have no watermark.
- `tests/test_learn_stores.py`: a modified transcript is replaced without being deleted first.

Not validated: the update on a real data directory, and the return of removed chunks from a paired device.
