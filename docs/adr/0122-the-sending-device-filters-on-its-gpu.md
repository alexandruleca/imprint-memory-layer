---
title: ADR 0122 — The Privacy Filter runs on a GPU that passes a probe, and that device filters what it syncs
description: Setup installs ONNX Runtime's build for a GPU it finds; the Privacy Filter uses that GPU only when it labels exactly like the CPU and clearly faster; a device whose filter runs on a GPU filters the memories it sends and marks them, and a receiver with the same filter skips the model for marked records but still applies the rules.
---

- Status: Amended
- Date: 2026-10-31
- Scope: CUDA was verified on Linux x64 with an RTX 5070 Ti. CUDA on Windows, MIGraphX on AMD with ROCm, DirectML and Core ML were tested only with stand-ins; see Validation.
- Amends:
  - [ADR 0060](0060-redact-secrets-before-memories-are-stored.md) — the pinned export (q4 instead of q4f16), the runtime (a GPU that passes the probe, else the CPU), and the receiver's Privacy Filter skipping records a paired sender marked as filtered by the same model
  - [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md) — ONNX Runtime may be a GPU build again, for the Privacy Filter only, in place of the CPU build
- Amended by: [ADR 0123](0123-a-paired-sender-prepares-the-next-chunk-while-the-last-is-stored.md) — the sender filters the next chunk while the other device stores the last
- Related: [ADR 0032](0032-one-onnx-runtime-per-environment.md) (one ONNX Runtime per environment, unchanged), [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md), [ADR 0113](0113-unchosen-install-profiles-try-the-gpu.md), [ADR 0116](0116-paired-devices-send-only-what-the-other-lacks-in-stored-chunks.md), [ADR 0120](0120-a-device-storing-a-chunk-says-so.md)

## Context

`redact.privacy_filter` (ADR 0060) runs OpenAI Privacy Filter, a mixture-of-experts token classifier, with ONNX Runtime's CPU build. Every write redacts its content, and every memory a device receives through sync is redacted again on arrival (`vectorstore._redacted_received`). On bedrock (Ubuntu x64, RTX 5070 Ti, driver 595.99) a received memory averaged 581 tokens and took about 0.85 s, so the first paired sync of a 58,563-memory workspace spent about 16 hours storing. ADR 0120 stopped such stores from failing the sync; this record makes them shorter. Earlier measurements on the same machine found that the q4 export instead of q4f16, `ORT_ENABLE_ALL` and batching each changed CPU time by less than 10%.

The owner asked for the filter to use any GPU it can, and for the device that filters fastest to do the work during sync.

Facts that shaped the decision, measured on bedrock with ONNX Runtime 1.31.0 (`onnxruntime-gpu[cuda,cudnn]`):

