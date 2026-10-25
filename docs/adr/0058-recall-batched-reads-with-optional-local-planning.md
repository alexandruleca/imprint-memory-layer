---
title: ADR 0058 — Recall: batched reads with optional local planning
description: One additive recall MCP tool runs several read steps in one call under one budget; an opt-in model on this computer may plan the steps and answer with checked citations.
---

# 0058 — Recall: batched reads with optional local planning

- Status: Accepted
- Date: 2026-10-25
- Related: [ADR 0021](0021-budgeted-mcp-evidence-and-selective-expansion.md) (budgeted evidence), [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md) (hybrid retrieval), [ADR 0024](0024-enforce-local-service-boundaries.md) (local boundaries), [ADR 0035](0035-prebuilt-llama-wheels-and-chat-provisioning.md) and [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md) (local helper model)

## Context

A question with several parts ("how does Imprint serialize ingest, and how does the reference project queue work?") takes several Imprint calls today: a search per part, a fact lookup, perhaps a file read. Each call is a round trip through the caller's model, and each result enters the caller's context whole. Search alone defaults to 4,000 characters (ADR 0021).

Three published approaches attack the same cost:

- Anthropic, [Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) (2025-11-04): "Every intermediate result must pass through the model." The agent instead calls MCP tools from code in an execution environment and returns only what it needs. In its example, token use falls "from 150,000 tokens to 2,000 tokens". It also warns that running agent code "requires a secure execution environment with appropriate sandboxing", whose "operational overhead and security considerations" direct tool calls avoid.
- Anthropic, [Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) (2025-11-24), Programmatic Tool Calling: "Each tool call requires a full model inference pass." Orchestrating calls in code cut average usage "from 43,588 to 27,297 tokens, a 37% reduction on complex research tasks". The article calls it most beneficial for three or more dependent calls or parallel operations, and less so for "quick lookups with small responses".
- Cloudflare, [Code Mode](https://blog.cloudflare.com/code-mode/) (2025-09-26): converts MCP tools into a TypeScript API that generated code calls inside isolated V8 sandboxes. "When the LLM can write code, it can skip all that, and only read back the final results it needs."

The savings come from one round trip, and from intermediate results that never enter the caller's context.

The maintainer proposed a layer over the existing MCP tools. The caller would say what it wants. When local LLM inference is on, the layer would decide which tools to batch, turning about ten calls into one; otherwise Imprint would behave as it does now. The approved design: one new tool, existing tools unchanged, a deterministic plan path that always works, and a model path only for a model on this computer.

Measured constraints on this machine (i9-13900HX, WSL2, CPU, four llama.cpp threads, Qwen3.5-4B Q4_K_M): prompt processing runs at about 75 tokens/s and generation at about 13 tokens/s. LLM tagging took 34–38 s per exchange (ADR 0049).

## Decision

**One additive tool.** `recall(question, plan=None, max_chars=4000, condense=True, workspace="")` is read-only. The 16 existing tools keep their behavior and output; Imprint now has 17.

**Plans.** A plan is up to 8 steps, `{"tool": name, "args": {...}}`, over `search`, `get_memory`, `neighbors`, `file_chunks`, `file_summary`, `list_sources` and `kg_query`. These are the reads that return evidence; `graph_scope`, `status` and `device_context` return navigation and diagnostics. Writes are never steps.

- The read tools were split into normalize-and-fetch functions (`server.search_rows`, `memory_row`, `neighbor_rows`, `chunk_rows`, `summary_row`, `source_rows`, `fact_rows`) and their existing formatting. A recall step calls exactly the function its tool calls, so clamps, filters and workspace scope cannot drift apart.
- A step's arguments are its function's parameters, as a pydantic model derived from the signature. Workspace and output sizes are not parameters, so a step cannot change the workspace.
- A caller's invalid plan is rejected before anything runs. A step that fails while running reports its error; the others still return.
- Steps are independent and run concurrently. Records merge by key: memory id (shared by search hits, file chunks, neighbors and `get_memory`), `fact:<id>` and `file:<source>`. Each step's results enter in turn by rank, and a repeated memory keeps the longer content.
- One `max_chars` budget (1,200–12,000, default 4,000, the same range and default as search) bounds the response. It holds a line per step, then numbered blocks in search's provenance format. Every shown excerpt gets the widest common width of at least 300 characters, and a final line counts hidden results.
- Steps cannot reference earlier steps' results. Most "expand the top hits" needs are met by the excerpt of each hit, and a second recall covers the rest. References would add ordering, a reference syntax and a harder grammar for a small planner.

**Without a plan,** recall returns exactly the compact `search` response for the question.

**Local inference** is on when `recall.llm` is true (default false, `IMPRINT_RECALL_LLM`) and the tagger's provider runs on this computer:

- `local`: llama.cpp in the MCP process, using `tagger.local.*`;
- `ollama` or `vllm`, at a base URL whose host is loopback.

Anthropic, OpenAI, Gemini and LAN servers never enable it, and recall then adds a one-line note. Recall reuses the tagger's provider plumbing rather than adding a fourth set of provider keys. It never downloads a model: a read-only, closed-world tool does not fetch 2.7 GB. When local inference is on:

- **Planning.** Without a plan, the model receives the question, the workspace's project names and the read tools. It returns `{"steps": [...]}` under a JSON-schema grammar: llama.cpp `response_format`, or OpenAI-compatible `json_schema` for ollama/vllm. The planner may set only the arguments it can know: search `query`/`project`, ids, file paths and ranges, and fact subject/predicate. On the first live trial, it emptied two steps with `type="preference"` and `type="architecture"` filters on a "how" question.
- **Validation.** The model's steps go through the same validator as callers' steps. Invalid steps are dropped and counted. The plain search of the question is always step 1, so the model can only add evidence.
- **Fallback.** Unparseable output, no valid step, a timeout or a model error returns the plain search with a note.
- **Answering.** Unless `condense=false`, the model answers from exactly the budgeted evidence the caller would otherwise receive, citing evidence numbers. An answer without a citation, or citing evidence that was not shown, is discarded and the evidence returns with a note. The condensed response holds the plan, the answer, the meta lines of the cited evidence and the keys of the rest.
- **Time limit.** Planning and answering share `recall.llm_timeout_sec` (default 30). Model work runs in a worker thread. `local_llm.complete` now streams internally and accepts a deadline: it does not start after the deadline, including while it waits for the shared inference lock, and it stops at the next token past it. Prompt processing cannot be interrupted and finishes in the background. The ollama/vllm client gets the remaining time as its HTTP timeout.

## Alternatives

- **Code-execution sandbox (Code Mode / code execution with MCP).** This is the most general option, with loops, conditionals and chaining. But it needs a sandbox for arbitrary code inside a local stdio server installed on Windows, macOS and Linux: a V8 isolate or a hardened Python sandbox, resource limits and a typed API surface. That adds a new code-execution attack surface next to users' memories. Imprint's reads are a small set, and typical lookups fan out rather than chain, so a declarative plan covers them without executing code. Clients that support programmatic tool calling can already orchestrate Imprint's tools on their side.
- **An intent-only tool, gated on the LLM.** A tool that exists only when a model is on hides behavior behind configuration, gives callers no control over which reads run, and on CPU waits tens of seconds before answering (measured below). The plan path works on every installation, and the model only adds to it.
- **Deterministic batching only.** This is the core of the decision and could ship alone. The model layer exists because the maintainer asked for it. It is off by default, local-only, bounded in time and backed by a deterministic fallback, and its measured cost is recorded here.
- **Remote providers for planning or answering.** Rejected: recall questions and memory evidence would leave the computer, contrary to "only with LLM inference turned on locally".
- **Separate `recall.provider`/`recall.model`/`recall.base_url` keys.** This would duplicate the provider plumbing a fourth time (tagger, chat, summarizer). Recall uses the tagger's provider settings with its own on/off switch, so tagging and recall stay independent.
- **Changing `search` to accept several queries.** Rejected: existing tool semantics stay unchanged, and fact and file reads would still need separate calls.

## Consequences

Measured over eight multi-part lookups (details in [benchmarks](../benchmarks.md#recall-lookups); tokens are a Qwen3-tokenizer proxy):

- **Round trips:** 21 sequential calls become 8 recalls.
- **Default budget:** caller tokens fall 45% (19,288 → 10,681), mostly because of the shared 4,000-character budget. Two fewer gold sources are visible (21/26 vs 23/26).
- **Matched size:** given the characters the separate calls returned, recall uses 3% fewer tokens and shows one more gold source (24/26).
- **Server time:** concurrent steps cut it by about 18% (1.66 s → 1.36 s total).
- **Local model on CPU, given time:** it plans in 11–21 s and answers in 24–44 s (median about 47 s per intent-only recall, 32 s per plan plus answer). Caller tokens fall to about 5,300–5,800, but only the 15–17 cited gold sources are named. Several runs took minutes while other work loaded the host.
- **Local model at the 30 s default:** it answered 1 of 16 recalls. A timed-out prompt keeps the CPU busy and delays the next recall's planning.
- **Verdict:** the model path is too slow on this CPU for interactive use. It needs a GPU or a fast loopback server, which was not measured. The default stays 30 s because some clients (Codex, for one) default to a 60 s MCP tool timeout; CPU users can raise it.
- **Answer quality:** most answers matched their cited evidence. At least two misstated it: startup races credited to `start_new_session` instead of the start lock, and an invented fifth tagging layer.

- The tool list grows by one definition of about 377 tokens (Qwen3 tokenizer proxy; all 17 tools: about 3,250). Clients that load tool definitions upfront pay this once per session.
- A shared 4,000-character budget hides more evidence than several 4,000-character responses. Callers who need breadth raise `max_chars`, up to 12,000, still one round trip. Hidden results are counted, never silently skipped.
- With `local`, each MCP server process holds its own model copy (about 3 GB for Qwen3.5-4B) after its first recall. ollama or vllm share one.
- A timed-out local request keeps the CPU busy until its prompt is processed and its next token is generated. A recall that queues behind it gives up at its own deadline without starting.
- The citation gate establishes traceability, not truth. A 4B model can still misstate or misattribute what cited evidence says; one trial before the attribution instruction did. Callers are told to verify cited evidence, and `condense=false` returns the evidence itself.
- `local_llm.complete` streams for every role. Tagging and summaries produce the same text; their tests now mock streamed responses.
- `vectorstore.get_chunks_by_source` also returns each chunk's id, project and source (additive), so file chunks merge with search hits.
- The dashboard Settings page shows the new `recall` group; the static export is rebuilt by release CI.

## Validation

- `tests/test_recall.py` (19 tests) covers:
  - plan validation (unknown tools, writes, unknown or mistyped arguments, a workspace argument, deduplication, too many steps);
  - concurrent execution (two steps meeting at a barrier);
  - merging by id across search, `get_memory`, file chunks and neighbors, with fullest content and round-robin order;
  - a failing step beside working ones;
  - hard budgets from 1,200 to 12,000 characters, with counted hidden results and minimum excerpt width;
  - byte-identical output to `search` without a plan or model;
  - workspace isolation of every read and of the planner's project list;
  - the model path with a fake model: plan, baseline kept, answer, citations and the condensed view;
  - `condense=false`;
  - planning timeout, invalid plans and a missing model falling back to the search;
  - answer timeout and untraceable answers falling back to the evidence;
  - one deadline across planning and answering;
  - local-inference detection (`local`, loopback ollama/vllm including IPv6, LAN and remote providers refused);
  - a remote provider never being called;
  - MCP registration (17 tools, read-only annotation, parameters).
- `tests/test_models.py` covers the streamed `complete` and its deadline: not started when late, stopped mid-stream with the stream closed.
- Full Python suite: 382 tests pass.
- Benchmark: `benchmark/runtime.py recall` over `benchmark/recall.json`. That is eight multi-part lookups over the frozen public corpus (revision `fdea98ae`), with gold sources declared beforehand. Evidence is in `benchmark/published/recall-20261025T081236Z/`; method and results are in [benchmarks](../benchmarks.md#recall-lookups).

- Byte-identity: on the benchmark index, every read tool returned the same output before and after the refactor, run on the parent commit and on this change: search in hybrid, semantic and full detail, its error paths, kg_query, list_sources, file_summary, file_chunks and its error path, and neighbors. `get_memory` differed only in `path_context.working_directory`, because the two runs had different working directories.
- The first full benchmark run found the shared graph-connection race, fixed separately. The published run is on fixed code; recall outputs are identical across repeated runs.

Not validated:

- ollama or vllm against a live server; only the client path and the loopback rule are unit-tested;
- GPU (Vulkan or Metal) latency;
- Windows and macOS runtimes;
- a Claude Code session end to end. The benchmark counts tool-call and tool-result tokens with a proxy tokenizer and measures server time; it does not count the caller model turns each extra round trip costs.
- the dashboard Settings page, whose source changed but which was not rebuilt or rendered.
