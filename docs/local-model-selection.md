---
title: Local model selection
description: Embedding and local LLM recommendations, licensing, hardware profiles and required integration work.
---

## Recommendation and evidence

Reviewed 2026-10-23 against commit `ca60bbe`. For Imprint's mixed code, documentation, decisions and conversation retrieval, my first evaluation choice is **Qwen3-Embedding-0.6B**, initially at 1024 dimensions. Pair it with **Qwen3.5-4B**, quantized to a tested Q4 GGUF, as a shared optional helper for tagging, summaries and dashboard tool use. On a 12 GB GPU, compare **Qwen3.5-9B** as the higher-quality option once memory and latency are measured. This research proposed those defaults without claiming application-benchmark wins; subsequent adoption is documented below. The Qwen upstream cards declare Apache-2.0. [Embedding card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), [4B LLM card](https://huggingface.co/Qwen/Qwen3.5-4B), [9B LLM card](https://huggingface.co/Qwen/Qwen3.5-9B).

The strongest smaller embedding challenger is **Voyage-4-nano**. For code-heavy CPU installations, include **CodeRankEmbed**. **BGE-M3** is the conservative baseline closest to an explicitly supported alternative in the current wrapper. **EmbeddingGemma 2** is a compelling small-model candidate, but its export contract and conflicting licensing language need resolution before selection.

This review reads upstream cards, conversion artifacts and repository source. No candidate quality, throughput, RAM/VRAM or native-platform benchmark was run. Only small ONNX graph metadata was inspected; model weights were not loaded. Published benchmark suites, versions and evaluation harnesses differ, so their scores are not a direct Imprint ranking. Full open training reproducibility is a separate property from permissive model-weight licensing.

## Implementation follow-up

After this research, the user approved adopting Qwen defaults and rebuilding the index. [Accepted ADR 0010](adr/0010-qwen-defaults-and-explicit-reindex.md) records the implemented encoding, identity, explicit reindex and helper runtime changes. The findings below describe commit `ca60bbe`; they are retained as the research baseline. Current behavior and executed smoke tests are in [embeddings](embeddings.md), [local helper setup](local-models.md) and [platform validation](platform-validation.md). Adoption does not establish comparative quality scores.

## Workloads that matter

Benchmark follow-up, 2026-10-23: the pinned Qwen default now has a [public local
retrieval evaluation](benchmarks.md) on 297 fixed passages from 51 files.
Hybrid source Recall@5/10 is 0.833/0.937; semantic is 0.802/0.921. CPU warm
hybrid p50/p95 is 151/173 ms in the compact-response follow-up on an i9-13900HX WSL2 host with two ONNX threads;
model load plus first query is recorded separately. The hybrid-vs-semantic
Recall@5 interval includes zero. These results do not compare embedding
candidates, establish answer correctness or validate the optional helper LLM.

| Workload | What good behavior means | Model policy |
| --- | --- | --- |
| MCP/IDE search | Find a symbol or a paraphrased decision across selected code and prose; support multilingual queries and exact identifiers | A small, persistent embedding model plus lexical retrieval; no generative model required for ordinary search |
| Bulk indexing | Predictable throughput and bounded memory on laptop CPUs as well as GPUs | Modest token batches; avoid automatically loading a large LLM per chunk |
| Metadata tagging | Short, valid schema; stable vocabulary; respect opt-out | Deterministic tags first, optional small LLM for enrichment |
| Session summarization/facts | Extract supported decisions and reasons without inventing durable memories | Stronger instruction following, source evidence, JSON schema validation, explicit negation/contradiction cases |
| Dashboard chat/tools | Correct tool and arguments, grounded answers, bounded iteration | Small general instruct model with tested templates and tool parsing |
| Large autonomous code generation | Broader planning/coding capability | A separate optional client/model choice; Imprint's helper does not replace the model used by Claude Code or Codex |

Keeping these roles separate matters more than choosing one large model for everything. Search and explicit MCP memory writes should work with the helper LLM disabled. Embedding-based topic prototypes are another task: validate their prompt mode and thresholds rather than assume a search model's similarity scores are calibrated classification confidence.

## Embedding shortlist

