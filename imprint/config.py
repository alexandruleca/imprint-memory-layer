import json
import os
import re
from pathlib import Path


def get_project_dir() -> Path:
    """Return the imprint project root (parent of the imprint/ package)."""
    return Path(__file__).parent.parent


def get_data_dir() -> Path:
    """Resolve the data directory for storage. Priority:
    1. IMPRINT_DATA_DIR env var
    2. data/ relative to this package's parent (the repo root)
    """
    env = os.environ.get("IMPRINT_DATA_DIR")
    if env:
        return Path(env)
    return get_project_dir() / "data"


# ── Schema-driven resolution ────────────────────────────────────
# All settings now go through config_schema.resolve() which checks:
#   env var > data/config.json > hardcoded default
from .config_schema import resolve as _resolve


def _get(key: str):
    val, _ = _resolve(key)
    return val


# ── Embedding model ─────────────────────────────────────────────
MODEL_NAME = _get("model.name")
DEVICE = str(_get("model.device")).lower()


def _default_model_file() -> str:
    """Pick the best ONNX variant for the configured model + device.

    BGE-M3 ships int8/fp16 variants — pick by device. EmbeddingGemma
    and most other models have a single onnx/model.onnx.
    """
    model_lower = MODEL_NAME.lower()
    if "bge-m3" in model_lower:
        if DEVICE == "cpu":
            return "onnx/model_int8.onnx"
        if DEVICE == "gpu":
            return "onnx/model_fp16.onnx"
        try:
            import onnxruntime as _ort
            if "CUDAExecutionProvider" in _ort.get_available_providers():
                return "onnx/model_fp16.onnx"
        except Exception:
            pass
        return "onnx/model_int8.onnx"
    # Generic ONNX model — single file
    return "onnx/model.onnx"


_model_file_raw = _get("model.file")
MODEL_FILE = _default_model_file() if _model_file_raw == "auto" else _model_file_raw
EMBEDDING_DIM = _get("model.dim")
MAX_SEQ_LENGTH = _get("model.seq_length")
POOLING = str(_get("model.pooling")).lower()

# ── Qdrant ──────────────────────────────────────────────────────
QDRANT_COLLECTION = _get("collection")
QDRANT_VECTOR_NAME = "dense"
QDRANT_HOST = _get("qdrant.host")
QDRANT_PORT = _get("qdrant.port")
QDRANT_GRPC_PORT = _get("qdrant.grpc_port")
QDRANT_VERSION = _get("qdrant.version")
QDRANT_NO_SPAWN = _get("qdrant.no_spawn")

# ── Chunker ─────────────────────────────────────────────────────
CHUNK_OVERLAP = _get("chunker.overlap")
CHUNK_SIZE_CODE = _get("chunker.size_code")
CHUNK_SIZE_PROSE = _get("chunker.size_prose")
# Legacy alias
CHUNK_SIZE_CHARS = int(os.environ.get("IMPRINT_CHUNK_SIZE", str(CHUNK_SIZE_PROSE)))
CHUNK_HARD_MAX = _get("chunker.hard_max")
CHUNK_SEMANTIC_THRESHOLD = _get("chunker.semantic_threshold")

# ── Embedding runtime ──────────────────────────────────────────
ONNX_THREADS = _get("model.threads")
GPU_MEM_MB = _get("model.gpu_mem_mb")
GPU_DEVICE = _get("model.gpu_device")
BATCH_SIZE = _get("model.batch_size")  # 0 = auto

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


def register_workspace(name: str) -> bool:
    """Add name to known list without switching active. Returns True if newly added."""
    cfg = _read_workspace_config()
    known = cfg.setdefault("known", [])
    if name in known:
        return False
    known.append(name)
    _write_workspace_config(cfg)
    return True


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
