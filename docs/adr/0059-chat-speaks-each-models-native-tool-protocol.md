---
title: ADR 0059 — Chat speaks each model's native tool protocol
description: Dashboard chat keeps one registry of tool JSON schemas; the local model gets them through its own chat template and answers in its trained markup, and remote providers use API function calling.
---

# 0059 — Chat speaks each model's native tool protocol

- Status: Accepted
- Date: 2026-10-25
- Related: [ADR 0035](0035-prebuilt-llama-wheels-and-chat-provisioning.md) (stream withholds tool-call markup, unchanged), [ADR 0043](0043-live-voice-and-echo-companion.md) (voice is read-only and keeps the session workspace, unchanged), [ADR 0010](0010-qwen-defaults-and-explicit-reindex.md), [local model selection](../local-model-selection.md)

## Context

Dashboard chat used a text protocol of its own. The system prompt listed the tools in prose and asked for `<tool_call>{"name": "tool_name", "args": {...}}</tool_call>`. The parser read `args` (or `arguments`) from that JSON, and every provider generated with a `</tool_call>` stop string.

With the default local model, Qwen3.5-4B Q4_K_M, asking "what can you tell me about alex?" gave five `[tool error] bad args for search: _tool_search() missing 1 required positional argument: 'query'` results and then `max tool iterations (6) reached`. The model wrote `<tool_call>{"name": "search", "query": "alex", "limit": 10}`, with its arguments at the top level, so they parsed as `{}`.

The model was never trained on that protocol. Its GGUF chat template (`tokenizer.chat_template`) defines tool calling:

- The tools are JSON function schemas, which the template renders into a `# Tools … <tools>…</tools>` system block with its own calling instructions.
- The model answers a call as `<tool_call>\n<function=NAME>\n<parameter=P>\nvalue\n</parameter>\n</function>\n</tool_call>`. This is the Qwen3-Coder/Qwen3.5 XML style, and one turn may contain several calls.
- An assistant message's `tool_calls` (each argument mapping) is rendered back in that markup.
- A `tool` role message is rendered as a `<tool_response>` block in a user turn.

The remote providers (OpenAI, Ollama, vLLM, Gemini and Anthropic through their OpenAI-compatible endpoints) also have native tool calling: `tools` in the request, streamed `tool_calls` deltas, and `role: "tool"` results carrying `tool_call_id`.

[Model selection](../local-model-selection.md) already listed this as a compatibility task. Native tool-calling scores say nothing about a custom parser, so a structured adapter with argument schemas was needed.

## Decision

Chat keeps **one registry** (`chat.TOOLS`). Each entry is the function that runs plus its JSON schema: a description, typed parameters and the required ones. Every backend speaks its native protocol from these schemas. No backend keeps the old text protocol, and no parser accepts other shapes.

- **Local llama.cpp.** The conversation is rendered through the model's embedded template. As before, `enable_thinking=false`. The schemas go in as `tools=[{"type": "function", "function": schema}]`, earlier calls as assistant `tool_calls` with mapping arguments, and results as `tool` messages. The context budget measures the prompt with the tool block included, and pruning removes an assistant turn together with its tool results. The reply is parsed in the template's markup. A string parameter is taken verbatim, including multi-line text. Any other schema type is read as JSON, so integers and booleans come from their text, and a value that does not parse stays text for the tool to reject. The stream still withholds text from the open tag on and holds back a trailing partial tag. The `</tool_call>` stop string is gone, because the model ends its turn itself. Text that is not a complete call in that markup is reply text. JSON inside `<tool_call>` tags is not a call.
- **Remote OpenAI-compatible providers.** Requests carry `tools`. Streamed `tool_calls` deltas are assembled by index: the id and name, plus the argument fragments. Results go back as `role: "tool"` with the call's `tool_call_id`, and a call's arguments travel as a JSON string. Arguments that are not a JSON object end the reply with a visible error. The Anthropic default now names its OpenAI-compatible endpoint (`https://api.anthropic.com/v1/`). Without it, the client used OpenAI's default URL and sent the Anthropic key there.
- **One loop.** A turn may make several calls. Each one emits `tool_call` (`name`, `args`), runs, emits `tool_result`, and is answered with its own tool message. `chat.max_tool_iters` bounds model turns.
- **Dispatch.** The model's `workspace` argument is still dropped in favour of the session's. Missing required parameters and unknown parameters are checked against the schema. Every failure, including a tool exception, becomes the result the model reads. Voice/read-only mode offers only the read tools' schemas and still rejects a write by name.
- **History.** A stored tool exchange (`role: "tool"`, `tool_name`, `tool_args`) replays as an assistant tool call followed by its tool result, in each backend's encoding.
- **System prompt.** It keeps only strategy guidance. The schema descriptions replace the prose tool list, and the template or API supplies the calling format.

