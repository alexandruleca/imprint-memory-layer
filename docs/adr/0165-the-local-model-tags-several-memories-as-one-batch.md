---
title: ADR 0165 — The local model tags several memories as one batch
description: tagger.llm_concurrency now applies to the local model — a second llama.cpp context on the loaded model decodes that many memories at once, each in its own sequence — because generating a token reads the whole model whether it serves one sequence or several; the default stays 1.
---

- Status: Accepted
- Date: 2026-11-09
- Related: [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md) (the background job), [ADR 0087](0087-llama-cpp-runs-each-model-on-one-gpu.md) (one GPU per model), [ADR 0125](0125-integrated-gpus-can-be-chosen-beside-a-discrete-one.md) (the integrated GPU measured here), [ADR 0163](0163-tagging-asks-for-its-classification-as-one-line-of-json.md), [ADR 0164](0164-the-tagging-prompt-keeps-its-layout-despite-checkpoints.md), [ADR 0166](0166-tagging-never-asks-a-model-to-think.md), [tagging](../tagging.md#prompt-batches-and-thinking)

## Context

On 2026-11-09 the owner asked why tagging took 4 s per memory on their Windows desktop, then asked for a concurrency option. The desktop has an AMD Ryzen AI Max with a Radeon 8060S integrated GPU (`model.gpu_device` 1, 112 GB shared memory, Vulkan) and a GTX 1060 6 GB.

- **The setting existed only for servers.** `tagger.llm_concurrency` (1–8) sent that many requests at once to a server provider. The local provider ignored it: "one model in memory runs one request at once; more would only wait in line" (`config_help.py`).
- **Where the time went** (8 waiting memories, the prompt before ADR 0163):
  - Prefill: about 1,000 prompt tokens at about 740 tokens/s, 1.3 s.
  - Decode: about 90 reply tokens at 44 tokens/s, 2.0 s.
  - Prefill speed was the same at batch sizes of 256, 512 and 1,024.
  - The GTX 1060 took 7.5 s per memory.
- **Why batching helps decode.** Decoding at 44 tokens/s reads the 2.74 GB GGUF at about 120 GB/s, roughly half of the 8060S's 256 GB/s memory bandwidth. A step that decodes one token for each of several sequences reads the weights once. Prefill is compute-bound, so a batch does not shorten it.
- **The binding.** llama-cpp-python's `Llama` generates one sequence; its `n_seq_max` is set only for embeddings.

## Decision

`tagger.llm_concurrency` applies to the local provider too, and the default stays 1. With a value above 1, the background job and `imprint retag` hand each group of that many memories to `local_llm.complete_many`:

1. **A second context.** On first use, a second llama.cpp context is created on the already-loaded model, with `n_seq_max` equal to the setting. The KV cache is not unified, so each sequence has 4,096 tokens. The weights are shared; the context's memory is the sequences' attention cache and recurrent state.
2. **Prefill, then lockstep decode.** The group's prompts are prefilled together, 512 tokens at a time, each in its own sequence. Each sequence samples its first token right after the decode that ended its prompt. Then every step decodes one token for each sequence still generating, until each reaches its end-of-generation token or the 200-token cap.
3. **The same sampling.** Each sequence has its own sampler with the parameters `generate` uses at temperature 0.1 (repetition penalty, top-k, top-p, min-p).
4. **Fallbacks.**
   - A prompt that would not fit its 4,096-token sequence, and a group of one, are completed alone as before.
   - If the batch context cannot be created or a batch fails (most likely a GPU that cannot hold it), the process prints why and completes requests one at a time from then on.
   - Results come back in order, and each memory is still written in its own payload update.

Pages hold whole groups: 16 memories with 1, 2, 4 or 8 slots, 15 with 3 or 5, 12 with 6 and 14 with 7.

## Rejected alternatives

- **One thread per request on the same model, or several copies of the model.** One model runs one decode at a time, so threads would only wait. Copies would each read all of the weights for every token, so throughput would not rise, and memory would grow with each copy.
- **llama.cpp's server with parallel slots, reached through the OpenAI-compatible provider.** It batches the same way, but the install ships the binding's library, not the server, and it would be a second runtime to install, start and supervise. Users can still point `vllm` or `openai` at such a server (ADR 0166 turns its thinking off).
- **Sharing the fixed instructions between sequences.** It would save each sequence about 540 tokens of prefill. It needs the instructions at the start of the prompt, which changed the classifications (ADR 0164).
- **Continuous batching, refilling a sequence as soon as its reply ends.** One-line replies (ADR 0163) are 30–40 tokens, so sequences finish within a few steps of each other. Whole groups keep the drain loop's order, its writes and its stops between groups.
- **A default above 1.** Each slot costs a 4,096-token context. That is 128 MiB of attention cache (8 attention layers × 4 KV heads × 256 dimensions × K and V × 2 bytes = 32 KiB per token) and about 50 MiB of recurrent state for Qwen3.5-4B: about 0.7 GiB for 4 slots. Devices with little GPU memory were not measured, and on a CPU a batch gains little (see *Consequences*). The owner sets the value per device.

## Consequences

- **The gain on a GPU is on decode only.** Measured on the 8060S with a layout ADR 0164 rejected (shared instructions, about 450 prompt tokens per memory), a memory took:

  | Slots | Time per memory |
  | ---: | ---: |
  | 1 | 1.18 s |
  | 2 | 0.88 s |
  | 4 | 0.76 s |
  | 8 | 0.83 s |

  With the kept layout each sequence reads its whole prompt, so batching shares only the decoding. Measured on the 8060S with the final prompt ([ADR 0163](0163-tagging-asks-for-its-classification-as-one-line-of-json.md)), in six alternating rounds of 8 waiting memories:
  - One at a time took 2.0–2.3 s per memory, and 2.95 s in a first round run under other load.
  - A batch of 4 took 1.58–1.72 s.
  - The batch was faster in every round: 17–24% less time per memory, 46% in the first round.
  - On all 48 memories, unpaired, batches of 2, 4 and 8 took 1.69, 1.56 and 1.60 s per memory.
  - The desktop's backlog of 174,959 memories then takes about 3.3 days instead of about 4.2.
- **On CPU, measured.** On llama.cpp's CPU build under WSL (4 threads), 20 waiting memories with the kept layout took 19.5 s each in batches of 4, against 19.9 s one at a time. Prefill is nearly all of a memory's time on a CPU, so batching gains little there.
- **Replies differ from sequential ones only as runs do.** They are not byte-identical: sampling at temperature 0.1 already varies between runs. On those 20 memories:
  - On the GPU's 48 memories, batched replies (2, 4 and 8 slots) agreed with a sequential run on 96–98% of types and every language, with a domain overlap (Jaccard) of 0.73–0.76 and a topic overlap of 0.54–0.60. Two sequential runs agreed with each other on 100% of types, with overlaps of 0.74 and 0.54.
  - On CPU's 20: 95% of types and every language, with overlaps of 0.64 and 0.44.
  - The batched type was acceptable for 17–19 of the 20 hand-labelled memories, the sequential one for 19 ([ADR 0163](0163-tagging-asks-for-its-classification-as-one-line-of-json.md)).
- **Bounded loss.** A stop between groups leaves at most one group unwritten. A preempted job loses at most the group being classified, which is classified again.
- **Memory.** The batch context stays allocated while the model is loaded.

## Invalidation conditions

- If llama-cpp-python's `Llama` gains multi-sequence generation, use it instead of the second context.
- If a device's GPU fails to hold the batch at the setting it reports, lower the per-sequence context, or size the batch from free GPU memory.
- If measurements on other GPUs or models put the gain below about 15%, withdraw the local batch path rather than keep its code.
- If CPU-only or GPU devices are measured to always gain, reconsider the default of 1.
- If tagging replies grow long or uneven (another prompt, another model), refill sequences as they finish.

## Validation

- `tests/test_tagging_throughput.py`, run on Windows 11 with the Python 3.13 runtime:
  - `BatchTests`: inputs are decoded `slots` at a time and in order, a lone last input is completed alone, one slot completes each input alone, a prompt too long for a sequence is completed alone and keeps its place, an error is returned in its request's place, and a batch the GPU cannot hold falls back and is not tried again;
  - `PromptTests`: a batch classifies each memory, or returns the reason it could not, with redacted prompts and the registry updated.
- `RealModelBatchTests`, the decoder against llama.cpp itself: Qwen3.5-4B Q4_K_M on llama.cpp's Linux CPU build under WSL. Each of five prompts, asking the model to name a fruit, got a reply naming it both in a batch of 4 slots and alone, and the batch context was used. It is skipped without `IMPRINT_TEST_TAGGER_GGUF`.
- `tests/test_background_tagging.py`: the local model classifies the configured number as one batch, in order; the last memory left is classified alone; a failed classification keeps the memory's keyword tags.
- The measurements above, run in the session scratchpad on the owner's desktop without storing anything: `tag_eval.py`, `gpu_final.py` and `gpu_ab.py` on the 8060S, and `batch_final_cpu.py` on CPU. The GPU runs of the final prompt came after the owner turned off Smart App Control, which had blocked llama.cpp's DLLs on Windows for part of the evaluation.
- Not verified:
  - Linux and macOS GPUs, and NVIDIA through Vulkan with batches.
  - A GPU too small for the batch (the fallback was exercised only with a stand-in).
