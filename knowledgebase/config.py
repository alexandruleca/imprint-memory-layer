import os
from pathlib import Path


def get_data_dir() -> Path:
    """Resolve the data directory for storage. Priority:
    1. KNOWLEDGE_DATA_DIR env var
    2. data/ relative to this package's parent (the repo root)
    """
    env = os.environ.get("KNOWLEDGE_DATA_DIR")
    if env:
        return Path(env)
    # Default: <repo>/data/
    return Path(__file__).parent.parent / "data"


MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"
# ONNX file variant. `model_quantized.onnx` is int8 dynamic quantization —
# 2-4× faster on CPU vs fp32 with ~1% MTEB drop. Override with
# KNOWLEDGE_MODEL_FILE for fp32 (`onnx/model.onnx`) or fp16/q4 variants.
MODEL_FILE = os.environ.get("KNOWLEDGE_MODEL_FILE", "onnx/model_quantized.onnx")
EMBEDDING_DIM = 768
# Token cap per embedding call. nomic-embed supports up to 8192 but activation
# memory scales linearly — 1024 covers ~4k chars, which fits the chunker's
# typical output. Override with KNOWLEDGE_MAX_SEQ_LENGTH for higher fidelity
# at the cost of more RAM during ingest.
MAX_SEQ_LENGTH = int(os.environ.get("KNOWLEDGE_MAX_SEQ_LENGTH", "4096"))
MEMORIES_TABLE = "memories"