- **The q4f16 export is wrong on CUDA here.** Its eight `QMoE` nodes (the expert layers) run on CUDA, with `moe_gemm_profiler: No valid GEMM config found for (N;K)=(1280;640)` warnings, in about 0.014 s for three probe texts, and label background where the CPU labels a secret with a margin of 5 to 8 logits: 10 of 64, 139 of 580 and 388 of 1,100 tokens differ. ONNX Runtime 1.30.0 and 1.31.1, every graph optimization level and the CUDA provider's `ORT_DISABLE_MOE_GEMV`, `ORT_FPA_INTB_GEMM` and `ORT_CUDA_GEMM_AUTO_TUNE` switches gave the same. An earlier report of "identical labels at 0.01 s per memory" compared texts without secrets, where both label everything background.
- **The q4 export is right on CUDA.** Its expert layers have float32 activations, for which ONNX Runtime has no CUDA `QMoE` kernel, so they run on the CPU and the other 288 of 307 nodes on CUDA. With TF32 off (`use_tf32=0`), CPU and CUDA give identical labels on the probe texts and identical decoded paths on 43 texts of 26,502 tokens; with TF32 on, one near-tie token (margin 0.03) differed. A 581-token text takes 0.18 s on CUDA against 0.36 s on the CPU.
- **q4 against q4f16 on the CPU.** Here q4 took 17.7 s and q4f16 18.7 s for the 43 texts, with decoded paths equal on 42 and 2 tokens different in all. ADR 0060's laptop measured q4 at 5.0 s against 4.1 s for one 1,024-token pass. q4 is 917 MB against 809 MB.
- **Other providers cannot run this model's expert layers.** The model's operators are 20 standard ones plus `MatMulNBits` (41), `RotaryEmbedding` (16), `SkipSimplifiedLayerNormalization` (16), `QMoE` (8) and `GatherBlockQuantized` (1). The DirectML build (`onnxruntime-directml` 1.24.4) registers DirectML kernels for `MatMulNBits`, `RotaryEmbedding`, `MatMul`, `Gather`, layer normalization and the shape operators, but its only `QMoE` and `GatherBlockQuantized` kernels are the CPU ones (read from the `Dml::DmlOperator*` and `QMoECPU` symbols of its `onnxruntime.dll`). Core ML runs standard ONNX operators only, so every `com.microsoft` operator stays on the CPU. AMD's MIGraphX provider leaves operator support to the installed MIGraphX library, so it could not be checked offline. A partitioned graph copies tensors between devices at every boundary, which can be slower than the CPU alone.
- **Packaging.** `onnxruntime`, `onnxruntime-gpu`, `onnxruntime-directml` and `onnxruntime-migraphx` install the same `onnxruntime` package directory (ADR 0032), and faster-whisper requires `onnxruntime` by name. AMD publishes `onnxruntime-migraphx` only at `repo.radeon.com`, one wheel per ROCm release: 1.23.2 for ROCm 7.2 to 7.2.4, each a different file. ROCm 6.4.4 and 7.0.2 have only `onnxruntime_rocm` 1.21/1.22, whose ROCm provider upstream has removed. `onnxruntime-directml` stops at 1.24.4. Some `nvidia-cublas` releases (13.8.1, 13.8.2) have no Windows wheel.
- **Sync.** A full sync (`stream_export_lines`) and a paired sync (`sync_delta.export_chunks`) send stored memories with their IDs and vectors. A receiver validates each ID against the content as sent, then redacts; a record redaction changes takes the ID of its new content and is embedded again. `_stream_line_ok` checks frames, not record fields, and `process_import_buffer` copies only the fields it knows, so an older receiver ignores an unknown field.

## Decision

### Setup installs the build of a GPU it finds

On the `gpu` and `auto` profiles, setup asks `python -m imprint.onnx_runtime plan` for the builds to try, best first, always ending with the CPU build:

| Machine | Build | Lock |
| --- | --- | --- |
| NVIDIA GPU listed by `nvidia-smi -L`, Linux x64 or Windows x64 | `onnxruntime-gpu[cuda,cudnn]` 1.31.0 with NVIDIA's CUDA 13 and cuDNN 9 wheels | `requirements/onnx-cuda.lock.txt` |
| Linux x64 with ROCm 7.2.0–7.2.4 (`/opt/rocm/.info/version`, or `$ROCM_PATH`) whose `rocminfo` lists a GPU agent | AMD's `onnxruntime-migraphx` 1.23.2 cp312 wheel for that release, by URL and SHA-256 | `requirements/onnx-rocm.json` with `onnx-rocm.lock.txt` (`coloredlogs`) |
| Windows x64 with a display adapter other than the basic, remote or virtual ones | `onnxruntime-directml` 1.24.4 | `requirements/onnx-directml.lock.txt` |
| Anything else, Apple silicon included (its standard wheel has Core ML), and the `cpu` profile | `onnxruntime` 1.31.0 | `requirements/cpu.lock.txt` |

A GPU build installs with `base` and `cpu` and uv's `--excludes` naming `onnxruntime`, hash-locked like every other install, so the venv holds one build. Setup then runs `python -m imprint.onnx_runtime check <build>`: the build must be installed alone and complete and run a two-node model on its provider, with `get_providers()[0]` naming it (a CUDA build without a usable driver silently puts the session on the CPU). A build that fails to install or fails the check is replaced by the next one, and `python -m imprint.onnx_runtime unused <build>` names what only the replaced build brought in, such as NVIDIA's libraries, for setup to remove. Without uv, setup installs the CPU build. Setup installs no driver or ROCm: an AMD GPU on Linux without a supported ROCm release stays on the CPU build, and setup says which ROCm releases would do. `imprint profile set cpu` keeps the CPU build.

