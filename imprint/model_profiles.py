"""Versioned defaults and encoding contracts. No inference dependencies."""

EMBEDDING_REPO = "onnx-community/Qwen3-Embedding-0.6B-ONNX"
EMBEDDING_REVISION = "c25a394dd583836952667c12f008335071b3f43d"
EMBEDDING_FILE = "onnx/model_quantized.onnx"
EMBEDDING_SHA256 = "87cd124e0ef1fd1f223ebc283efccbaeac386d0b08344701c46975d0657b591f"
QUERY_INSTRUCTION = "Retrieve relevant source code, documentation, and engineering decisions for the query"
HELPER_REPO = "unsloth/Qwen3.5-4B-GGUF"
HELPER_FILE = "Qwen3.5-4B-Q4_K_M.gguf"
HELPER_REVISION = "e87f176479d0855a907a41277aca2f8ee7a09523"
HELPER_SHA256 = "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4"
# OpenAI Privacy Filter (Apache-2.0), opt-in secret redaction (ADR 0060).
# The q4f16 ONNX export: the smallest download and the fastest on CPU.
PRIVACY_FILTER_REPO = "openai/privacy-filter"
PRIVACY_FILTER_REVISION = "7ffa9a043d54d1be65afb281eddf0ffbe629385b"
PRIVACY_FILTER_FILE = "onnx/model_q4f16.onnx"
PRIVACY_FILTER_SHA256 = {
    PRIVACY_FILTER_FILE: "eaae4e83cf1345a60abe333ed882b55fe5775d1dfbf34b9b269e5e5416f45e5b",
    PRIVACY_FILTER_FILE + "_data": "6d4dde787e03ace283c45d4e32a94eec32b6cfcc242e7219bea96f5b4c13569d",
    "tokenizer.json": "0614fe83cadab421296e664e1f48f4261fa8fef6e03e63bb75c20f38e37d07d3",
}


def family(repo: str) -> str:
    name = repo.lower()
    if "qwen3-embedding" in name:
        return "qwen3"
    if "embeddinggemma" in name and "embeddinggemma-2" not in name:
        return "embeddinggemma"
    if "bge-m3" in name:
        return "bge-m3"
    return "custom"
