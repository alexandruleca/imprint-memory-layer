---
title: ADR 0157 — Chat research is charged for the work each request does
description: The research token budget charges each model request for the prompt past the prefix the previous request already processed plus the output it generated, refunding unused output allowance, instead of the whole prompt and maximum output every time.
---

- Status: Accepted
- Date: 2026-11-08
- Amends: [ADR 0102](0102-chat-research-follows-progress-and-resource-budgets.md) — what a request is charged against `chat.research_token_budget`
- Related: [ADR 0093](0093-sequential-chat-section-analysis.md) (section workers, also charged), [ADR 0127](0127-the-final-answer-is-written-from-research-notes.md) (the answer written after a budget stop), [ADR 0159](0159-a-reply-can-ask-the-user-and-wait-for-the-answer.md) (time spent waiting on the user is not research time)

## Context

The user asked a local chat (Qwen3.5-4B, the default 8,192-token context) "what can you tell me about multi language embedding and searching in the CDP?". The reply ran one `search`, then one `get_memory` call, then stopped with "The research token budget is complete." and answered from one result.

Under ADR 0102 every research request reserved its whole formatted prompt plus the full `chat.max_tokens` output allowance, unused output was never refunded, and each round resent the whole conversation and was charged for all of it again. With an 8k context a coordinator round costs up to about 9,200 tokens, and a search result too large for the context is split into section-worker requests that each cost about as much. The default budget of 65,536 therefore ran out after roughly seven requests, whatever they did: in this case two tool rounds plus the section workers of one large search result. ADR 0102 lists this consequence ("Large prompts or worker-heavy inputs can consume the token budget in relatively few requests"); in practice it ended research before the evidence was read.

The reservation also counted work no model does. llama.cpp keeps the evaluated prefix of the previous prompt and evaluates only what follows it, and remote providers cache prompt prefixes the same way; a round that appends one tool result to the conversation processes that result and nothing before it.

## Decision

A research request (coordinator or section worker) is charged:

- its formatted prompt tokens **past the leading messages it shares with the previous research request on the same model and tool offer**, measured with the same tokenizer and template as before, less 16 tokens kept charged for the next turn's header that a rendered prefix adds; plus
- its output allowance, reserved before it starts; after it finishes, the allowance it did not generate is refunded. Generated output is counted with the loaded tokenizer, or the UTF-8-byte estimate for remote providers, and for coordinator turns includes the tool calls it wrote.

A request still starts only when its new prompt and whole output allowance fit in what remains, so the budget remains a bound that no request crosses. Section workers stay charged (ADR 0102); their prompts differ from the coordinator's, so they share little prefix and are charged almost in full. The setting, its default (65,536) and range are unchanged; the time budget and evidence-novelty patience still end research.

## Rejected alternatives

- **Raise the default budget.** It keeps charging repeated prefixes and unused allowance, so the number of rounds still depends on context size rather than on work; a larger context (the MoE choice of ADR 0153 or a remote model) would again exhaust it in a few requests.
- **Scale the budget with the context window.** Same flaw; it only moves the cut-off.
- **Drop the token budget and keep only time and novelty.** ADR 0102 rejected removing every resource budget; remote providers bill tokens, and the budget is their only bound besides time.
- **Ask llama.cpp how many prompt tokens it actually evaluated.** Exact for local models, but the binding does not expose it per request through the generation path used here, and remote providers report cache hits differently or not at all; the shared-message prefix is a consistent, conservative estimate for both.

## Consequences

Research is bounded by the work it does: a round that adds one search result costs that result plus what the model writes. With an 8k context a round that appends one search result is charged that result plus what the model writes, rather than about 9,200 tokens, so the default budget no longer ends research after about seven requests; the time budget (300 s) or the no-progress patience is expected to end it first. Remote accounting stays an estimate (ADR 0102): a provider whose prefix cache expired between rounds bills more than is charged. Any change early in the prompt, such as a summary rewrite or the current-time line ADR 0158 adds to the instructions, makes the next request share nothing and be charged in full, as before. Measuring the shared prefix adds one tokenization per request.

## Invalidation conditions

- If remote providers' billed usage for research diverges materially from the charge, then reconcile with the provider's reported usage before calling the budget a cost bound.
- If llama.cpp stops reusing the evaluated prompt prefix between requests (for example the shared helper model is used by another task between rounds), then charge the full prompt in that case.
- If research again stops on tokens while it still finds new evidence in real replies, then revisit the default budget with those traces.

## Validation

- `tests/test_chat_research.py`: a cached prefix is not charged again, unused output is refunded, the whole allowance must fit before a request starts, and a prefix longer than the prompt never makes a charge negative.
- `tests/test_chat_analysis.py`: every coordinator and section-worker request is reserved and settled, and the budget equals the charges less the refunds.
- `tests/test_chat_stream.py`: the token budget still ends research and reserves the final answer when every round uses its whole allowance.
- Live run on the maintainer's machine (Windows, Radeon 8060S, Qwen3.5-4B, `chat.n_ctx` 65,536, the user's setting in the reported reply): six questions over 38 indexed docs, none stopped by the token budget; five answers held their facts after one search plus `get_memory` reads (75–166 s each, with the GPU shared with a running ingest). The sixth looped on one filtered search until the no-progress patience stopped it, which [ADR 0160](0160-chat-search-filters-are-hints-and-repeated-reads-are-not-run-again.md) addresses.
