# Recall lookups, 2026-10-25

`recall-report.json` is the complete output of

```bash
python benchmark/runtime.py recall --state STATE --out recall-report.json --runs 5 \
  --local-model Qwen3.5-4B-Q4_K_M.gguf --llm-timeouts 600,30
```

on the `feat/recall` change (ADR 0058), on the frozen public corpus index of benchmark run `20261023T140134777248Z-fdea98ae-s1729` (corpus revision `fdea98ae`; Qwen3-Embedding-0.6B q8 ONNX on CPU, 4 threads). The lookups and their gold sources are `benchmark/recall.json` (`datasetSha256` in the report).

Host: Intel i9-13900HX, WSL2, CPU only. llama.cpp 0.3.36 CPU wheel, four threads, Qwen3.5-4B Q4_K_M (sha256 `00fe7986…a4`). Other sessions shared the host; the multi-minute local-model outliers coincide with that load.

Tokens are counts of tool-call arguments and results under the index's Qwen3 tokenizer, a proxy for the caller's tokenizer. Server time excludes the caller model turn that each extra round trip costs. Deterministic modes repeat five times (median time); local-model modes run once per lookup. No raw responses are published; local-model rows keep the plan lines, notes and answers for review.
