---
title: ADR 0153 — Chat offers a larger Qwen MoE and custom models
description: A new chat.local_model setting keeps Qwen3.5-4B as the default and offers the pinned Qwen3.5-35B-A3B mixture-of-experts model on machines with enough memory, or any custom GGUF.
---

- Status: Accepted
- Date: 2026-11-08
- Scope: the local chat provider only; tagging, summaries and recall keep their model. Quality and speed of the MoE are unmeasured here (see Validation)
- Related: [ADR 0078](0078-keep-qwen3-5-4b-as-the-local-tagger.md), [ADR 0010](0010-qwen-defaults-and-explicit-reindex.md), [ADR 0087](0087-llama-cpp-runs-each-model-on-one-gpu.md), [local models](../local-models.md), [configuration](../configuration.md)

## Context

On 2026-11-08 the owner asked whether a Qwen mixture-of-experts model would beat the default Qwen3.5-4B in speed and quality, and then asked for it as an optional chat setting for larger machines: the default selected in a dropdown, the MoE as a second option, and Custom for any model.

- **The default.** Chat and the tagger share Qwen3.5-4B Q4_K_M (2.74 GB). ADR 0078 keeps it as the tagger and measured it at 3.2 s per memory on an RTX 4080 Laptop GPU.
- **The candidate.** Qwen3.5-35B-A3B (Apache-2.0) runs about 3B of its 35B parameters per token. `unsloth/Qwen3.5-35B-A3B-GGUF` at `bc014a1` offers Q4_K_M at 22,016,023,168 bytes. Its GGUF architecture is `qwen35moe`, which the pinned llama-cpp-python 0.3.36 wheel contains.
- **Speed.** Per token it can at best match a 4B dense model, while every weight stays resident. Public reports put it at about 10 tokens/s on M-series Max chips with llama.cpp (one log and calculator estimates, not a benchmark). On the owner's 12 GB laptop GPU it does not fit.
- **Quality.** A 35B-class model is expected to answer and reason better than the 4B, which matters most for chat and research, least for tagging throughput.
- **Before.** Another model meant setting `chat.model_repo` and `chat.model_file` by hand, unverified, and a loaded chat model was kept until the process restarted.

## Decision

**A new setting, `chat.local_model`, chooses the local chat model, with the 4B as the default.** The Settings page shows it as a dropdown under Chat:

- **`default` — Qwen3.5 4B.** Exactly the previous behaviour: `chat.model_repo`, `chat.model_file` and `chat.model_path`.
- **`qwen3.5-35b-a3b` — Qwen3.5 35B-A3B (MoE).** Downloaded at a pinned revision and verified by SHA256, like the 4B. It is shown as unavailable, with the reason, on a computer with less than 30 GiB of physical memory (32 GB machines report a little less).
- **Custom.** A GGUF path, `<owner>/<repo>/<file>.gguf`, or a Hugging Face file URL, stored as `<owner>/<repo>/<file>.gguf`. Repository files download from `main` into `data/models/<owner>--<repo>/` and are not verified.

Files Imprint pins are listed in one table (`model_profiles.PINNED`), so download and load verify either pinned model. Chat follows the setting: when it resolves to another file, the next chat loads that one and the previous model is released unless the tagger uses the same file.

## Rejected alternatives

- **The MoE as the default.** It needs about eight times the memory, does not fit many GPUs, and is not faster per token. The 4B stays the default.
- **The MoE for tagging too.** Tagging is throughput-bound and ADR 0078 chose its model on measured speed and accuracy. The tagger keeps `tagger.local.*`; a tagging evaluation of the MoE would be its own decision.
- **Only documenting `chat.model_repo`/`chat.model_file` values.** Two settings to change together, no checksum, and no reload: the owner asked for a choice in a dropdown.
- **Hiding the MoE on small machines.** Showing it as unavailable with the reason tells the owner why it cannot be chosen there; a stored value is still honoured.

## Consequences

- Chat on a large machine can run a much stronger local model with one choice; the 22 GB download starts at the next chat with the usual progress.
- When chat and the tagger use different models, both can be resident at once.
- A custom repository file is the user's responsibility: it is unpinned and unverified.
- The memory check reads total physical memory, not free memory or GPU memory; a machine with enough RAM but a small GPU runs most of the model on the CPU.

## Invalidation conditions

- If a measured comparison shows the MoE no better than the 4B for chat answers, remove the choice.
- If it proves fast enough on common hardware, consider it as the default for machines with the memory.
- If Qwen or Unsloth publish a fixed or better quantization, update the pin and its checksum.
- If 30 GiB proves too little or too much in practice, move `CHAT_MOE_MIN_MEMORY`.

## Validation

- **`tests/test_chat_local_model.py`** (15 tests):
  - accepted and refused values, including Hugging Face URLs normalised to their file;
  - the default and the MoE offered, the MoE unavailable below its memory with the reason;
  - `default` keeps the repo, file and path settings; the MoE resolves to its pinned file and revision; a custom repository file downloads into its own folder unpinned; a custom path is never downloaded;
  - switching releases the previous model, the same choice reuses it, and a model the tagger shares is kept.
- `tests/test_chat_stream.py` and `tests/test_wsl_bridge.py` pass unchanged.
- The pinned wheel contains `qwen35moe`, and the GGUF header at the pinned revision declares it.
- **Not verified:** downloading and running the MoE, its answer quality and speed against the 4B, and the Settings dropdown in a real dashboard.
