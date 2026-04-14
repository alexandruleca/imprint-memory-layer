import json
import os
import re
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
CHUNK_OVERLAP = int(os.environ.get("IMPRINT_CHUNK_OVERLAP", "400"))

# Per-modality target sizes. BGE-M3 handles 8192 tokens (~24-32k chars),
# so chunks can be large. Semantic chunking decides the real boundaries
# via topic-shift detection — these targets are soft caps, not the primary
# split signal. Bigger chunks = more context per retrieval hit.
# Code: whole classes/modules stay together. Oversized chunks get
# semantic sub-splitting where the logic shifts topic.
# Prose: full sections stay whole. SemanticChunker splits at topic change.
CHUNK_SIZE_CODE = int(os.environ.get("IMPRINT_CHUNK_SIZE_CODE", "4000"))
CHUNK_SIZE_PROSE = int(os.environ.get("IMPRINT_CHUNK_SIZE_PROSE", "6000"))
# Legacy alias — some callers may still import CHUNK_SIZE_CHARS.
CHUNK_SIZE_CHARS = int(os.environ.get("IMPRINT_CHUNK_SIZE", str(CHUNK_SIZE_PROSE)))

# Hard max ~4000 tokens — well within BGE-M3's 8192 context.
CHUNK_HARD_MAX = int(os.environ.get("IMPRINT_CHUNK_HARD_MAX", "16000"))

# SemanticChunker topic-shift threshold. Lower = more aggressive topic
# splits (each subtle shift starts a new chunk). 0.5 catches paragraph-level
# topic changes well; 0.7 is too lenient — adjacent paragraphs about
# different things get merged.
CHUNK_SEMANTIC_THRESHOLD = float(os.environ.get("IMPRINT_SEMANTIC_THRESHOLD", "0.5"))

# ── Workspaces ─────────────────────────────────────────────────
WORKSPACE_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')


def _workspace_file() -> Path:
    return get_data_dir() / "workspace.json"


def _read_workspace_config() -> dict:
    wf = _workspace_file()
    if wf.exists():
        try:
            return json.loads(wf.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"active": "default", "known": ["default"]}


def _write_workspace_config(cfg: dict) -> None:
    import tempfile
    wf = _workspace_file()
    wf.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(wf.parent), suffix=".tmp")
    try:
        os.write(fd, (json.dumps(cfg, indent=2) + "\n").encode())
        os.close(fd)
        os.replace(tmp, str(wf))
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def validate_workspace_name(name: str) -> str | None:
    """Return error message if invalid, None if ok."""
    if not name:
        return "name cannot be empty"
    if len(name) > 40:
        return "name too long (max 40)"
    if not WORKSPACE_NAME_RE.match(name):
        return "must be lowercase alphanumeric + hyphens, start with letter/digit"
    return None


def get_active_workspace() -> str:
    return _read_workspace_config().get("active", "default")


def get_known_workspaces() -> list[str]:
    return _read_workspace_config().get("known", ["default"])


def switch_workspace(name: str) -> None:
    cfg = _read_workspace_config()
    cfg["active"] = name
    if name not in cfg.get("known", []):
        cfg.setdefault("known", []).append(name)
    _write_workspace_config(cfg)


def remove_workspace(name: str) -> None:
    cfg = _read_workspace_config()
    if name in cfg.get("known", []):
        cfg["known"].remove(name)
    if cfg.get("active") == name:
        cfg["active"] = "default"
    _write_workspace_config(cfg)


def collection_name(workspace: str | None = None) -> str:
    ws = workspace or get_active_workspace()
    if ws == "default":
        return QDRANT_COLLECTION
    return f"memories_{ws}"


def graph_db_path(workspace: str | None = None) -> Path:
    ws = workspace or get_active_workspace()
    if ws == "default":
        return get_data_dir() / "imprint_graph.sqlite3"
    return get_data_dir() / f"imprint_graph_{ws}.sqlite3"


def wal_path(workspace: str | None = None) -> Path:
    ws = workspace or get_active_workspace()
    if ws == "default":
        return get_data_dir() / "wal.jsonl"
    return get_data_dir() / f"wal_{ws}.jsonl"
