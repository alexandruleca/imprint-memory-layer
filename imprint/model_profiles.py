"""Versioned defaults and encoding contracts. No inference dependencies."""

# Qwen's own GGUF of Qwen3-Embedding-0.6B. Q8_0 matches the full-precision
# model (mean cosine 0.9994 on real chunks) where the int8 ONNX export it
# replaced did not (0.84; ADR 0086).
EMBEDDING_REPO = "Qwen/Qwen3-Embedding-0.6B-GGUF"
EMBEDDING_REVISION = "370f27d7550e0def9b39c1f16d3fbaa13aa67728"
EMBEDDING_FILE = "Qwen3-Embedding-0.6B-Q8_0.gguf"
EMBEDDING_SHA256 = "06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439"
QUERY_INSTRUCTION = "Retrieve relevant source code, documentation, and engineering decisions for the query"
HELPER_REPO = "unsloth/Qwen3.5-4B-GGUF"
HELPER_FILE = "Qwen3.5-4B-Q4_K_M.gguf"
HELPER_REVISION = "e87f176479d0855a907a41277aca2f8ee7a09523"
HELPER_SHA256 = "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4"
# OpenAI Privacy Filter (Apache-2.0), opt-in secret redaction (ADR 0060).
# The q4 ONNX export (float32 activations, 4-bit weights): as fast as q4f16
# on the CPU, and its labels on CUDA match the CPU's. The q4f16 export's
# mixture-of-experts layers run on CUDA but returned wrong labels on an RTX
# 5070 Ti (ADR 0122).
PRIVACY_FILTER_REPO = "openai/privacy-filter"
PRIVACY_FILTER_REVISION = "7ffa9a043d54d1be65afb281eddf0ffbe629385b"
PRIVACY_FILTER_FILE = "onnx/model_q4.onnx"
PRIVACY_FILTER_SHA256 = {
    PRIVACY_FILTER_FILE: "8f7dee8b46d096f052b359375dfba5d983cc4d18c44a783bf548615c472f8dea",
    PRIVACY_FILTER_FILE + "_data": "f30998e28c71c5374cc7e8b7de8f0f83e981592c0c2d652d2ad4928454dbb496",
    "tokenizer.json": "0614fe83cadab421296e664e1f48f4261fa8fef6e03e63bb75c20f38e37d07d3",
}
# The filter a synced record says its sender applied (ADR 0122): a receiver
# with the same identity does not run it again. Whatever changes the spans
# the filter produces (the model file, its revision, the kept categories,
# the decoder) must change this string; the execution provider does not.
PRIVACY_FILTER_IDENTITY = (f"{PRIVACY_FILTER_REPO}@{PRIVACY_FILTER_REVISION}/{PRIVACY_FILTER_FILE}"
                           ";secret,account_number;viterbi-0")


def family(repo: str) -> str:
    name = repo.lower()
    if "qwen3-embedding" in name:
        return "qwen3"
    if "embeddinggemma" in name and "embeddinggemma-2" not in name:
        return "embeddinggemma"
    if "bge-m3" in name:
        return "bge-m3"
    return "custom"
