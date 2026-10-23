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


def family(repo: str) -> str:
    name = repo.lower()
    if "qwen3-embedding" in name:
        return "qwen3"
    if "embeddinggemma" in name and "embeddinggemma-2" not in name:
        return "embeddinggemma"
    if "bge-m3" in name:
        return "bge-m3"
    return "custom"
