---
title: ADR 0141 — Chat summarizes what no longer fits and can look back through the conversation
description: When a conversation's own text no longer fits the model's context, its oldest exchanges are summarized into a running summary kept with the session, and the model can search and reread every earlier message of the conversation with a read-only conversation_history tool; voice keeps both in memory only.
---

- Status: Amended
- Date: 2026-11-05
- Scope: dashboard Chat and Echo's same-chat voice route; the small LFM voice engine keeps its own short context
- Amends:
  - [ADR 0140](0140-chat-keeps-the-conversation-and-drops-old-research-first.md) — conversation text that no longer fits is summarized instead of dropped; dropping remains the fallback when the summary cannot be written
  - [ADR 0043](0043-live-voice-and-echo-companion.md) — a voice conversation keeps every heard message, with its tool results, and a running summary in memory, instead of its last three exchanges
- Amended by:
  - [ADR 0145](0145-a-request-runs-until-it-is-answered-or-explicitly-stopped.md) — voice keeps every completed reply in its history, played to the end or not
  - [ADR 0146](0146-echo-saves-its-conversations-in-chat-history-and-carries-chats-on.md) — a voice conversation's history and running summary are saved with its chat in Chat history, unless the user turns saving off
- Related: [ADR 0093](0093-sequential-chat-section-analysis.md), [ADR 0127](0127-the-final-answer-is-written-from-research-notes.md), [ADR 0060](0060-redact-secrets-before-memories-are-stored.md), [ADR 0059](0059-chat-speaks-each-models-native-tool-protocol.md), [ADR 0102](0102-chat-research-follows-progress-and-resource-budgets.md), [local models](../local-models.md), [live voice](../live-voice.md)

## Context

The user asked for a way for the chat model to "query previous chat history in the conversation, allowing it to find things referenced later", and for "a summary of the important details similar to the compression that is done by Claude Code", for voice as well. The behaviour they want, from another assistant: after compaction, asked about something said earlier, it said it was searching the conversation for the pasted message, found it and answered from it.

ADR 0140 keeps a conversation as long as its text fits, dropping old research first and then whole exchanges, oldest first. Its invalidation condition anticipated this record: if the conversation text alone overflows the local context, summarize the oldest exchanges instead of dropping them. Once dropped, an exchange was gone for the model: it could not answer "what did I paste earlier?" except by searching memories, which do not hold the conversation.

Voice kept a `deque(maxlen=6)`: the last three heard exchanges, without tool results (ADR 0043).

Measured on the user's longest stored session (Outland, 31 messages, 56,808 characters), with the installed Qwen3.5-4B Q4_K_M at 8,192 tokens and its own tokenizer and template:

- Replaying every stored message would take 23,102 prompt tokens; the conversation text alone 5,481, tool schemas 2,111 of them. With the default 1,024-token answer allowance the text alone still fits, as ADR 0140 measured.
- Five more realistic exchanges (a 3,607-character pasted compose file, a decision, a preference, two longer answers) bring the text alone to 7,925 tokens with the system prompt: more than the 7,152 left for the prompt. ADR 0140 would then drop the oldest exchanges, the paste among them.

The local model is slow: on the maintainer's Apple M3, under load from a learning job running at the same time, it prefilled about 100 tokens a second and wrote 5 to 10 tokens a second. Any extra generation had to be rare and short.

## Decision

**A running summary stands in for the exchanges that no longer fit.** At the start of a reply, before research, chat checks whether the conversation's own text (the user's and the assistant's messages; old research goes first anyway, ADR 0140) fits beside the current summary. Only when it does not:

1. The oldest exchanges are chosen to be covered until the newest ones fill at most half the room left beside a full-size summary (900 tokens reserved), so the next summary waits until the conversation has grown by that half again.
2. The model writes one bullet per newly covered exchange, given the summary so far, with message numbers like `(#12)`, at most 300 output tokens. Long messages are clipped for it, head and tail, and tool results are named rather than included. The bullets are appended to the summary so far; past 2,000 characters its oldest bullets go first.
3. The summary goes into the system prompt, under "Earlier in this conversation", with the range of messages it covers and a deterministic list of covered material a later turn may ask about: long pasted messages by number and first line, and tool results by tool and query.
4. The covered messages stay out of the prompt. ADR 0140's fitting handles everything after them as before, including the current reply's research.

