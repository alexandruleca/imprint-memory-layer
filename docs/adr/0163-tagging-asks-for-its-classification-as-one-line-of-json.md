---
title: ADR 0163 — Tagging asks for its classification as one line of JSON
description: The classification prompt asks for the JSON on one line, which halves the local model's reply and cuts a memory's tagging time by about 30% on a GPU, with types and languages as before and about a third fewer domains and topics.
---

- Status: Accepted
- Date: 2026-11-09
- Related: [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md) (classification without a grammar), [ADR 0078](0078-keep-qwen3-5-4b-as-the-local-tagger.md) (the tagger and its earlier evaluation), [ADR 0164](0164-the-tagging-prompt-keeps-its-layout-despite-checkpoints.md), [ADR 0165](0165-the-local-model-tags-several-memories-as-one-batch.md), [ADR 0166](0166-tagging-never-asks-a-model-to-think.md), [tagging](../tagging.md#prompt-batches-and-thinking)

## Context

On 2026-11-09 the owner asked why tagging took 4 s per memory on their desktop: Qwen3.5-4B Q4_K_M on a Radeon 8060S over Vulkan, with 174,959 memories waiting.

- **Where the time went.** Profiled on 8 waiting memories, the time per memory was 3.3 s:
  - Reading the prompt (about 1,000 tokens at about 740 tokens/s): 1.3 s.
  - Generating the reply (about 90 tokens at 44 tokens/s): 2.0 s.
  - Everything else (Qdrant, registry, redaction): 4.5 ms.
- **What the reply looked like.** Indented JSON, a key or a list item per line. Line breaks and indentation were a large share of its tokens.
- **No grammar.** ADR 0049 dropped the JSON-schema grammar for classification because on a GPU it cost more than the inference, so nothing forced a compact layout.

## Decision

The classification prompt's last rules are now:

```
- Return ONLY the JSON object, no explanation
- Write the JSON on one line: no line breaks, no indentation
```

The reply is parsed and validated as before. The prompt's layout and its other rules are unchanged (ADR 0164).

## Rejected alternatives

Measured against the previous prompt (indented replies) on random waiting memories. Types and languages were also checked against labels written by hand before any output was read (see *Validation*).

| Variant | Where | Reply tokens (median) | Time per memory | Domains / topics per memory | Domains from the registry | Acceptable type (of 20) | Correct language (of 20) |
| --- | --- | ---: | ---: | --- | ---: | ---: | ---: |
| Previous prompt, two runs | GPU, 48 | 76–78 | 2.99 / 2.94 s | 3.4 / 3.5 | 52–53% | 18, 18 | 20, 18 |
| "compact on one line (no line breaks, no indentation)" in place of the last rule | GPU, 48 | 34 | 1.95 s | 2.0 / 2.5 | 72% | 19 | 19 |
| "on a single line, without line breaks or indentation" in the last rule | CPU, 20 | 36 | — | 2.3 / 2.5 | 72% | 18 | 19 |
| **This decision**: the last rule kept, a formatting rule added | CPU, 20 | 36 | — | 2.4 / 2.6 | 74% | 19 | 19 |
| **This decision**, two runs | GPU, 48 | 37 | 2.0–2.3 s | 2.4 / 2.7 | 65–66% | 19, 19 | 19, 19 |

- **The other wordings.** Same speed, a little more terse or a little worse at types.
- **A JSON-schema grammar** that forces a compact reply without asking. ADR 0049 measured that it cost more than the inference on a GPU; it was not measured again.
- **Indented JSON**, as before. About twice the reply tokens; generating the reply was the largest part of a memory's time.

## Consequences

- **Speed.** On the 8060S, a memory took 2.0–2.3 s with this decision's wording (replies of 37 tokens), against 2.99 s before, and 1.95 s with the first wording (34 tokens). At that rate the desktop's backlog takes about 4 days instead of about 6; [ADR 0165](0165-the-local-model-tags-several-memories-as-one-batch.md)'s batches take it further.
- **Fewer labels.** Replies carry about a third fewer domains and topics: 2.4 and 2.6 per memory instead of 3.4 and 3.5. The labels dropped were of mixed precision.
  - The previous prompt also gave `animation` and `ml` for floor-plan coordinates, and `react-native` for a Twig and Bootstrap fix.
  - The new replies had their own misses, such as `s3` and topics copied from a path.
  - More of the new domains come from the registry, which keeps filters consistent across memories.
- **Types and languages** are as good as before on the 20 hand-labelled memories. On the GPU's 48 memories, the new wording agreed with the previous prompt on 96% of types, as often as two runs of the previous prompt agreed with each other. Layers and kinds moved more: 69–71% and 44–46% agreement, against 83% and 67% between two runs of the previous prompt.
- **Other providers** get the same prompt, and their replies are parsed the same way.

## Invalidation conditions

- If search or filtering suffers from the fewer domains and topics, ask for label counts explicitly, or return to indented replies.
- If constrained decoding becomes cheaper than the tokens it saves, use the grammar instead of the instruction.
- If the default tagging model changes, measure the reply length and the label counts again.

## Validation

- `tests/test_tagging_throughput.py` (`PromptTests`), run on Windows 11 with the Python 3.13 runtime: the prompt asks for one line, keeps its layout, and a one-line reply parses.
- Executed on 2026-11-09 on the owner's desktop, against the live Qdrant server, without storing anything:
  - **GPU.** `tag_eval.py`: 48 random waiting memories, two runs of the previous prompt and the first wording.
  - **CPU.** While Smart App Control blocked the GPU runtime, the same GGUF ran on llama.cpp's Linux CPU build under WSL (`tag_eval_cpu.py`, `tag_eval_wording.py`) on 20 of the 48, to choose the wording. CPU and GPU runs of the same prompt agreed with each other as well as two GPU runs (type 90%, domain overlap 0.76).
  - **GPU, this decision.** Once the owner turned Smart App Control off: `gpu_final.py`, two runs on the 48 memories, and `gpu_ab.py`, six alternating rounds of 8 for the timing (the 48-memory runs took 2.41 and 3.07 s per memory while other work loaded the machine).
- **Hand labels.** The acceptable types and the language of the 20 memories were written by the assistant that ran the evaluation (Claude), from their text, before reading any output, as in ADR 0078.
- **Limits.**
  - The waiting memories were all indexed files, mostly JSON output of one project; conversations were not in the sample.
  - This is a private evaluation whose scripts are in the session scratchpad, so it does not meet [ADR 0019](0019-reproducible-benchmark-evidence.md)'s bar.
  - Not measured: macOS, and remote providers' reply lengths.
