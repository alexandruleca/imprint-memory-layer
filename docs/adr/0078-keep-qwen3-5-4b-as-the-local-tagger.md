---
title: ADR 0078 — Keep Qwen3.5-4B as the local tagger
description: Qwen3.5-2B was measured against the default on 50 real memories; it tags 2.2–2.5× faster and no worse at memory types, but worse at the language and topic tags search filters on, so the default stays.
---

- Status: Accepted
- Date: 2026-10-26
- Related: [ADR 0010](0010-qwen-defaults-and-explicit-reindex.md) (the Qwen defaults), [ADR 0019](0019-reproducible-benchmark-evidence.md) (this evaluation does not meet its bar), [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md) (tagging on the GPU), [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md) (tagging runs in the background), [local model selection](../local-model-selection.md), [tagging](../tagging.md)

## Context

On 2026-10-26 the owner asked whether a lighter tagging model would make tagging faster without giving up much quality.

- **Cost.** Since ADR 0067 nothing waits for LLM tagging. Its cost is how long the background job keeps the machine busy and the model in memory. ADR 0049 measured the default on the owner's machine at 38.4 s per exchange on CPU and 8.0 s with the Vulkan build.
- **The default.** Qwen3.5-4B Q4_K_M (`unsloth/Qwen3.5-4B-GGUF` at `e87f176`, pinned by checksum in `model_profiles.py`). The local summarizer uses the same model.
- **No prior quality evidence.** [Local model selection](../local-model-selection.md) names Qwen3.5-2B as the small-machine candidate, but no tagging quality had been measured for any model. It also asks for at least 50 cases before a default changes.

## Decision

Qwen3.5-4B Q4_K_M stays the default local tagger, and the classification prompt keeps its layout. Another GGUF remains a setting away (`tagger.local.model_repo`, `tagger.local.model_file`); a file other than the pinned one is loaded without a checksum.

## Rejected alternatives

- **Qwen3.5-2B Q4_K_M as the default** (`unsloth/Qwen3.5-2B-GGUF` at `f6d5376`). Measured on 50 memories (see *Validation*):
  - **Speed:** 2.2× faster on the GPU and 2.5× on the CPU.
  - **Memory types:** no worse.
  - **Language:** wrong on 5 of 50, against 1 for the 4B. It labelled both Romanian conversations and three JSON chunks `english`.
  - **Topics:** clearly wrong on 6 of 50, against 2, and vague on 4 more. Most of its misses tagged the harness text that opens some conversation chunks instead of their content.

  Language, domains and topics are the search filters tagging exists to set ([tagging](../tagging.md)). The summarizer would also have moved to the smaller model.
- **Putting the prompt's fixed instructions first, so llama.cpp reuses their evaluation.** They are 517 of about 1,243 tokens, and the prompt begins with the memory's project and file. But the GGUF architecture is `qwen35`, part recurrent, and llama.cpp cannot roll its state back to a shared prefix.
  - **Measured:** in three pairs of calls on the 4B, a call after a prompt sharing its first 517 tokens took as long as the same call alone: 24.8 vs 23.1 s, 14.7 vs 14.7 s, 18.7 vs 18.6 s.
  - **Output:** identical in every pair.

## Consequences

- Tagging stays at about 3.2 s per memory on an RTX 4080 Laptop GPU with the default Vulkan build, and about 18.5 s on CPU with 4 threads. A CPU-only machine drains a backlog about 2.5 times slower than it would with the 2B.
- The evaluation also showed weaknesses the default keeps. Neither model size fixes them, and they are not addressed here:
  - The 4B labels 18 of 50 memories `decision`, most of them progress reports.
  - For code it returns types the rules do not name (`test`, `source`).
  - Both models rarely give conversations the `session` layer and `qa` kind the learner sets.
  - Some conversation chunks open with text the client injected (for example "Another Claude session sent a message…"). The learner's cleanup keeps it, and both models tag it.

## Invalidation conditions

- If a smaller model matches the 4B's language and topic results on such an evaluation, switch to it for its speed.
- If the summarizer gets its own model setting, the tagger can be chosen on tagging alone: evaluate again.
- If the pinned llama-cpp-python gains prefix reuse for `qwen35` (for example by state checkpoints), putting the fixed instructions first becomes a free speed-up: reorder.
- If the learner starts stripping injected text from conversations, or the tagging prompt changes, run the comparison again: much of the 2B's topic error came from that text.

## Validation

Executed on 2026-10-26 on the owner's laptop (i9-13900HX, RTX 4080 Laptop GPU, Windows 11 build 26200 with WSL2).

**Sample.** 50 memories drawn with a fixed seed from this machine's data:

- 30 conversation chunks from Claude Code transcripts, made by the learner's `parse_exchanges` and `chunk_conversation`.
- 20 code and documentation chunks from local repositories, split without model inference. 12 of the 20 are vendored or generated files that an ingest would normally skip.

The taxonomy was frozen at its seed values, and each prompt was built as background tagging builds it (`rel_path` set to the source, the project hint from it).

**Agreement.** llama-cpp-python 0.3.36 on CPU; the 4B ran twice, the 2B once.

| Comparison | type | lang | Domain overlap (Jaccard) | Topic overlap (Jaccard) |
| --- | --- | --- | --- | --- |
| 4B with itself | 94% | 98% | 0.92 | 0.60 |
| 2B with 4B | 54% | 90% | 0.40 | 0.05 |

**Correctness.** The assistant that ran the evaluation (Claude) wrote, for every memory, the types the prompt's rules allow and the correct language. It wrote them for memories 11–49 before seeing either model's output, and for memories 0–10 after.

| Run | type correct (all 50) | type correct (39 blind) | lang correct |
| --- | --- | --- | --- |
| 4B, first run | 35 | 26 | 49 |
| 4B, second run | 33 | 24 | 48 |
| 2B | 38 | 30 | 45 |

Topics judged against each memory's content: clearly wrong for 2 memories with the 4B, and for 6 with the 2B plus 4 vague.

**Speed.** Sequential, through Imprint's own `local_llm.load` and `complete`:

| Build | 4B median | 2B median |
| --- | --- | --- |
| Windows, Vulkan build from `llm-gpu.lock.txt` (20 prompts each) | 3.23 s | 1.47 s |
| WSL2, CPU, 4 threads (10 prompts each) | 18.5 s | 7.3 s |

**Prefix reuse.** On the 4B with the CPU build, as described under *Rejected alternatives*.

**Limits.** This is a private evaluation. The sample is the owner's own conversations and code, the labels are one judge's, and the harness and its outputs are not committed. It does not meet ADR 0019's bar for benchmark claims and supports only this decision. Not measured: summary quality with the 2B, other small models, and macOS.
