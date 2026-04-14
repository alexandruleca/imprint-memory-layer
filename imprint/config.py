import os
from pathlib import Path


def get_data_dir() -> Path:
    """Resolve the data directory for storage. Priority:
    1. IMPRINT_DATA_DIR env var
    2. data/ relative to this package's parent (the repo root)
    """
    env = os.environ.get("IMPRINT_DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).parent.parent / "data"


# ── Embedding model ─────────────────────────────────────────────
# BGE-M3: 1024-dim, 8192 ctx, Apache 2.0. Xenova ships int8/fp16/q4 ONNX
# variants — pick int8 for speed-quality trade-off (~1% MTEB drop, 2-4×
# faster on CPU). Override via env vars for fp32 or fp16.
MODEL_NAME = os.environ.get("IMPRINT_MODEL_NAME", "Xenova/bge-m3")

# Device control. `auto` (default) picks GPU if CUDA/ORT-GPU is available,
# CPU otherwise. `gpu` forces GPU. `cpu` forces CPU. ONNX quantization
# kernels are CPU-only — when the device resolves to GPU we auto-swap to
# the fp16 model file unless the user pinned one via IMPRINT_MODEL_FILE.
DEVICE = os.environ.get("IMPRINT_DEVICE", "auto").lower()


def _default_model_file() -> str:
    """Pick a sensible ONNX variant per device. int8 flies on CPU; GPU
    wants fp16 since int8 quant ops fall back to CPU kernels."""
    if DEVICE == "cpu":
        return "onnx/model_int8.onnx"
    if DEVICE == "gpu":
        return "onnx/model_fp16.onnx"
    # auto: probe provider list
    try:
        import onnxruntime as _ort
        if "CUDAExecutionProvider" in _ort.get_available_providers():
            return "onnx/model_fp16.onnx"
    except Exception:
        pass
    return "onnx/model_int8.onnx"


MODEL_FILE = os.environ.get("IMPRINT_MODEL_FILE", _default_model_file())
EMBEDDING_DIM = int(os.environ.get("IMPRINT_EMBEDDING_DIM", "1024"))
# Token cap per embedding call. BGE-M3 supports 8192 but activation memory
# + CPU compute scale linearly with seq len. 2048 covers ~8k-char inputs,
# which exceeds the chunker's HARD_MAX=6000 — no truncation in practice,
# and embedding is ~2× faster than at 4096. Raise via env if you want
# full-document embedding without chunking.
MAX_SEQ_LENGTH = int(os.environ.get("IMPRINT_MAX_SEQ_LENGTH", "2048"))

# ── Qdrant ──────────────────────────────────────────────────────
QDRANT_COLLECTION = os.environ.get("IMPRINT_COLLECTION", "memories")
QDRANT_VECTOR_NAME = "dense"

# ── Chunker ─────────────────────────────────────────────────────
# Sliding-window overlap appended at chunk boundaries so retrieval doesn't
# miss signal sitting right at a split.
CHUNK_OVERLAP = int(os.environ.get("IMPRINT_CHUNK_OVERLAP", "150"))

# Per-modality target sizes.
# Code: small enough that CodeChunker (tree-sitter) emits one top-level
# definition (function, method) per chunk for typical methods, while still
# bundling tight classes whole. Methods longer than this get sub-AST split.
# Prose: larger — SemanticChunker decides boundaries via topic-shift
# detection; the size acts as a soft cap, not the primary boundary signal.
CHUNK_SIZE_CODE = int(os.environ.get("IMPRINT_CHUNK_SIZE_CODE", "800"))
CHUNK_SIZE_PROSE = int(os.environ.get("IMPRINT_CHUNK_SIZE_PROSE", "1500"))
# Legacy alias — some callers may still import CHUNK_SIZE_CHARS.
CHUNK_SIZE_CHARS = int(os.environ.get("IMPRINT_CHUNK_SIZE", str(CHUNK_SIZE_PROSE)))

CHUNK_HARD_MAX = int(os.environ.get("IMPRINT_CHUNK_HARD_MAX", "6000"))

# SemanticChunker topic-shift threshold. Lower = more aggressive topic
# splits (each subtle shift starts a new chunk). 0.5 catches paragraph-level
# topic changes well; 0.7 is too lenient — adjacent paragraphs about
# different things get merged.
CHUNK_SEMANTIC_THRESHOLD = float(os.environ.get("IMPRINT_SEMANTIC_THRESHOLD", "0.5"))

# ── Legacy compat ───────────────────────────────────────────────
MEMORIES_TABLE = QDRANT_COLLECTION
