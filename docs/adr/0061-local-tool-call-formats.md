---
title: ADR 0061 — Local tool-call formats by model family
description: The local chat parses tool calls in the format of the model's family, detected from the GGUF's own template or chosen with chat.tool_format; a model with no format chats without tools and says so.
---

- Status: Accepted
- Date: 2026-10-25
- Amends: [ADR 0059](0059-chat-speaks-each-models-native-tool-protocol.md) — local tool-call parsing
- Related: [ADR 0035](0035-prebuilt-llama-wheels-and-chat-provisioning.md), [ADR 0058](0058-recall-batched-reads-with-optional-local-planning.md) (local completions keep their deadline)
- Restructured: 2026-10-25

## Context

[ADR 0059](0059-chat-speaks-each-models-native-tool-protocol.md) renders the local conversation through the GGUF's own chat template, with `tools`. It parsed replies only in the Qwen3.5/Qwen3-Coder XML markup, so the calls of any other model family appeared as text. The user asked for a tool-call format selector so that models other than Qwen-style ones can call tools.

Rendering already works for any template that takes the standard `tools` variable. The families differ in the grammar of the reply. These are the official templates (fixtures in `tests/fixtures/chat_templates/`, sources and hashes in `sources.json`):

| Family | Templates | A call in the reply |
|---|---|---|
| Qwen XML | Qwen3.5-4B (Qwen's and Unsloth's GGUF), Qwen3-Coder | `<tool_call>\n<function=NAME>\n<parameter=P>\nvalue\n</parameter>\n</function>\n</tool_call>` |
| Hermes JSON | Qwen2.5, Qwen3 (HF and GGUF) | `<tool_call>\n{"name": NAME, "arguments": {...}}\n</tool_call>` |
| Llama 3 | Llama 3.1, 3.2 | the whole reply is `{"name": NAME, "parameters": {...}}`, optionally after `<|python_tag|>` |
| Mistral | Mistral 7B v0.3, Mistral Nemo | `[TOOL_CALLS][{"name": NAME, "arguments": {...}}]`, with an optional `"id"` |

Two facts about the runtime decide part of the design:

- **Special tokens were invisible.** llama-cpp-python's completion decodes generated tokens with `special=False`. Control tokens therefore vanish from the text. `[TOOL_CALLS]` (Mistral) and `<|python_tag|>` (Llama) are control tokens: token types 3 in the GGUF vocabularies, and `"special": true` in Mistral's tokenizer config. Qwen's `<tool_call>` is a user-defined token and survived. A Mistral call could not be read in its documented shape.
- **Templates disagree on an empty assistant turn.** Qwen3's GGUF template evaluates `'</think>' in message.content`. That fails when the content is null or missing, which is the shape Hugging Face documents for a tool-call-only turn. Mistral's templates also require call ids of exactly 9 characters.

## Decision

**Format registry.** `imprint/tool_formats.py` holds one entry per family: a name, a label and detail for the setting, the markers that open a call, whether a call can open only at the start of the reply, and a parser. The families are `qwen-xml`, `hermes`, `llama3` and `mistral`.

- Each parser accepts exactly its family's shape and returns the calls with their arguments as written.
- Llama's parser also accepts an optional `"type": "function"`. Llama 3.2 1B wrote it live, echoing how the template lists the tools, and llama.cpp's Llama 3.x parser accepts the same key. Any other `type` value is rejected.
- A parser does not read another family's shape. A test runs every parser over every family's output to check this.

**Detection from the template itself.** `detect(render, eos_token)` works as follows:

1. Render a probe conversation through the GGUF's template: a user message, then the same plus an assistant tool call.
2. Cut the difference between the two renders at the end-of-turn token. That text is exactly what the template's model is trained to write for that call.
3. The family whose parser reads that text back as the probe call is the template's format.

This replaces string fingerprints with the template's own rendering, and it checks that the renderer and the parser agree.

**Setting `chat.tool_format`.** Its choices are `auto` (default), each family, and `none`. Settings shows it as a dropdown through the existing choices mechanism.

