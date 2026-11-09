---
title: ADR 0164 — The tagging prompt keeps its layout despite prompt checkpoints
description: With prompt checkpoints (ADR 0161), putting the tagger's fixed instructions first would roughly halve a memory's time, but in every measured arrangement Qwen3.5-4B then classified by the file path more than by the text, so the project and file stay first, then the instructions, then the memory.
---

- Status: Accepted
- Date: 2026-11-09
- Amends: [ADR 0078](0078-keep-qwen3-5-4b-as-the-local-tagger.md) — its invalidation condition on putting the fixed instructions first: with prompt checkpoints it was measured, and it changes the classifications, so the layout stays
- Related: [ADR 0161](0161-chat-keeps-its-instructions-a-reusable-prompt-prefix.md) (prompt checkpoints), [ADR 0163](0163-tagging-asks-for-its-classification-as-one-line-of-json.md), [ADR 0165](0165-the-local-model-tags-several-memories-as-one-batch.md), [tagging](../tagging.md#prompt-batches-and-thinking)

## Context

- **ADR 0078's condition.** ADR 0078 kept the classification prompt's layout: the memory's project and file, then the fixed instructions with the registry's most used labels, then the memory's text. Reordering gained nothing then, because llama.cpp cannot cut a hybrid model's state back to a shared prefix. Its invalidation condition: *If the pinned llama-cpp-python gains prefix reuse for `qwen35` (for example by state checkpoints), putting the fixed instructions first becomes a free speed-up: reorder.*
- **The condition is met.** ADR 0161 (2026-11-08) gave Imprint its own state checkpoints, for chat.
- **What reordering would save.** On 2026-11-09 the owner asked why tagging took 4 s per memory on their desktop (Radeon 8060S, Vulkan). Of a prompt's roughly 1,000 tokens there, about 540 are the fixed part, identical for every memory while the registry's order holds. At about 740 tokens/s of prefill, that is about 0.7 s per memory.

## Decision

The layout stays: project and file, then the instructions, then the memory's text. The tagger passes no stable prefix to the local model. Chat's checkpoints (ADR 0161) are unchanged.

ADR 0078's invalidation condition no longer holds. Reordering is faster, but not free: in every arrangement measured, it changes the classifications.

## Rejected alternatives

All measured on random memories waiting for tagging on the owner's desktop, with a one-line reply ([ADR 0163](0163-tagging-asks-for-its-classification-as-one-line-of-json.md)'s wording, or its first draft). The reference is the current layout with the same reply wording. For comparison, two runs of the old prompt agreed with each other on 90–96% of types, with a domain overlap (Jaccard) of 0.74–0.83.

- **The instructions as a system message, the memory as the user message, kept evaluated.** GPU, 48 memories:
  - 1.18 s per memory, against 1.95 s.
  - 9% of domains came from the registry, against 72%.
  - It agreed with the old prompt on 27% of types.
  - Its type was acceptable for 13 of 20 hand-labelled memories, against 19.
- **The instructions first in the one user message, then project, file and text.** CPU, 20 memories, with and without a `Chunk:` label before the text:
  - 7.3 s per memory, against 22.2 s.
  - Its type was acceptable for 9 and 7 of 20 memories, against 17.
  - 29–31% of domains came from the registry, against 77%.
  - It agreed with the old prompt on 15% of types.
- **The instructions first, then the text, then project and file.** CPU, 20 memories:
  - 6.7 s per memory, against 19.9 s.
  - Its type was acceptable for 17 of 20 memories, against 19.
  - It agreed with the old prompt on 35–50% of layers (two runs of the old prompt: 85%), with a domain overlap of 0.16–0.18.
  - 41% of domains came from the registry, against 74%.
- **Whether the checkpoint, not the layout, was at fault.** Five memories were decoded greedily, instructions first, both evaluated fresh and restored from the checkpoint. The outputs were identical for three. For the other two, the restored and fresh answers differed in some labels, one of them in its type, and were equally plausible. The current layout gave a different type from both on four of the five. The layout changes the answers; the restore does not.

## Consequences

- Tagging keeps evaluating the fixed instructions for every memory, about 0.7 s on the 8060S.
- Batches (ADR 0165) cannot share the instructions between sequences: each sequence reads its whole prompt.
- With the instructions first, the model labelled test fixtures `source` or `data`, and a fix `decision` instead of `bug`. A file path next to the text appears to outweigh the text; the current layout keeps the path above the instructions.

## Invalidation conditions

- If the default tagging model changes, measure the instructions-first layouts again with that model.
- If the fixed part grows enough to dominate the prompt (more registry labels, longer rules), weigh its cost against these results again.
- If llama.cpp gains reuse of a fixed block that does not start the prompt, use it.

## Validation

Executed on 2026-11-09 on the owner's desktop (Windows 11, AMD Radeon 8060S over Vulkan, WSL2 with 4 CPU threads), against the live Qdrant server, without storing anything:

- `tag_eval.py` (GPU), on 48 random waiting memories: the system-message layout, with timings and agreement against two runs of the old prompt.
- Smart App Control began blocking llama.cpp's unsigned DLLs on Windows at 07:28 that day. The other layouts therefore ran on CPU, under WSL with llama.cpp's Linux CPU build and the same GGUF (`tag_eval_cpu.py`, `tag_eval_wording.py`, `restore_check.py`), on 20 of the 48 memories. CPU and GPU runs of the same prompt agreed with each other about as well as two GPU runs (type 90%, domain overlap 0.76).
- **Labels.** The acceptable types and the language of the 20 memories were written by the assistant that ran the evaluation (Claude), from their text, before reading any variant's output, as in ADR 0078.
- **Sample.** The waiting memories on this device were all indexed files, mostly JSON output of one project. Conversations were not in the sample.
- **Limits.** This is a private evaluation. The scripts and outputs are in the session scratchpad and are not committed, so it does not meet [ADR 0019](0019-reproducible-benchmark-evidence.md)'s bar and supports only this decision.
