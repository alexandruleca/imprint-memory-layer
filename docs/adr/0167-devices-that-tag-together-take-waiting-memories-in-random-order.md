---
title: ADR 0167 — Devices that tag together take waiting memories in random order
description: While a paired device in the workspace also runs LLM tagging, the background job and retag --pending draw each page of waiting memories at random instead of newest first, so devices that share one backlog do not all classify the same memories before sync brings each other's tags.
---

- Status: Accepted
- Date: 2026-11-09
- Amends: [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md) — the order the backlog is tagged in while another device tags it too
- Related: [ADR 0138](0138-memories-that-wait-for-llm-tagging-travel-to-a-paired-device-that-tags.md) (the backlog travels; its invalidation condition on duplicated work), [ADR 0051](0051-synced-tags-follow-the-latest-retag.md) (the later classification wins), [ADR 0165](0165-the-local-model-tags-several-memories-as-one-batch.md), [tagging](../tagging.md#llm-tagging-runs-in-the-background)

## Context

On 2026-11-09 the owner asked: "could we make tagging random? so if 4 devices work in parallel they can each work on other memories not the same batch order".

- ADR 0067 tags each workspace's backlog newest first: the memories of the session in progress are the likeliest to be searched next.
- Since ADR 0138 the backlog travels. Every device that runs LLM tagging holds the same waiting memories, starts at the same newest one and goes on in the same order. A classification reaches the other devices only at the next sync, through its `tagged_at` stamp (ADR 0051). Until then they classify the same memories, so several devices tag at the speed of one.
- ADR 0138 called that duplicated work "limited to memories that waited on both devices at once". With the backlog travelling, that is the whole backlog. On the owner's Windows desktop, 174,959 memories waited on 2026-11-09, at 3.8 s each (the background job's log).
- ADR 0138's invalidation condition: *If two devices that both tag often duplicate work, give each waiting memory one device that claims it.*
- The pinned Qdrant server (1.17.1) and the client's in-process mode both draw a random sample under a filter (`SampleQuery(Sample.RANDOM)`).

## Decision

While this device is paired, in the workspace, with a device that said at its last sync that it runs LLM tagging (`peers_tag`, ADR 0138), tagging draws each page of the workspace's waiting memories as a random sample under the same filter, instead of newest first. This applies to the background job and to `imprint retag --pending`. Each page is drawn anew, and so is the first page after the job yields and starts again.

A device that tags alone keeps ADR 0067's order: newest first.

## Rejected alternatives

- **Claims, as ADR 0138's condition suggests.** Each waiting memory would get the device that claimed it. A claim reaches the other devices only at the next sync, the same delay that causes the duplication. It needs a new synced field and conflict rules, and a device that goes offline holds its claimed memories until some timeout.
- **A share of the backlog per device**, for example by a hash of the memory's ID and the sorted list of devices that tag. Pairings are per device pair (A–B and A–C without B–C), so devices would disagree about the shares. Qdrant cannot filter on an ID hash, so each device would read pages and skip what is not its share.
- **A random starting point in ID order.** Point IDs are uuid5 hashes of memory IDs, so scrolling from a random ID spreads devices over the backlog with any Qdrant version. Random sampling does the same with one query on the pinned server.
- **Random order always.** A device that tags alone would lose newest-first. The current session's memories would wait behind a random half of the backlog: about 4 days at this device's 3.8 s per memory and 174,959 waiting.
- **A random pick among the newest few hundred.** Every device would draw from the same small window and collide often until sync.

## Consequences

- While the backlog is large, collisions are rare. A device picks a memory another device has already classified, but whose tags have not arrived yet, with a probability of about U / P. U is the number of classifications not yet synced, and P is the number of memories waiting. A few hundred against 174,959 is under 1%. As the backlog empties the fraction grows, but the duplicated work is then small in absolute terms.
- Freshness: while another device tags, new memories no longer go first. A memory stored now waits for a random part of the backlog to drain across the devices. Until it is tagged, it carries its keyword and zero-shot tags, as before.
- A device paired with an older peer, which does not say whether it tags (ADR 0138), still tags newest first, and duplicates with that peer as before.

## Invalidation conditions

- If new memories wait too long for their tags while devices drain a large backlog together, take part of each page from the newest memories.
- If duplicated classifications remain common (two devices' classifications of one memory within one sync interval of each other), give each memory one claiming device, as ADR 0138 proposed.
- If Qdrant drops random sampling or it becomes slow on large collections, start each page from a random point ID in ID order instead.

## Validation

- `tests/test_background_tagging.py`, run on Windows 11 with the Python 3.13 runtime:
  - with a paired device that tags, a drain samples its pages at random and tags every waiting memory;
  - alone, a drain does not sample;
  - the existing tests still pass: newest first alone, and a stop between memories leaves the rest waiting.
- Against the owner's live Qdrant 1.17.1 server, random samples of 4 and 48 waiting memories were drawn with the same filter (the evaluation of [ADR 0163](0163-tagging-asks-for-its-classification-as-one-line-of-json.md)).
- Not verified: two or more real devices tagging one backlog over sync, and the duplicate rate they reach.