- `auto` detects the format.
- `auto` with no match (no tools support, or an unknown grammar), and `none`, both run the chat without tools. No schemas are sent and the tool strategy guidance is left out of the system prompt.
- For `auto` with no match, a conversation's first reply carries a `notice` event naming the setting. The dashboard shows it above the reply with a link to Chat settings. Raw calls are never shown silently.
- An explicit family is used as chosen.
- Remote providers ignore the setting; they use API function calling (ADR 0059).

**One local generation primitive.** `local_llm.generate()` renders the messages and tools through the template. It then runs llama-cpp-python's token generator, decodes each token with special tokens as text (UTF-8 kept intact across tokens), and stops at an end-of-generation token or `max_tokens`. Sampling matches the chat handler's defaults.

- Both the chat and `complete()` (classification, summaries and recall's planner) use it. `complete()` keeps its grammar and deadline.
- The stream withholds text from a family's marker on and holds back a partial marker. A family whose call is the whole reply (Llama) streams the reply unless it opens as a call.

**Arguments are typed once, by the schema, for every backend.** A string given for a non-string parameter is read as JSON. The Qwen XML format writes every value as text, and small models write numbers as strings (live: Llama 3.2 1B wrote `"limit": "10"`).

- The typed arguments go into the `tool_call` event, the conversation history and the tool call.
- Dispatch rejects a value of the wrong type with a tool error the model reads, alongside missing and unknown parameters.

**Message shapes every supported template accepts.**

- An assistant tool-call turn always has string content, empty when there is no text.
- Local call ids and replayed-history ids are 9 alphanumerics (`call00000`, `hist00001`).

## Rejected alternatives

- **Fingerprint template text** (for example, `"<function=" in template`). This is brittle against escaping and wording. Qwen's Qwen2.5 GGUF template, for instance, writes its instruction as `{{\"name\": …}}`. Rendering a probe call tests the behaviour that matters.
- **One lenient parser for every shape.** This is what ADR 0059 rejected. It would turn text that is not a call into calls: for example, a Llama reply that is JSON for the user, or the flat JSON Qwen3.5 wrote for the old protocol.
- **Keep llama-cpp-python's chat completion.** Its text drops control tokens, so Mistral's documented shape could not be parsed. Llama 3 calls would also lose their `<|python_tag|>`, and parsing would have to guess from bare JSON.
- **llama.cpp server with its built-in chat parsers.** This means a separate server process that Imprint does not run today (ADR 0035, ADR 0059).
- **Model-specific rendering for templates without a `tools` variable** (Phi-4-mini takes tools as a string field on the system message). That is a second rendering path; see Consequences.

## Consequences

- Local models of the four families call tools. Any other model chats without tools, with a visible notice, unless the user chooses a format.
- `complete()` now generates through the same primitive. Its prompts, grammar, sampling and deadline are unchanged. The tagger's legacy short-tag path still uses llama-cpp-python's chat completion, as before.
- The primitive reads `llm._model.vocab`, which is private to llama-cpp-python, to ask llama.cpp which tokens end generation. The binding's own completion does the same. The binding is pinned at 0.3.36.
- Walls, reported rather than worked around:
  - **Phi-4-mini.** Its official template does not read the standard `tools` variable; it expects tools as a string on the system message. Detection finds no format.
  - **Hermes 3 (NousResearch).** It ships its tool template separately (`tokenizer.chat_template.tool_use`). The loader renders `tokenizer.chat_template`, so detection finds no format.
  - **gpt-oss (Harmony).** Calls (`to=functions.NAME … <|call|>`) arrive after an analysis channel, so streaming would have to be channel-aware. The smallest model, 20B, could not be validated here. It is not included.
  - **Qwen's official Qwen2.5-0.5B GGUF.** The embedded template asks for `{{"name": …, "arguments": …}}` in its instructions, while rendering past calls correctly. Detection says `hermes`, but the 0.5B model copied the double braces live. That is not JSON, so the reply is shown as text. Qwen's HF template and its Qwen3 GGUF do not have this quirk.
  - **Llama 3.2's template** tells the model to respond with a function call to every prompt. Live, Llama 3.2 1B called a tool even for "What is 17 times 3?", and it sometimes nested its call in the tool-list shape, which is shown as text. Tool use at 1B is unreliable whatever the parser.
  - **Mistral's templates** require strictly alternating user and assistant turns, and they drop the system prompt once a tool result follows the last user message. A stored conversation with two consecutive user turns (after a failed reply) fails to render, and the failure is reported as a generation error.

## Invalidation conditions

- If llama-cpp-python exposes generation that keeps special tokens, or the private `llm._model.vocab` access breaks on a binding upgrade, revisit the generation primitive.
- If a model family users need renders tool calls the probe cannot round-trip, such as tools outside the `tools` variable or channel-based replies, add a family or a rendering path through a new record.

## Validation

- `tests/test_tool_formats.py` uses 12 published templates:
  - Each is detected as its family, and Phi-4-mini and a tools-less ChatML template are detected as none.
  - A real tool call rendered by each template parses back to the typed call.
  - A whole tool conversation (system, history call, tool result, follow-up) renders through each template.
  - Synthesized outputs check each family's grammar, the cross-family rejection matrix, near misses (including the live Qwen2.5 double braces), marker holdback and the setting's choices.
- `tests/test_chat_stream.py` drives the chat through a stand-in GGUF that streams tokens:
  - Qwen XML, with Mistral's special-token marker and a Llama reply that opens with `<|python_tag|>` and carries a string number.
  - Schema typing in events, history and dispatch, and a wrong-type tool error.
  - `auto` without a format: tools are omitted, the strategy guide is left out, and the notice appears only on the first reply. `none` shows no notice.
- `tests/test_models.py` checks generation: special tokens, UTF-8 split across tokens, end of generation, `max_tokens`, grammar and deadline.
- The dashboard is sandbox-built (tsc, eslint, build), and Playwright shows the notice and its settings link.
- Live, read-only, on the Windows runtime (llama-cpp-python 0.3.36), with official GGUFs in a temporary directory. No tool touched user data. Detection was `auto` in every case:
  - **Llama 3.2 1B Instruct Q4_K_M** (bartowski) was detected as `llama3`.
    - "Show me chunks 0 through 2 of the indexed file imprint/chat.py." produced `<|python_tag|>{"name": "file_chunks", "parameters": {"source": "imprint/chat.py", "start": "0", "end": "2", "project": "chat"}}`. It parsed, and dispatch typed `start` and `end` as integers.
    - An earlier run wrote `{"type": "function", "name": …, "parameters": …}`, which the optional key now accepts.
    - Another run nested the call as `{"type": "function", "function": {"name": "store", …}}`, copying the template's tool list. That is no documented shape, so it was shown as text.
    - A full `stream_reply` loop with stubbed tools ran `kg_query {"subject": "alex", "predicate": "person", "limit": 10}`, after typing the model's `"10"`, and answered.
  - **Qwen3-0.6B Q8_0** (Qwen's GGUF) was detected as `hermes`. The file-chunks prompt produced `<tool_call>\n{"name": "file_chunks", "arguments": {"source": "imprint/chat.py", "start": 0, "end": 2}}\n</tool_call>`, which parsed. The arithmetic question was answered directly.
  - **Qwen2.5-0.5B-Instruct Q4_K_M** (Qwen's GGUF) was detected as `hermes`. The model copied its template's `{{…}}` (see Consequences), so its calls were shown as text.
  - **Qwen3.5-4B Q4_K_M**, the managed default, was detected as `qwen-xml`, a regression check of ADR 0059.
    - "what can you tell me about alex?" produced `search {"query": "alex", "limit": 10}`, and the file-chunks request produced `file_chunks {"source": "imprint/chat.py", "start": 0, "end": 2}`.
    - "What is 17 times 3?" was answered directly.
- Not validated live:
  - Mistral, whose smallest official instruct GGUF is 7B, beyond this run's 1.5 GB bound; it is covered by its official templates and synthesized outputs.
  - Llama 3.1 and 3.3 weights, which are template-tested only.
  - GPU offload.