## Alternatives

- **Accept the flat shape as well** (`obj.get("args") or obj.get("arguments") or the remaining keys`). This would fix the reproduced prompt but keep a protocol the model was never trained on. Every new model's drift would need another accepted shape.
- **Keep the custom prompt and constrain it with a grammar.** It would force well-formed JSON, but the model would still work outside its training distribution. The tool prose would stay in the system prompt, and remote providers would keep a text protocol beside their native one.
- **llama.cpp server with built-in chat parsers.** This would cover many template families, but it means running a separate server process. Today chat runs in-process through llama-cpp-python, which does not expose those parsers ([ADR 0035](0035-prebuilt-llama-wheels-and-chat-provisioning.md)).
- **Derive the schemas from the MCP server's FastMCP tools.** The chat set differs: `wake_up` is internal, `kg_add`/`kg_invalidate` are split, and search suppresses the wake preamble. The MCP descriptions are also kept terse to save schema bytes, and the session owns the workspace. A chat-specific registry stays explicit.

## Consequences

- The default local model receives tools the way it was trained, and the reproduced prompt produces a well-formed `search` call (see Validation).
- The tool schemas cost more prompt tokens than the old prose list. With Qwen3.5-4B's template, the tool block took 1,683 of the default 8,192-token context: a system prompt plus one user message measured 369 tokens without tools and 2,052 with them. The context budget counts the block, and older history is pruned first.
- Local tool calls are parsed only in the Qwen3.5/Qwen3-Coder markup that the managed model's template defines. A custom GGUF still renders through its own template, tools included. If its template asks for a different call format (Hermes-style JSON in `<tool_call>`, Llama or Gemma formats), its calls appear as text and do not run. Supporting such a model means a parser for its template family.
- vLLM must run with `--enable-auto-tool-choice` and a `--tool-call-parser` for its model. Without them it rejects requests that carry tools. This is a property of the server, not something Imprint can switch on.
- `tool_call`/`tool_result` events keep their shape, so the dashboard and the stored transcripts are unchanged. One reply may now carry several `tool_call`/`tool_result` pairs in sequence.

## Validation

- Unit tests drive the real wire formats at the boundary. The local tests feed streamed template markup through a stand-in model and check the schema-typed arguments, verbatim multi-line strings, several calls in one turn, the native assistant/tool messages on the next request, `tools` passed to the template and the generation, no stop string, and that flat JSON in tags is not a call. The remote tests stream `tool_calls` deltas (split arguments and parallel calls) and check the JSON-string arguments and `tool_call_id` results on the next request, plus the error for malformed arguments. Shared checks cover history replay for both encodings, a missing required argument returned as the tool result, voice offering only read tools and rejecting writes, the session workspace overriding the model's, context pruning of whole tool turns, and cancellation closing the provider stream before a tool runs.
- A live read-only probe on Windows used the installed runtime's interpreter, llama-cpp-python 0.3.36 and the pinned Qwen3.5-4B Q4_K_M GGUF. It rendered prompts through the embedded template and ran local turns without executing any tool against user data:
  - "what can you tell me about alex?" produced `<tool_call>\n<function=search>\n<parameter=query>\nalex\n</parameter>\n<parameter=limit>\n10\n</parameter>\n</function>\n</tool_call>`, which parsed as `search {"query": "alex", "limit": 10}`.
  - A request for chunks 0 through 2 of a file parsed as `file_chunks {"source": "imprint/chat.py", "start": 0, "end": 2}`.
  - "What is 17 times 3?" was answered directly with no call.
  - After a synthetic `tool` result, the template rendered it as a `<tool_response>` block, and the model's next turn was another native call.
  - A full `stream_reply` loop with every tool stubbed answered the alex question after one search.
- `scripts/smoke-local-helper.py` was started against the same GGUF on CPU. While other workloads saturated the machine, it did not get past its first classification stage within 15 minutes, so it gives no result here.
- Not validated:
  - live remote providers, which need keys and servers;
  - how the Gemini and Anthropic compatibility layers handle streamed tool-call ids and parameterless tools (`properties: {}`);
  - vLLM tool parsers;
  - custom GGUFs with other tool formats.