### The Privacy Filter keeps a GPU only after a probe

`privacy_filter._load` tries the GPU providers the installed build offers, in the order CUDA, MIGraphX, DirectML, Core ML. For a provider without a recorded verdict it labels three fixed texts of 64, 580 and 1,100 tokens on that provider and on the CPU. It keeps the provider only when the argmax labels of every token are identical and two timed passes over the texts, after one warm-up, are at least 1.5 times faster than the CPU's. The distinct lengths charge providers that compile per input shape for it. The verdict, with both timings, is stored in `data/models/privacy-filter/<revision>/providers.json` under the model identity, the provider and the ONNX Runtime version, so later starts load the chosen session directly. A provider that does not load is not recorded and is tried again next start. CUDA sessions call `onnxruntime.preload_dlls()` first and set `use_tf32=0`; Core ML uses its `MLProgram` format. A GPU session that fails at run time is replaced by a CPU session for the rest of the process, and the text is labelled again on it; a write never fails because of the GPU. The pinned digests are checked on every load as before.

The pinned export becomes `onnx/model_q4.onnx` (917 MB plus the 28 MB tokenizer), because q4f16 gave wrong labels on CUDA and q4 matches the CPU there.

OCR passes `use_cuda`, `use_dml`, `use_coreml` and `use_cann` false to RapidOCR, whose defaults already pick the CPU; faster-whisper's VAD asks for the CPU provider itself. Both run on the CPU provider of whichever build is installed. The audio profile hashes the version of whichever distribution provides `onnxruntime`.

### The sending device filters, the receiver trusts the mark

When a device exports memories for sync (full sync batches and paired-sync chunks), its redaction filters them only if its `redact.privacy_filter` is on and the filter runs on a GPU here (`privacy_filter.accelerated()`, which loads the model only when a GPU provider is installed). It runs `redact_all`, rules and model, over each record's text after any source prefix (ADR 0081). A record that comes out unchanged gains `privacy_filter: <identity>`, where the identity is `model_profiles.PRIVACY_FILTER_IDENTITY`: the repository, revision and file of the model, the kept categories and the decoder version, and not the provider. A record the sender's redaction would change, because it was stored while the filter was off or under fewer rules, is sent as stored and unmarked: its ID and vector are those of its stored content, and the receiver redacts it, takes the ID of the redacted content and embeds it again, as before. Any failure sends the batch unmarked. A device whose filter runs on the CPU marks nothing and leaves the work to the receiver. A full sync now sends each batch as it is made, so a sender filtering a large workspace does not leave the receiver waiting for its first frame.

A receiver with `redact.privacy_filter` on skips the model for a record whose mark equals its own identity. It still applies its rules (`redaction.rule_spans` and `apply`), keeps the most redacted version it holds or receives, relabels and re-embeds a record its rules change, and validates the sender's ID first. A record without the mark, or marked with another identity, is filtered on arrival. The mark is a transfer field: it is never stored.

