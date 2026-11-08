---
title: ADR 0161 — Chat keeps its instructions a reusable prompt prefix
description: The current time follows the user's question instead of sitting in chat's instructions, and the local model keeps the evaluated instructions and tool definitions in memory and restores them for the next turn, because a hybrid model such as Qwen3.5 cannot reuse a prompt prefix any other way.
---

- Status: Accepted
- Date: 2026-11-08
- Amends: [ADR 0158](0158-reminders-timers-and-scheduled-jobs-fire-from-the-api-process.md) — where the current time appears in a chat request
- Related: [ADR 0157](0157-chat-research-is-charged-for-the-work-each-request-does.md) (research charges assume a reused prefix), [ADR 0145](0145-a-request-runs-until-it-is-answered-or-explicitly-stopped.md) (Echo and chat replies), [local models](../local-models.md#how-chat-researches)

## Context

Looking at what [Headroom](https://github.com/headroomlabs-ai/headroom) (a context-compression proxy) does for agents, its cache-alignment idea applied to Imprint's own chat: a prompt prefix that stays byte-identical across requests is evaluated once. ADR 0158 had added the current time, to the minute, to chat's instructions, ahead of the guidelines and the conversation, so every new turn began with a changed prefix.

Measured on this machine (WSL2, 4 CPU threads, Qwen3.5-4B Q4_K_M, llama-cpp-python 0.3.36), time to the first token of a 3,510-token prompt after another prompt had been evaluated:

| Next prompt | Seconds |
| --- | ---: |
| Nothing cached | 79.5 |
| The previous prompt with 40 words appended | 0.4 |
| The previous prompt with the time changed near its start | 77.4 |
| The previous prompt changed 90% of the way in | 72.3 |

Qwen3.5 is a hybrid model (three linear-attention layers to each full-attention layer, GGUF architecture `qwen35`). llama.cpp cannot cut a recurrent state back to a shorter prefix, so the binding reuses the live context only when the new prompt extends it exactly; any difference evaluates the whole prompt. A live chat reply showed that rounds within one reply do extend each other exactly (round 2 shared all 3,989 evaluated tokens of round 1). A new turn does not: its prompt replays the earlier conversation without the previous reply's research rounds, so it differs where they were. With no history at all, chat's instructions and tool definitions were 3,841 tokens, which every new chat turn and every Echo utterance evaluated again (about 90 s on this CPU).

## Decision

1. **The time follows the question.** The request's last user message is the user's text, a blank line, then `[Current time, added by Imprint]` and the same time line as before. Stored messages are unchanged; earlier turns replay without it. The instructions, guidelines and replayed conversation form a prefix that changes only when they do.
2. **The local model keeps the instructions evaluated.** A chat coordinator turn passes its instructions to `local_llm.generate` as the stable text. The stable part is the prompt's shared token prefix with a rendering of only those instructions and the offered tools. Before generating, when the live context would not keep that part, the model either restores a saved checkpoint that is a prefix of the prompt, or evaluates the stable part alone and saves it. The binding then evaluates only the rest. A checkpoint is the llama.cpp context state with its tokens, kept in memory (at most two, most recently used first, none under 256 tokens). Any failure resets the context, drops the checkpoints and evaluates the prompt in full. Section workers, the tagger and other callers pass no stable text and are unchanged.

## Rejected alternatives

- **llama-cpp-python's `save_state` or `LlamaRAMCache`.** `save_state` also copies the logits array, `n_batch` × vocabulary floats (256 × 248,320 × 4 bytes, about 254 MB for Qwen3.5), on top of the state. The prompt cache works through `create_completion`, which chat does not use, and the loader disables the binding's cache because its disk variant depends on an unfixed `diskcache` deserialization issue (CVE-2025-69872).
- **Replay every research round in the history, so a new turn extends the previous request exactly.** Each turn's prompt would carry all earlier tool results, filling the context the summary (ADR 0141) keeps free, and any change to a replayed message would still lose the whole prefix.
- **Keep the time in the instructions, rounded to the hour or day.** The prefix would still change at each boundary, reminders need the minute, and a hybrid model would still lose the prefix at the previous reply's rounds.
- **Run Headroom in front of the chat.** Its cache alignment keeps provider prompt caches valid; the local model's state is inside this process, not behind an HTTP API.

## Consequences

Measured with the same model and machine, two replies in one process: the first reply evaluated the 3,841 stable tokens alone (91 s, the cost it paid before as part of its first request) and saved a 170 MB checkpoint. The second reply restored it in 0.1 s and its first request produced a token after 2.6 s instead of about 90 s. Rounds within a reply are unchanged. A coordinator round after a section worker replaced the context also restores the checkpoint instead of evaluating the instructions again. On a GPU the same work takes seconds, so the saving is small there; on CPU-only devices it is most of a turn's latency, for Echo especially.

The checkpoint costs memory per distinct instruction set (chat and Echo differ): 170 MB each for Qwen3.5-4B at 3,841 tokens; the 35B-A3B choice is unmeasured. Changing a guideline or the offered tools saves a new checkpoint at the next turn. Models whose context can be cut back (attention-only GGUFs) keep any shared prefix as before and only use a checkpoint when it covers more. The model reads the time inside the user's turn, labelled as added by Imprint. Remote providers that cache prompt prefixes automatically now find the instructions and history unchanged between turns; Anthropic's cache needs explicit `cache_control` marks, which Imprint does not send.

## Invalidation conditions

- If the llama.cpp binding gains context checkpoints that let a hybrid model reuse a partial prefix, then drop Imprint's own checkpoints.
- If the checkpoint memory matters on small devices (measured for the MoE choice or a long guideline set), then keep one checkpoint or make the count configurable.
- If models answer the time note as though the user had written it, then move it into a separate message for templates that allow one.
- If a template renders the tools before the system text so that the stable part falls under 256 tokens, then compute the stable part from the tools alone.

## Validation

- `tests/test_prompt_checkpoints.py`: the first turn evaluates and saves the stable part; a hybrid model restores it for the next turn; a prompt extending the live context keeps it; an attention-only model keeps a shared prefix; short prefixes are not saved; a checkpoint as long as the prompt is not restored; at most two are kept, most recently used first; a failure resets the context and drops them; the stable part is the shared prefix with its rendering.
- `tests/test_chat_stream.py`, `tests/test_chat_memory.py`, `tests/test_schedule.py`: the time follows the question in the request and is absent from the instructions.
- The live measurements above, run with `turns_probe.py` and `prefix_probe.py` in the session scratchpad against the evaluation corpus of ADR 0160. Windows, macOS and GPU runs were not measured.
