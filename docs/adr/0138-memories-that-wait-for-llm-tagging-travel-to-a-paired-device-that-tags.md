---
title: ADR 0138 — Memories that wait for LLM tagging travel to a paired device that tags
description: A memory's "waiting for LLM tagging" marker syncs like its tags, so a paired device that runs LLM tagging tags it and the tags come back; with LLM tagging off, a device paired with one that tags marks its new memories for it.
---

- Status: Accepted
- Date: 2026-11-05
- Amends: [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md) — the tagging backlog is no longer each device's own: its markers travel to paired devices, and a device with LLM tagging off marks new memories when a paired device tags
- Related: [ADR 0051](0051-synced-tags-follow-the-latest-retag.md), [ADR 0116](0116-paired-devices-send-only-what-the-other-lacks-in-stored-chunks.md), [ADR 0129](0129-core-memories-are-user-approved-and-start-every-session.md), [ADR 0134](0134-placement-rules-and-organize-proposals-sync.md), [sync](../sync.md), [queue](../queue.md)

## Context

The user has two devices in two-way sync: a desktop (bedrock) with a GPU and LLM tagging on, and a laptop (Outland) with it off. They reported that Outland showed memories waiting for LLM tagging, and expected bedrock to tag them, since its tags reach Outland.

Read on Outland on 2026-11-05 (`tag_pending` counts in its Qdrant collection):

- 42,739 of its 79,008 memories waited for LLM tagging, and none of them had LLM tags.
- A sample of 2,000 of them were all recorded on Outland, almost all from indexed files, stored between 2026-10-28 and 2026-10-31, while LLM tagging was on there. It was off by the time they waited.
- Every LLM-tagged memory recorded on Outland was tagged between 2026-10-27 and 2026-10-31. None was tagged after that, so bedrock had tagged none of Outland's memories.

The cause is in the code. Sync carries a memory's classification and its stamp, `llm_tagged` and `tagged_at` (ADR 0051), but not the `tag_pending` marker (ADR 0067). Bedrock stored Outland's memories as not waiting, so its background job never saw them. Delta sync (ADR 0116) versions a memory by `tagged_at`, so memories both devices already held were never sent again either. On Outland, with LLM tagging off, nothing would ever tag them.

A device with LLM tagging off also never marked its new memories (ADR 0067). Even with the marker syncing, bedrock would not tag Outland's future memories.

## Decision

**The marker travels.** A memory record carries `tag_pending` while it waits and has no classification, and a receiver stores a new memory with it. A new delta dataset, `backlog`, lists each memory that waits on the sending device. For memories the other device already holds, the receiver marks those it has not classified as waiting; one it has classified stays as it is, and its tags travel back. Like core memories, rules and proposals (ADR 0129, 0134), the dataset goes only to a device whose handshake offers it. It streams after the memories, so the receiver holds each memory it is told about.

**The tags come back.** When the device that tags classifies a memory, its `tagged_at` moves forward, and sync sends the classification back as before (ADR 0051). Storing it clears the marker on the other device.

**A device can leave tagging to a paired device.** Each sync's handshake now says whether the device runs LLM tagging (`tagger.llm`), and the pairing remembers what the other device said. A new setting, `tagger.peer`, on by default, applies when `tagger.llm` is off. A device paired, in that workspace, with a device that said it tags marks its new memories as waiting, as if it tagged them itself, but runs no tagger. The storing paths, the tagging job and `imprint retag --pending` are otherwise unchanged. Before any paired device has said it tags, and with no pairing, nothing is marked.

**What shows.** The Queue page's background card says when the memories that wait here are tagged on a paired device ("LLM tagging by a paired device"), instead of asking to turn LLM tagging on. Storing commands print the same.

## Rejected alternatives

- **Turning LLM tagging on for the laptop.** It loads a tagging model on the weaker machine and duplicates the desktop's work, as the user said.
- **Marking every unclassified memory as waiting on every device.** ADR 0067 keeps the classification of memories stored with LLM tagging off unless `imprint retag` runs. A single device with LLM tagging off would then show its whole index as waiting.
- **Sending the marker only with each memory record.** Delta sync sends a memory only when it is missing or its `tagged_at` is newer, so the 42,739 memories bedrock already held would never carry it.
- **Running `imprint retag` on the desktop.** It would re-tag the desktop's whole index, 79,008 memories here, including memories already tagged. It is a one-off, and new laptop memories would wait again.
- **A per-memory "tag here or there" choice.** One setting, on by default, and the peer's own `tagger.llm` already say which device tags.

## Consequences

- On the user's devices, once both run this version and sync, bedrock receives Outland's 42,739 waiting memories as its backlog, tags them in its background job, and the tags reach Outland. Outland never runs a tagging model.
- New memories stored on Outland wait for bedrock's tags as long as bedrock tags. Until they arrive, they carry their keyword and zero-shot tags, as on a device that tags itself.
- Two paired devices that both tag may tag the same waiting memory. The later classification wins everywhere (ADR 0051), and the duplicated work is limited to memories that waited on both devices at once.
- A device paired with one that stops tagging keeps marking memories until the next sync tells it so. They wait until some device tags.
- An older peer neither offers the dataset nor says whether it tags. It receives no backlog, and is not counted as a device that tags.

## Invalidation conditions

- If two devices that both tag often duplicate work, give each waiting memory one device that claims it.
- If a paired device that tags goes offline for long, add a timeout after which the device that stores memories stops marking them.
- If a workspace's backlog outgrows the manifest (capped at 256 MiB, ADR 0116), page it like memories.

## Validation

- `tests/test_tag_backlog_sync.py`:
  - a new waiting memory reaches the peer still waiting, the peer tags it, and the tags clear the marker on the device that stored it; afterwards neither side sends anything;
  - memories both devices already hold start waiting on the peer from the `backlog` dataset alone, without any memory being sent again;
  - a memory already classified on the receiver, an unknown memory and malformed records are not marked;
  - the dataset is offered, and an older device's manifest decodes without it;
  - new memories wait only while a paired device in that workspace tags and `tagger.peer` is on, and always while this device tags;
  - the handshake says whether this device tags and records whether the peer does, and an older peer counts as not tagging.
- `tests/test_sync_delta.py`: the manifest lists the backlog, without memories classified since they were marked.
- `imprint/ui/tests/queue-background.spec.ts`: the background card with tagging left to a paired device.
- Not verified: the two real devices syncing over the relay and the desktop draining Outland's 42,739 memories.
