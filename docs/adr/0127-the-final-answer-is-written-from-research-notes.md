---
title: ADR 0127 — The final answer is written from research notes
description: When chat research ends on a budget, the reserved answer is written from a deduplicated digest of what research found, without the raw research turns, and an answer that cannot finish ends with a short list of findings instead of raw tool output.
---

- Status: Accepted
- Date: 2026-11-04
- Amends: [ADR 0101](0101-chat-research-always-reserves-an-answer.md) — what the reserved answer's request holds, and how a reply ends when that answer cannot finish
- Related: [ADR 0102](0102-chat-research-follows-progress-and-resource-budgets.md), [ADR 0093](0093-sequential-chat-section-analysis.md)

## Context

The user reported that chat replies that use up the research budget come out long and hard to read.

A stored reply on the maintainer's Mac shows why. The question "what can you tell me about the transaction gateway" ran on the local Qwen3.5-4B:

- Five searches took about 35 s each, and the 300 s research budget ended during the next model turn.
- ADR 0101's reserved answer kept the whole research transcript. That was five search results of about 3,870 characters each, full of memory ids, similarity scores, hashes, devices and paths, with the same memories returned by several searches.
- The answer ran for the full 120 s final deadline and stopped mid-sentence.
- The fallback then appended three raw search results, quoted line by line, provenance included.

Replaying that final pass on the same model measured the request at 6,153 prompt tokens.

## Decision

When research ends on a budget, stagnation or a full context, `chat_research.notes` turns this reply's tool results into notes:

- Search hits are split by memory id. Ids, scores, hashes, devices, paths, excerpt markers and headers are dropped, and each hit is labelled by its source, kind and project.
- A memory returned by several searches becomes one note and ranks first.
- Other tool results each become one note, named by the tool and its subject.
- Empty reads, errors and calls that were not run add nothing.

The reserved answer's request then holds:

1. the system prompt without the tool strategy;
2. up to six earlier user and assistant messages of the conversation, without tool exchanges;
3. the question, followed by the notes numbered for citation, within 6,000 characters.

The written answer is asked to open with a direct answer, give the key points as a short list citing notes as `[n]`, name what the notes leave open, and stay under about 250 words. A spoken reply is asked for two or three plain sentences. A written answer that cites notes gets a **Sources** list of those notes, added by Imprint rather than written by the model.

If the answer stops early or asks for tools again, what it wrote stays and is followed by a note that it was cut short and the three strongest findings. With no answer at all, the reply is a short list of the four strongest findings. Spoken replies keep ADR 0101's spoken fallback.

ADR 0101's other rules stand: no tools are offered or run in the final turn, the final turn has its own output allowance and deadline, and a bounded research stop is never a failed reply.

## Rejected alternatives

- **Keeping the research turns and only shortening each result.** The same memories would still repeat across searches, and the call and result protocol stays in the request. A small model can still answer it with more tool calls.
- **A longer final deadline.** The replayed request took over 120 s because of its size. A longer deadline makes a slow answer slower without making it readable.
- **Keeping the quoted raw excerpts as the fallback.** That fallback is what the user found hard to read.

## Consequences

- On the replayed reply, the final request fell from 6,153 to 1,536 prompt tokens. The answer finished in 14 s with a direct answer, cited points, a line on what was not covered and a sources list.
- The final answer sees only excerpts of at most 700 characters per note and 6,000 characters in all. Detail beyond them is not in its request. ADR 0093's section analysis, which runs during research, is unchanged.
- Notes are parsed from the search tool's text format. If that format changes, hits parse as one note per result, which is still clean but not deduplicated. A test pins the format.
- Citations are checked only for range. A number the model invents within range lists a real note that may not support the point.

## Invalidation conditions

- If final answers miss facts that were only in the dropped detail, raise the note and digest sizes or let the final pass reread notes, measured on stored replies.
- If the search tool returns structured results to chat instead of text, build notes from those and drop the text parsing.
- If small local models stop proposing tool calls in a tools-free turn that includes the research protocol, reconsider passing the research turns.

## Validation

- `tests/test_chat_stream.py`: the final request holds the question with notes and no tool messages; repeated hits merge, rank first and lose their provenance; cited notes are listed as sources; an answer cut off after a whole sentence gets the cut-short note and findings without an ellipsis; a final turn that asks for tools, fails or cannot prepare its context ends with the findings; existing ADR 0101 and 0102 cases still pass.
- `tests/test_chat_research.py`: earlier replies, empty reads, errors and calls that were not run add no notes; other tools become one note named by their subject; long notes end at a word; the digest keeps whole notes within its size; sources list only cited, valid numbers.
- macOS on an Apple M3: the stored transaction gateway reply's research, replayed through the new final pass on Qwen3.5-4B Q4_K_M, gave the token counts and the 14 s answer above.
- Not verified: remote providers' answers, and voice replies on hardware.