The summary is written once per reply at most, never during research, within 120 seconds including the wait for the local model, and outside the research budget. The stream shows `Summarizing the earlier conversation…` while it runs and then a `summary` event. If the model fails, times out or the request cannot fit, the reply continues with ADR 0140's dropping, and no new attempt is made until six more messages have been added. Secrets are redacted (ADR 0060) in everything the summarizer reads and in what it writes. Chat stores the summary with its session (`chat_summaries`: text, the last message number it covers, the retry point), and the next reply reuses it.

**The model can look back through the whole conversation.** A read-only `conversation_history` tool, offered whenever there are earlier messages and tools are offered, searches every earlier message of the current conversation (the user's, the assistant's and tool results, summarized or not) or reads one by its number exactly, 4,000 characters at a time from a character offset. Search is lexical: words of the query, rarer words weighted more, matched exactly or by a shared stem, over 700-character windows of each message; the best two messages show up to three places each, so a long paste shows the place that names it and the place asked about. Output stays within 4,000 characters. The tool is built for one reply from the messages handed to that reply, so it cannot reach another conversation, workspace or user. The tool strategy gains one line: when the user refers to something earlier in the conversation that is not shown, use `conversation_history`, not search, and say what was found, quoting it briefly. The Chat page and Echo show the step as "Searching earlier in this conversation for …".

**Voice keeps both in memory.** A voice conversation keeps every heard message, numbered as Chat stores them and with the tool results of heard replies (at most 400 messages, 4,000 characters a result), and its summary, for as long as the session lasts. It replays them through the same fitting as Chat. Nothing is written to disk.

## Rejected alternatives

- **Rewriting the whole summary on every update, as Claude Code's compaction does.** Measured on the stored session with the local model, updating an eight-bullet summary with messages #18–#31 took 38.6 seconds and 317 output tokens, most of them copying the previous bullets; writing only the new bullets took 19.1 seconds and 112 tokens, and leaves earlier bullets exactly as they were. Corrections are written as new bullets, which come after what they correct.
- **The first prompt, without an example or a closing instruction.** On messages #1–#17 it summarized only the last two messages (80 tokens), losing naming-ai, bedrock's GPU and Wickling. Asking, after the transcript, for one bullet per exchange from the first to the last number, with a two-line example, covered all six.
- **Covering just enough to fit.** Once a conversation is long, every reply would then need a new summary: a model call on nearly every turn. Leaving half the room free makes it one call per several turns.
- **Summarizing when the current reply's research crowds out earlier exchanges.** Those drops are transient: the next reply rebuilds the prompt from the stored conversation, where the exchanges fit again. A summary then would cover exchanges that did not need it and add a model call in the middle of research.
- **Embeddings for the history search.** A conversation is small, and people refer back with the words the message used, often identifiers and numbers in pasted text. Lexical search took 9.8 ms per query on the stored session and 182 ms on a synthetic 400-message, 921,024-character conversation, needs no model, and does not compete with the chat model for the local GPU or the inference lock.
- **Keeping a timed-out summary's partial output.** It would replace the summary so far with whatever was written before the deadline. The fallback keeps the previous summary instead; the exchanges stay searchable.
- **A setting to turn summaries off.** Not added: the summary runs only when text would otherwise be dropped, is bounded in time and output, and falls back to the old behaviour.
- **Persisting voice conversations so they can be summarized.** ADR 0043 keeps voice transcripts off disk; the summary and history live as long as the voice session.

## Consequences

