---
title: ADR 0166 — Tagging never asks a model to think
description: LLM tagging asks OpenAI-compatible servers to answer without thinking — chat_template_kwargs for vLLM and an openai provider at another address, reasoning_effort none for Ollama — drops the switch for a server that refuses it, and keeps the local model's non-thinking template.
---

- Status: Accepted
- Date: 2026-11-09
- Related: [ADR 0010](0010-qwen-defaults-and-explicit-reindex.md) (the local template with thinking disabled), [ADR 0165](0165-the-local-model-tags-several-memories-as-one-batch.md), [ADR 0163](0163-tagging-asks-for-its-classification-as-one-line-of-json.md), [tagging](../tagging.md#prompt-batches-and-thinking), [local models](../local-models.md)

## Context

On 2026-11-09 the owner asked for faster tagging, with a concurrency option that "automatically disabled thinking for tagging", and asked whether thinking is useful for tagging at all.

- **The local model** has never thought: since ADR 0010 the embedded chat template is rendered with `enable_thinking = false`.
- **Server providers** got nothing that switched thinking off. Ollama and vLLM serve Qwen3-family models, which think unless the request says otherwise. The tagger only stripped `<think>` blocks from replies (`_strip_think`, `_extract_openai_content`). A reply is capped at 200 tokens, so a model that thinks first can spend the whole budget on reasoning. That reply holds no classification, the memory keeps its keyword tags, and it leaves the backlog without LLM tags.
- **The cost of thinking, measured.** Qwen3.5-4B Q4_K_M with thinking switched on in its template, on the owner's Radeon 8060S (Vulkan). On three waiting memories, every reply was still thinking at a 4,000-token cap, after 98 s each, without reaching a classification. Without thinking, the same model classified the same memories in 1.2–3.0 s with replies of 34–78 tokens. Under the tagger's 200-token cap, a thinking reply would never reach its JSON.
- **What each server reads.**
  - vLLM documents `chat_template_kwargs: {"enable_thinking": false}`, and llama.cpp's server and SGLang read the same field.
  - For Ollama's OpenAI-compatible `/v1` endpoint, the documented control is `reasoning_effort`, with `"none"` asking for no thinking. Its documentation names gpt-oss; that `"none"` reaches `think: false` for other models is community-reported, not verified here.
  - OpenAI's API refuses fields it does not know, and which `reasoning_effort` values it accepts depends on the model.

## Decision

Every tagging request asks the model not to think, where the provider has a switch for it:

| Provider | What tagging sends |
| --- | --- |
| `local` | Nothing new: the template already renders `enable_thinking = false` (ADR 0010). |
| `vllm`, and `openai` with `tagger.llm_base_url` set | `extra_body={"chat_template_kwargs": {"enable_thinking": false}}` |
| `ollama` | `extra_body={"reasoning_effort": "none"}` |
| `openai` at its own address, `gemini` | Nothing: their default tagging models (`gpt-4o-mini`, `gemini-2.0-flash`) do not think, and the APIs refuse unknown fields. |
| `anthropic` | Nothing: Claude models think only when a request asks. |

Suppose a server answers a request with the switch with HTTP 400 and accepts the same request without it. That provider, address and model then get no switch for the rest of the process. A request that also fails without the switch raises as before, and the switch stays. Replies are still stripped of thinking text.

## Rejected alternatives

- **Thinking on for tagging.** Measured above: over 30 times the time per memory, and no classification within 4,000 tokens, let alone 200. A short classification against a fixed label list leaves the model little to reason about.
- **A setting to choose thinking for tagging.** The owner asked for it to be off automatically, and nothing measured shows thinking would help a classification this short.
- **Sending `reasoning_effort` to OpenAI and Gemini as well.** The accepted values differ by model, and non-reasoning models refuse the field. Each process would then spend a refused request before falling back, for models that do not think anyway.

## Consequences

- A thinking model served by vLLM, Ollama or llama.cpp's server answers tagging requests directly, within the 200-token cap.
- A server that ignores the field behaves as before. One that refuses it costs one extra request per process.
- A reasoning model chosen for the `openai` or `gemini` provider at its own address keeps that provider's default reasoning.
- Other users of remote servers (chat, recall, summaries) are unchanged; [local models](../local-models.md) says so.

## Invalidation conditions

- If Ollama documents a different switch for its `/v1` endpoint, or rejects `reasoning_effort: "none"` for models without thinking, use what it documents.
- If a provider's default tagging model becomes a thinking model, add that provider's switch.
- If an evaluation shows that thinking improves tags enough to pay its cost, make it an opt-in setting.

## Validation

- `tests/test_tagging_throughput.py` (`ThinkingTests`), run on Windows 11 with the Python 3.13 runtime and a stand-in OpenAI client:
  - each provider receives its switch, or none;
  - the openai provider at another address gets the vLLM switch;
  - a server that refuses the switch gets the request again without it, and never again in that process;
  - a request that fails without the switch too raises and keeps the switch;
  - Anthropic gets the instructions as its system prompt and no thinking parameter.
- The thinking measurement above, run with `tag_eval.py` in the session scratchpad.
- Not verified: a real vLLM, Ollama or llama.cpp server. The switches follow vLLM's documentation and Ollama's OpenAI-compatibility notes.