The licensing column reports upstream declarations as checked on the review date. Every conversion needs its own provenance and redistribution review.

| Model | Size; native vector; advertised context | License declaration | Assessment for Imprint |
| --- | --- | --- | --- |
| [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) | 0.6B; 1024d with Matryoshka dimensions; 32K | Apache-2.0 | First mixed-workload evaluation choice: code plus multilingual prose, instruction-aware retrieval and multiple serving formats. More compute/weight memory than the smaller challengers. |
| [Voyage-4-nano](https://huggingface.co/voyageai/voyage-4-nano) | 340M total; 2048d native, supports 1024/512/256; about 32K | Apache-2.0 | First efficiency challenger. A 180M non-embedding backbone plus 160M embedding parameters suggests an attractive CPU profile; actual latency must be measured. Evaluate 1024d and 512d. |
| [CodeRankEmbed](https://huggingface.co/nomic-ai/CodeRankEmbed) | 137M; 768d; 8192 | MIT | Code-specialized candidate for small CPU machines or an optional code index. Its code benchmark strength does not establish conversation/ADR retrieval quality. Review custom implementation/export support. |
| [BGE-M3](https://huggingface.co/BAAI/bge-m3) | About 568M; 1024d; 8192 | MIT | Conservative multilingual baseline; CLS pooling and no query instruction. Imprint already recognizes its family. Its learned sparse/multi-vector capabilities are not enabled by the current dense ONNX wrapper. |
| [EmbeddingGemma 2](https://huggingface.co/google/embeddinggemma-2) | 270M text component; 740M with all encoders; 768d; 8192 | Card declares Apache-2.0; also contains mandatory prohibited-use-policy language | Strong compact code/text challenger, with optional multimodal expansion. Resolve the contradictory card language and adapt graph inputs/output selection before adopting under a strict permissive-OSS policy. |
| [Qwen3-Embedding-4B](https://huggingface.co/Qwen/Qwen3-Embedding-4B) | 4B; 2560d with Matryoshka; 32K | Apache-2.0 | Quality-tier experiment for a dedicated GPU. Large embedding weights compete with helper/chat memory; do not make this the laptop default. |
| [Nomic Embed Text v2 MoE](https://huggingface.co/nomic-ai/nomic-embed-text-v2-moe) | 475M total/305M active; 768d down to 256d; 512 | Apache-2.0 | Useful open-training-data alternative, but its 512-token cap is awkward for current code/prose chunks. Active parameters describe compute, not all resident weights. |

The current **EmbeddingGemma-300M** remains the regression baseline, but its [upstream license is Gemma](https://huggingface.co/google/embeddinggemma-300m), rather than Apache/MIT. That is distinct from the newer Gemma 4 family. Do not label every Gemma model identically. Likewise, [Jina Code Embeddings 0.5B](https://huggingface.co/jinaai/jina-code-embeddings-0.5b) declares CC-BY-NC-4.0, which does not meet the requested unrestricted permissive-model criterion. Small MiniLM models are useful speed baselines, but short-context English sentence embeddings are not my first choice for multilingual code/prose memory.

Voyage's API documentation and model card disagree about some default dimensions. For a local adapter, use the pinned checkpoint's config, documented local encoding contract and shape tests. The inspected upstream card describes 2048d native with truncation to 1024, 512 or 256. Do not infer settings from a hosted API model alias.

EmbeddingGemma 2's public card reports code-suite improvements over its predecessor, but that comparison is not proof it beats Qwen or Voyage on Imprint. Its [ONNX conversion](https://huggingface.co/onnx-community/embeddinggemma-2-ONNX) documents roughly 314 MB for q8 text weights, excluding runtime/activations; that is an artifact size, not peak RAM. This makes it worth evaluating after the licensing and adapter issues are resolved.

## Integration defects to resolve before comparing quality

These are findings from the reviewed baseline, before the implementation follow-up.

1. **Query/document preprocessing is missing.** `embed_query` and `embed_document` currently call the same raw encoder. Qwen expects a query instruction and unprefixed documents; Voyage uses different query/document prompts; the existing EmbeddingGemma baseline also documents asymmetric prefixes. Fixing the baseline is necessary for a fair comparison. [Qwen instructions](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), [Voyage local encoding](https://huggingface.co/voyageai/voyage-4-nano), [EmbeddingGemma prompts](https://huggingface.co/google/embeddinggemma-300m).
2. **Pooling is model-specific.** Auto pooling chooses CLS for BGE-M3 and mean otherwise. Qwen embedding requires last-token pooling. The current `last` implementation uses `attention_mask.sum()-1`, which assumes right padding; use mask-position selection that handles either side and test padding invariance. Voyage's local card specifies mask-aware mean pooling.
3. **Graph outputs and required inputs need a contract.** `_run_session` reads `outputs[0]` and only fills token IDs, attention mask and optional token types. That is not enough for arbitrary ONNX models. Select the named sentence output or a documented hidden-state output plus projection/pooling; validate every required input, dtype and output dimension.
4. **Embedding identity is absent from collection validation.** Existing collections are reused without checking model/revision, pooling, prompts or quantization identity. Different embedding models with the same dimension still produce incompatible coordinate spaces. A dimension setting alone cannot make an old collection valid.
5. **Refresh/dedup can prevent a model migration.** Unchanged file hashes skip extraction, while content-derived IDs can skip re-embedding. Switching `model.name` and running refresh therefore does not reliably rebuild vectors. The current workspace `migrate` command preserves vectors; it is not an embedding-model migration.
6. **Caches and peers need the same identity.** Model files are keyed only by repository name and prototypes are reused based on matrix shape. A same-dimension model change can reuse incorrect label vectors. Include model revision, artifact hash, pooling, prompt version, output dimension and normalization in cache keys; validate vector identity before importing/migrating/syncing precomputed vectors.
7. **Sequence limits are token budgets, not raw character limits.** Current pretrim uses a character heuristic. Record truncation rates, enforce model-token limits including prefixes, and avoid interpreting 32K support as a reason to embed entire files. Start around 512–1024 code tokens per chunk and test larger prose chunks separately.

For Qwen, start with a stable task description such as `Retrieve relevant source code, documentation, and engineering decisions for the query`. Keep the instruction fixed within a profile. Treat changes to that instruction as an embedding-contract change requiring evaluation and potentially reindexing, not an invisible prompt tweak.

### Export metadata inspected without loading weights

On 2026-10-23, small protobuf graph files were inspected directly from the following conversion revisions:

| Conversion/revision | Observed graph interface | Implication |
| --- | --- | --- |
| `onnx-community/embeddinggemma-2-ONNX` at `daa72c51243991dfcaf9f9137d2c573d8f7790c0` | q8 graph inputs: `input_ids`, `attention_mask`, `image_features`, `video_features`, `audio_features`; outputs: `last_hidden_state`, `sentence_embedding` | Current wrapper omits required inputs and assumes the first output. A text adapter must supply validated empty-modality tensors or use a separately validated text-only export, and choose the intended output. |
| `onnx-community/voyage-4-nano-ONNX` at `fb6f32cceb63b6eac92e1fdc08e00d03c41cdf5a` | q8 graph inputs: `input_ids`, `attention_mask`; outputs: `last_hidden_state`, `pooler_output` | Promising simple input contract; still validate pooling, any projection and reference-output parity. |
| `onnx-community/Qwen3-Embedding-0.6B-ONNX` at `c25a394dd583836952667c12f008335071b3f43d` | Bounded 2 MB graph read was incomplete; input/output metadata was not established | Do not report compatibility as verified. The [conversion's documented use](https://huggingface.co/onnx-community/Qwen3-Embedding-0.6B-ONNX) explicitly uses last-token pooling and query instructions. |

This inspection establishes signatures only, not successful inference or correct retrieval. No native CPU/GPU compatibility test was run for these exports.

## LLM shortlist and task routing

| Model | License | Role and tradeoff |
| --- | --- | --- |
| [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) | Apache-2.0 | First shared-helper evaluation choice: structured extraction, grounded chat and tool use at moderate weight memory. Use non-thinking mode for routine extraction. |
| [Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B) | Apache-2.0 | Higher-quality candidate for the 12 GB GPU; compare summary fidelity and tool success against 4B before paying the latency/memory cost. |
| [Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B) | Apache-2.0 | Small-machine tagging/extraction candidate; less margin for long, ambiguous transcripts. Do not trust durable fact generation solely because JSON parses. |
| [Granite 4.1 3B](https://huggingface.co/ibm-granite/granite-4.1-3b) | Apache-2.0 | Serious compact challenger: extraction, summarization and native tool-calling focus. Its declared supported-language set does not include Romanian, so test that workload explicitly. |
| [Ministral 3 3B Instruct](https://huggingface.co/mistralai/Ministral-3-3B-Instruct-2512) | Apache-2.0 | Compact instruction/function/JSON candidate. Validate tokenizer/template, runtime support and the complete language/vision weight footprint. |
| [Gemma 4 E4B](https://huggingface.co/google/gemma-4-E4B-it) | Apache-2.0 | Keep the configured chat model as a baseline. The E4B label describes effective compute: the card lists about 8B parameters including embedding tables, so it is not a 4B memory allocation. |
| [Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B) | Apache-2.0 | Current larger-model option for a dedicated workstation, not the default for this laptop. A Q4 27B model needs about 13.5 GB for ideal packed weights alone, before quantization overhead/runtime/context. |

The repo currently configures Qwen3-1.7B for in-process tagging, Gemma 4 E4B for in-process chat, and `qwen3:1.7b` through Ollama for summaries. They are separate code paths and may allocate separate models. Pointing tagger and chat at the same GGUF does not share their Python model objects or memory.

Prefer one local helper server per selected LLM, shared by the processes needing it. A tested llama.cpp server/GGUF or an optional Ollama service is more suitable for desktop profiles than requiring a large GPU-serving stack. Keep the existing in-process route available for a genuinely standalone installation, with explicit load/unload and concurrency policies. All endpoint adapters must preserve loopback access, cancellation and no-LLM operation. The word `openai` in an API-compatible adapter describes the wire protocol; it does not require a cloud model.

There are three concrete compatibility tasks:

- The tagger inserts `/no_think` into text and has 60/200-token output paths. Qwen3.5's [official instructions](https://huggingface.co/Qwen/Qwen3.5-4B#instruct-or-non-thinking-mode) require a serving/template option such as `enable_thinking=false`; the old text switch is not a reliable control. Configure this explicitly and allocate a complete schema output budget.
- The dashboard asks models to emit custom `<tool_call>` text. Native model tool-calling scores do not establish success with that parser. Test the current contract, or add a real structured tool-call adapter with argument schemas, allowlisted tools and bounded retries.
- Summaries/tagging currently request JSON in prompts. Add constrained structured generation where supported and validate the schema. Correct JSON is not evidence that a fact is true: require transcript spans/message IDs, preserve negation, distinguish proposals from accepted decisions, and test contradictions and prompt injection.

Start extraction in non-thinking mode with short, bounded outputs and low-variance decoding, then measure actual accuracy. Do not override a model's recommended sampling policy blindly or make reasoning tokens compete with a tiny JSON-output allowance. Long-session summarization should use bounded chunks and an evidence-preserving merge, rather than allocate the full advertised context.

## Hardware profiles

Planning estimates below are not measured memory promises. A model file is only part of the working set: add token/KV or recurrent state, prompt prefill activations, temporary buffers, server overhead, Qdrant, the UI and other applications. Q4 and q8 refer to weight quantization; Qdrant vector quantization is a separate choice.

| Profile | Embedding | Optional helper | Initial resource policy |
| --- | --- | --- | --- |
| CPU / constrained laptop | Compare Voyage nano q8, CodeRankEmbed and Qwen embedding q8 | None by default; evaluate Qwen3.5-2B or Granite 3B Q4 | One heavy job; batch 1–4 initially; around 4K helper context; user opts into model download |
| General desktop | Qwen3-Embedding-0.6B q8 on CPU or a tested GPU backend | Qwen3.5-4B Q4 | About 8K helper context, serialize heavy prefill/indexing, then tune from measurements |
| Observed 12 GB RTX 4080 Laptop GPU | Same small embedding, initially CPU to leave GPU headroom | Start 4B Q4; benchmark 9B Q4 as an upgrade | Reserve GPU headroom; roughly 4–6 GB total for the 4B helper is an initial planning envelope, not a guarantee; 9B can need roughly 8–10+ GB depending on context/runtime |
| Dedicated 24 GB+ workstation | Evaluate Qwen embedding 4B or small embedding plus reranker | Evaluate a larger helper separately | Full resident weights and context must fit concurrently; test rather than add nominal capacities |

Before the interruption, the environment exposed a 12 GB RTX 4080 Laptop GPU, a separate 6 GB GTX 1060, an i9-13900HX and about 15 GiB RAM to WSL. This is not a measurement of total physical laptop RAM. Swap was heavily used at that snapshot; that alone does not establish why the laptop crashed. Do not assume the two GPU capacities form one fast 18 GB pool. Keep the 4080 as the primary supported accelerator and CPU embedding as the initial predictable configuration.

The [TEI upstream requirements](https://github.com/huggingface/text-embeddings-inference#cuda) exclude CUDA compute capabilities below 7.5, including the GTX 1000 series. The 1060 therefore is not a supported TEI CUDA target. Other legacy runtimes may work but need separate builds/testing; do not base the default on that assumption. Current Imprint embedding configuration selects a GPU index, but the helper loaders do not expose an equivalent full per-model placement policy.

On macOS, the current ONNX selection code uses CPU/CUDA and does not select CoreML; do not assume automatic Metal acceleration for embeddings. A llama.cpp helper can use a tested Metal build. Native Windows and Linux require their own tested runtimes. A GGUF file alone does not prove a bundled binding supports its architecture.

For vectors alone, 1 million float32 records at 1024 dimensions take about 4.10 GB decimal; 768d takes 3.07 GB and 512d takes 2.05 GB, before indexes, payloads and quantized copies. Validate retrieval before using Matryoshka truncation, and renormalize after truncating. Quantized model inference and quantized output vectors are separate experiments.

## Reranking and evaluation before adoption

Keep lexical matching and provenance; embeddings do not solve exact symbol matching or staleness. After establishing candidate recall, evaluate [Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B) on only the top 20–40 candidates, with CPU mode optional and a latency budget. A reranker cannot recover a relevant source absent from the candidate set. Whether this beats a 4B embedder on quality per second is an experiment, not an established result.

Use the current baseline, a correctly prompted baseline, Qwen 0.6B, Voyage nano, BGE-M3 and CodeRankEmbed on the same frozen corpus. Add Gemma 2 only after its contract/licensing questions are resolved. Include at least 100–200 held-out queries, balanced across code symbols, behavioral paraphrases, ADR rationale, conversation decisions, English/Romanian cross-language queries, similar code in different projects, and deliberately unanswerable questions. Record graded relevance and source IDs; separate exact lexical wins from semantic improvements.

Measure recall@5/10, MRR/nDCG, citation correctness, unsupported answers, cold/warm p50/p95, chunks/sec, peak RSS/VRAM, disk/download footprint and truncation rate. Run one model at a time in isolated data directories. Compare reference and ONNX/GGUF embeddings, variable padding, long inputs, quantization and configured dimensions. Report confidence intervals, model revisions, corpus revision and hardware; published model-card scores are not replacements for these measurements.

For helper models, test at least 50 extraction/summarization cases and 30 multi-step tool cases before accepting a default. Include false proposals, reversals, contradictory decisions, empty inputs, malformed tool outputs and injection in retrieved content. Grade schema validity, supported-fact precision/recall, correct tool/arguments, grounded answers, latency and memory separately. Provisional release targets are 99% schema-valid extraction and 95% supported-fact precision, zero cross-workspace leakage and no storage success reported after failure. These are proposed thresholds, not achieved scores.

Adopt a model only after there is a versioned embedding contract, reference parity, a complete re-embedding path into a new collection, regenerated prototypes, safe sync compatibility, native platform smoke and rollback. Preserve original memories and the old collection while evaluating. Do not wipe data or merely change `model.dim`. See [proposed ADR 0009](adr/0009-model-profiles-and-embedding-identity.md).