- A follow-up about something said long ago keeps working after it leaves the prompt: in the evaluation below, the model asked for the billing port of a compose file pasted five exchanges earlier, and covered by the summary, called `conversation_history`, received the paste's billing block and answered 8101 correctly.
- A reply that summarizes takes longer: 43 to 62 seconds more in the evaluation on a loaded Apple M3. It happens when the conversation text first overflows and then each time it grows by about half the room again.
- The summary is lossy and written by a 4B model; it can be wordy (one bullet per message rather than per exchange) or miss details. The exact words stay available to the tool, and the prompt says so.
- The summary's oldest bullets drop out past 2,000 characters in very long conversations; those exchanges remain searchable.
- Stored sessions gain a `chat_summaries` table, created on first use; existing sessions need no migration. `GET /api/chat/sessions/{sid}` returns `summary`, and `/api/chat` streams a `summary` event. The Chat page shows a collapsible "Summary of earlier conversation" after the last exchange it covers.
- Voice replays the tool results of heard replies, which ADR 0140 drops first when they do not fit; the small LFM engine still sees only its last three exchanges.
- The final answer after a research budget stop (ADR 0127) keeps the summary in its system prompt, without the history tool's guidance.
- Search cost grows with the conversation: about 0.2 seconds for 400 messages of 4,000 characters.

## Invalidation conditions

- If the local default moves to a context large enough that conversations rarely overflow it, revisit whether the summary is worth its code; the history tool stays useful for long pastes.
- If summaries routinely take longer than the 120-second limit on supported hardware, or fall back more often than they succeed, shorten the request or the output, or move summarizing out of the reply path (for example after a reply, in the idle slot).
- If users report the model searching memories or guessing instead of using `conversation_history` for what they said earlier, strengthen the strategy line or show more of the covered material in the summary.
- If conversations reach sizes where lexical search takes more than a second, index the conversation once per reply or cap the searched messages.
- If voice conversations become persisted by a later decision, store their summary with them as Chat does.

## Validation

- `tests/test_chat_memory.py`: the tool finds a passage deep inside a long pasted message and reads it back exactly by range, and pages through it to its end; results are bounded, numbered by stored seq and see only the messages handed in; tool results are searchable by their query. The plan covers nothing while the text fits, then the oldest exchanges, at an exchange boundary, with the stated headroom. The summarizer request clips long messages, names tool results and fits; new bullets are appended and the oldest go beyond the cap. With a stand-in local model: a long paste early in a session is compacted out of the prompt, the summary event comes before the reply's tool call, the prompt holds the summary block, the strategy line and the tool but not the paste, and the tool returns the paste's passage; a stored summary stands in for what it covers; a summary that runs out of time falls back to ADR 0140's dropping and waits six messages before trying again; secrets are redacted in the summarizer's request and in the summary; cancelling while summarizing ends the reply without a summary; the final answer keeps the summary without the lookup guidance; the tool is offered only with earlier messages, in voice too. The summary is stored with its session, deleted with it, and passed through the chat route.
- `tests/test_voice.py`: a voice conversation passes every heard message, numbered and with its tool results, and the summary to the next turn; an unheard reply is not kept, its summary is; no chat database is written. Echo's tool event carries "Searching earlier in this conversation for …".
- `imprint/ui/tests/chat.spec.ts`: a stored summary shows as a collapsible divider after the exchanges it covers and opens to its text; a live reply shows "Summarizing the earlier conversation…", then the summary, and the lookup as "Searched earlier in this conversation for “billing port”". `imprint/ui/tests/voice.spec.ts`: Voice shows the summarizing and lookup steps.
- Evaluation on a scratch copy of the user's stored session with the installed Qwen3.5-4B Q4_K_M (8,192 tokens) on the maintainer's Apple M3, while a learning job ran: a summary of messages #1–#17 took 55.2 seconds (1,655 request tokens, 229 output tokens) and covered all six exchanges in eight bullets, including bedrock's RTX 5070 Ti with 16 GB and the user's correction about PersonaPlex; an update with #18–#31 took 19.1 seconds. A full reply on the session plus those five exchanges: the text would have needed 7,925 prompt tokens against 7,152 available; the summary covered #1–#33 (nine exchanges, the paste included) in 42.8 seconds, the reply's prompt came to 3,948 tokens, and the model looked the paste up and answered correctly 80 seconds after the question. Not verified: hardware other than this Mac, remote providers with a live API, and spoken Echo conversations long enough to summarize.
