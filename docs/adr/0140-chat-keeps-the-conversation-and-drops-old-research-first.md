---
title: ADR 0140 — Chat keeps the conversation and drops old research first
description: When a chat prompt does not fit the model's context, earlier turns' tool exchanges go first, oldest first, then whole earlier exchanges, oldest first, instead of the whole history at once.
---

- Status: Amended
- Date: 2026-11-05
- Amends: [ADR 0093](0093-sequential-chat-section-analysis.md) — "old conversation history is still dropped first" now drops it a piece at a time: earlier research before anything said, then the oldest exchanges
- Amended by: [ADR 0141](0141-chat-summarizes-what-no-longer-fits-and-can-look-back.md) — conversation text that no longer fits at the start of a reply is summarized instead of dropped; dropping remains the fallback
- Related: [ADR 0059](0059-chat-speaks-each-models-native-tool-protocol.md), [ADR 0101](0101-chat-research-always-reserves-an-answer.md), [ADR 0127](0127-the-final-answer-is-written-from-research-notes.md), [local models](../local-models.md)

## Context

The user reported that the dashboard's Chat seemed to treat each message in isolation.

Chat replays a session's stored messages, tool exchanges included (ADR 0059), and fits the prompt to the model's context before each model turn. That fitting (ADR 0093, `SectionAnalysis.prepare`) deleted every message between the system prompt and the latest question as soon as the prompt did not fit: `del messages[1:anchor]`.

The local default is Qwen3.5-4B with an 8,192-token context. Each research turn stores several search results of up to 4,000 characters each. In the user's longest stored session (Outland, 2026-11-05), counted in characters and estimated at three per token:

- Before the PersonaPlex follow-up, earlier search results were 14,948 characters (about 5,000 tokens), against 6,180 characters (about 2,060 tokens) of conversation text.
- Before the repeated question about the transaction gateway, they were 30,522 characters (about 10,170 tokens), against 7,780 characters (about 2,590 tokens) of conversation.
- The whole prompt for that question came to about 17,560 tokens, so every earlier message was deleted, though the conversation text alone would have fitted.

ADR 0093's own context says that before it, fitting "removes old conversation turns and eventually old tool exchanges", gradually. The rewrite removed the history in one step.

## Decision

When the prompt does not fit, earlier history goes a piece at a time, re-measuring after each piece:

1. **Earlier turns' tool exchanges, oldest first.** An assistant tool call goes with its tool results. The answers that followed them already say what they found, and the final answer is written without them anyway (ADR 0127).
2. **Then whole earlier exchanges, oldest first.** Each goes from its question up to the next question, so the history still starts with a question and roles still alternate.
3. **Then, as before, the current turn's oversized input or evidence** is condensed by section analysis.

Stored sessions and ADR 0059's replay are unchanged. A model with a large context still sees every earlier tool exchange that fits.

## Rejected alternatives

- **Replaying no tool exchanges at all.** A remote model with a large context would lose earlier evidence it has room for. Dropping them first gives the same result on a small context.
- **A fixed number of earlier turns.** It does not follow the model's context: too many for an 8,192-token local model after long research, too few for a remote one.
- **Summarizing old turns with the model.** That is another generation on a local model already short of time per reply (ADR 0101), and it can lose detail the answers kept.

## Consequences

- A follow-up such as "compare it with…" keeps the turns it refers to as long as the conversation's own text fits. Replayed through the new fitting, the prompt for the last question of the user's session kept all 13 earlier messages and one earlier search result, about 6,200 estimated tokens.
- After long research on a small context, the model may search again for details an earlier tool result held. The earlier answer says what it found.
- Fitting measures the prompt once per removed piece, at most once per earlier message.

## Invalidation conditions

- If the conversation text alone regularly overflows the local context, summarize the oldest exchanges instead of dropping them.
- If the local default moves to a context large enough for the whole history, revisit whether old tool exchanges need to go first.

## Validation

- `tests/test_chat_analysis.py`: earlier research goes before any of the conversation, and only as much as must; then whole exchanges go oldest first, leaving a history that starts with a question and holds no orphaned tool exchange; the existing check that history goes before section workers start still holds.
- The chat stream and voice suites pass unchanged.
- Replay of the user's stored session through the new fitting, with estimated token counts, as above. Not verified: a live reply from the local model on that session.