The device records no mark at write time. A stored mark would have to stay true through every path that rewrites stored content (`replace_source`'s payload updates, project moves, adoption of a stricter version, model changes), and a wrong one would make receivers skip the model; on a GPU, filtering again on export costs about 0.15 s a memory.

This changes ADR 0060's rule that a receiver redacts every import with its own settings "whatever the sending device did": the receiver's model now trusts a paired sender's statement that the same model already went over a record. The rules and the version check are unchanged.

## Rejected alternatives

- **Keeping q4f16 and the CUDA provider.** Fast, and wrong on the RTX 5070 Ti: it labelled secrets as background. The probe rejects it, so the GPU would never be used on the machine that motivated this record.
- **The q4 export on GPU devices and q4f16 on CPU devices.** Their labels differ on a few tokens, so they are different filters: a GPU sender's mark would never match a CPU receiver, the case that matters most.
- **Comparing decoded spans instead of per-token labels in the probe.** Decoded spans are what Imprint uses, but identical labels are a stricter test and q4 on CUDA with TF32 off passes it.
- **Trusting a provider without timing it.** DirectML and Core ML cannot run the expert layers, and a partitioned graph can be slower than the CPU alone.
- **Detecting GPUs in Go.** The plan, the check and the list of what to remove need Python package metadata and ONNX Runtime anyway; `imprint.onnx_runtime` already answered setup's other runtime questions.
- **Installing the GPU build with `--no-deps`.** A later `uv pip install` of the CPU locks would bring the CPU build back for faster-whisper; `--excludes` keeps every install consistent.
- **Installing ROCm, or `onnxruntime_rocm` for ROCm 6.4 and 7.0.** ROCm needs root and a kernel driver matched to it. The `onnxruntime_rocm` builds are ONNX Runtime 1.21 and 1.22 with a provider upstream has removed.
- **Marking records whatever device filtered them, CPU included.** The owner chose that the fast side does the work; a CPU sender re-filtering on export would spend the time a GPU receiver saves.
- **Re-embedding a changed record on the sender.** It would add embedding to export; leaving it to the receiver reuses the path every unmarked record already takes.

## Consequences

- **Measured on bedrock** (load average 4–7 from other work during the runs), with 200 memories of 2,300 characters (406–848 tokens, mean 546; docs and source of this repository, 10 with a GitHub token and 10 with a passphrase in prose):

  | Step | Seconds for 200 | Per memory |
  | --- | --- | --- |
  | Redaction (rules and model) alone, CPU, q4f16 | 57.3–57.5 | 0.29 |
  | The same, CPU, q4 | 51.4–52.0 | 0.26 |
  | The same, CUDA, q4 | 29.5–29.6 | 0.15 |
  | Sender on CUDA: filtering and marking (180 marked, 20 changed and sent unmarked) | 29.5 | 0.15 |
  | Receiver storing unmarked records, CPU, q4f16 (before this record) | 58.9–61.6 | 0.30 |
  | The same, CPU, q4 | 59.2–60.3 | 0.30 |
  | The same, receiver's filter on CUDA, q4 | 37.8–37.9 | 0.19 |
  | Receiver storing the sender's marked records, CPU | 13.2–15.4 | 0.07 |
  | The same, receiver on CUDA | 13.6–15.4 | 0.07 |

  Storing runs `process_import_buffer` into a scratch data directory and Qdrant, with the 20 changed memories embedded again by llama.cpp's Vulkan build; the model was loaded beforehand (3–3.5 s). A paired sync filters the next chunk only after the receiver acknowledges the last, so sender and receiver times add up: a CPU receiver with a CUDA sender spends about 0.22 s a memory between them against 0.30 s, and a CUDA receiver alone 0.19 s. For the 58,563-memory first sync that is roughly 3.6 h or 3.1 h against 4.9 h at these rates; the 0.85 s a memory observed on the live installation would scale in proportion. Preparing the next chunk while the receiver stores the last would hide the sender's share; it is not done here.
- **Security.** A mark arrives only from a paired device over the authenticated, end-to-end encrypted sync channel (ADR 0018), that is from a device of the same owner. A lying or broken peer can make the receiver skip the model for what it sends, but it already holds that content and could store or send it unredacted anyway; skipping the model on its records exposes nothing the peer does not have. The rules always run on received records. A peer on an older release never marks; the receiver filters everything it sends.
- **Compatibility.** Older receivers ignore the mark and filter as before; older senders send none. Records and IDs are unchanged on the wire. Every device on this release downloads the q4 export (945 MB) the next time the filter loads; the q4f16 files stay in `data/models/privacy-filter/7ffa9a043d54/` until deleted. Labels can differ from q4f16 on a few tokens, so a memory filtered under q4f16 and again under q4 may redact differently; the most redacted version still wins.
- **Downloads.** NVIDIA machines on the `gpu` and `auto` profiles download about 2.5 GB of CUDA and cuDNN wheels, even with the Privacy Filter off; AMD machines with ROCm about 20 MB from `repo.radeon.com`; Windows machines about 25 MB. ADR 0086 had removed the CUDA libraries; an update installs them again on NVIDIA machines, where setup's `auto` plan tries the CUDA build.
- **Speed is model-bound.** With q4 the expert layers stay on the CPU, so CUDA is about 1.75 times as fast as the CPU here, not the hundredfold the q4f16 measurement suggested. A GPU sender now carries the filter's cost for both devices, and a CPU-only receiver stores marked memories at the cost of the rules and the database.
- **Known gaps.** The probe's verdict is per provider and ONNX Runtime version, not per GPU or driver: a new driver keeps an old verdict until `providers.json` is deleted, though a failure at run time still falls back. Windows CUDA, DirectML, MIGraphX and Core ML are unverified on hardware. MIGraphX compiles per input shape; the probe charges for three shapes but real memories have many lengths.

## Invalidation conditions

- If ONNX Runtime ships a CUDA `QMoE` kernel that labels the q4f16 export (or a float16 export) exactly like the CPU on this GPU, then reconsider the pinned export: the expert layers would run on the GPU and the speedup would grow from about 2x.
- If a provider passes the probe but its labels differ from the CPU's on real memories, then the probe's texts or criterion are insufficient: compare decoded spans over a larger set, and re-run the probe per driver.
- If paired devices could belong to different owners, then the mark is no longer trustworthy and the receiver must filter every record again.
- If AMD publishes `onnxruntime-migraphx` for later ROCm releases, or for a Python other than 3.12, then `requirements/onnx-rocm.json` needs those entries; if it publishes on PyPI, move the wheel into a lock.
- If DirectML or Core ML gain kernels for `QMoE` and `GatherBlockQuantized`, then their probes may pass and should be measured.
- If ONNX Runtime drops `--excludes`-compatible packaging (for example faster-whisper stops requiring `onnxruntime`, or the builds stop sharing a package directory), then setup can install a GPU build without excluding the CPU one.

## Validation

- Unit tests with stand-ins for ONNX Runtime and its sessions (`tests/test_privacy_filter_providers.py`): provider order, the probe accepting a GPU and caching its verdict, rejecting differing labels and an insufficient speedup, a provider that does not load, a load exception moving to the next provider, run-time fallback to the CPU, `accelerated()` loading nothing without a GPU provider, OCR's RapidOCR parameters, RapidOCR's provider list on a GPU build, and faster-whisper's VAD session.
- `tests/test_onnx_runtime.py`: the build plan for NVIDIA, Windows DirectML adapters, ROCm with and without a GPU agent or a pinned release, an AMD GPU without ROCm, the `cpu` profile, macOS and arm64; the ROCm version file; the check; what each build keeps and removes.
- `tests/test_sync_filter_mark.py`: a receiver skips the model only for its own identity, still applies the rules (relabel and re-embed), still adopts a stricter version and stores no mark; a sender marks only with the filter on and a GPU, sends a changed record unmarked with its stored ID, and the receiver relabels and re-embeds it; source prefixes; paired-sync chunks; a full sync streams batches.
- Go: `TestInstallCoreRuntimeGPUBuilds` (fresh venv, CPU venv, a failing CUDA check falling back with NVIDIA libraries removed, ROCm's pinned wheel) and `TestPlanOrtBuildsWithoutUvIsTheCPUBuild`, with the existing setup tests.
- On bedrock: `python -m imprint.onnx_runtime check cuda` passes in a venv installed from the locks with `--excludes onnxruntime`; the probe accepted CUDA for q4 (3.11 s on the CPU against 1.39 s) and rejected it for q4f16 (labels differ); the measurements above.
- Not verified on hardware: CUDA on Windows x64, DirectML, AMD MIGraphX with ROCm (bedrock has no ROCm, and its Ryzen 5700G's integrated GPU is not supported by ROCm and was disabled), Core ML on Apple silicon, and machines without an NVIDIA GPU running setup's `auto` plan end to end.
