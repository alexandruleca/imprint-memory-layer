---
title: ADR 0079 — Learn only what the Claude Code user typed
description: User messages Claude Code writes itself (task notifications, other sessions' messages, system reminders, image placeholders, compaction summaries) are not questions; replies to them stay with the question the user typed.
---

- Status: Amended
- Date: 2026-10-26
- Amended by: [ADR 0082](0082-retain-readable-conversation-evidence.md) — retain client-written context as labelled evidence while keeping human-question attribution
- Related: [ADR 0034](0034-learn-from-client-stores-by-conversation-ownership.md) (learning from client stores), [ADR 0056](0056-learn-cline-sessions-from-their-manifests.md) and [ADR 0057](0057-copilot-home-as-store-config-and-hook-root.md) (Cline and Copilot messages are already what the user typed), [ADR 0078](0078-keep-qwen3-5-4b-as-the-local-tagger.md) (where the problem showed), [ADR 0080](0080-conversations-are-relearned-when-their-reading-changes.md) (how stored conversations are learned again), [chunking](../chunking.md)

## Context

Learning pairs each user message with the assistant messages after it, so each exchange is a question and its answer. In Claude Code transcripts, many user messages are written by the client, not typed by the user:

- task notifications from background work;
- messages from other sessions and agents ("Another Claude session sent a message: … that's permission laundering");
- system reminders, image placeholders ("[Image: source: …]"), skill bodies and compaction summaries.

Each of these started an exchange of its own. `_clean_user_text` removes XML-style elements by regular expression, but these messages keep text outside any element. Nested elements also leave fragments behind, such as a lone `</task-notification>`. When nothing was left after cleaning, the whole exchange was dropped, reply included.

**Measured on 2026-10-26** over the 11 Claude Code project folders on the owner's machine: 592 of the 1,189 learned exchanges had text the client wrote as their question. The tagging evaluation in ADR 0078 found models tagging that text (`agent`, `permission`, `workflow`) instead of the conversation.

**Claude Code marks the messages it writes.** A survey of the same transcripts' user messages with text:

| Marker | Messages |
| --- | --- |
| `origin.kind: "human"` | 712 |
| `origin.kind: "task-notification"` | 387 |
| `origin.kind: "peer"` | 157 |
| `origin.kind: "coordinator"` | 15 |
| No `origin` (older versions, and messages marked only `isMeta`) | 552 |

`isMeta: true` marks system reminders, image placeholders, other sessions' messages and skill bodies. `isCompactSummary` marks compaction summaries.

## Decision

`transcripts.message` returns nothing for a Claude Code user message that the client marks as its own: `isMeta`, `isCompactSummary`, or an `origin.kind` other than `human`. A message without these fields, as older versions write, counts as typed.

So a message the client wrote starts no exchange. The replies to it join the exchange of the question the user typed before it. The element cleanup stays for transcripts from versions that mark nothing.

## Rejected alternatives

- **Strip the client's wording with more regular expressions.** The wording changes between client versions, while the markers state the message's origin. Stripped text would also still leave the message starting an exchange of its own, with an empty or partial question.

## Consequences

- **On the owner's machine:**
  - The same transcripts give 660 exchanges, none with the client's text as the question.
  - Answer text kept goes from 2,434,730 to 2,434,387 characters. The difference is replies that came before any typed question in their session; they are no longer learned.
- **Exchanges are longer.** The work after a background task's notification now belongs to the request that started it. Long exchanges are split into chunks as before.
- **Not covered:**
  - Transcripts from Claude Code versions that wrote no markers still rely on the element cleanup.
  - The session summarizer reads transcripts its own way and still sees the client's messages.
- **Stored memories** of conversations read the old way are replaced through ADR 0080.

## Invalidation conditions

- If Claude Code stops writing these markers or changes their meaning, the rule must follow, or the client's messages become questions again.
- If Claude Code marks a kind of typed input with another `origin.kind` (for example scheduled prompts the user wrote), decide whether it is a question; until then it is treated as the client's.

## Validation

Executed on 2026-10-26 on Linux (WSL2):

- `tests/test_learn_stores.py`:
  - messages marked as the client's (`isMeta`, `isCompactSummary`, `origin.kind` `task-notification`, `peer`, `coordinator`) are not messages, while `human` and unmarked ones are;
  - a typed question followed by a reply, a task notification, another session's message and a second reply gives one exchange with the typed question, both replies, and none of the client's text.
- The before and after exchange counts above, measured with the learner on this machine's transcripts.

Not validated: transcripts of Claude Code versions older or newer than those on this machine.
