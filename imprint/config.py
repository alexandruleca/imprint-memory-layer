import json
import os
import re
import hashlib
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
from . import model_profiles as _profiles


def _get(key: str):
    val, _ = _resolve(key)
    return val


# ── Embedding model ─────────────────────────────────────────────
MODEL_NAME = _get("model.name")
DEVICE = str(_get("model.device")).lower()
MODEL_FAMILY = _profiles.family(MODEL_NAME)
_revision_raw = _get("model.revision")
MODEL_REVISION = (_profiles.EMBEDDING_REVISION if MODEL_NAME == _profiles.EMBEDDING_REPO else "main") if _revision_raw == "auto" else _revision_raw
# A custom model repo names its GGUF file; there is no file to guess.
_model_file_raw = _get("model.file")
MODEL_FILE = ((_profiles.EMBEDDING_FILE if MODEL_NAME == _profiles.EMBEDDING_REPO else "")
              if _model_file_raw == "auto" else _model_file_raw)
EMBEDDING_DIM, _dim_source = _resolve("model.dim")
if _dim_source == "default" and MODEL_FAMILY == "embeddinggemma":
    EMBEDDING_DIM = 768
MAX_SEQ_LENGTH = _get("model.seq_length")
POOLING = str(_get("model.pooling")).lower()
QUERY_INSTRUCTION = _get("model.query_instruction")
if QUERY_INSTRUCTION == "auto":
    QUERY_INSTRUCTION = _profiles.QUERY_INSTRUCTION if MODEL_FAMILY == "qwen3" else ""


def embedding_identity() -> str:
    """Fingerprint vector semantics, including templates and truncation policy."""
    profile = {"encoder": 2, "repo": MODEL_NAME, "revision": MODEL_REVISION,
               "file": MODEL_FILE, "dim": EMBEDDING_DIM, "seq_length": MAX_SEQ_LENGTH,
               "pooling": POOLING, "instruction": QUERY_INSTRUCTION}
    return hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()

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

# ── Hugging Face Hub ───────────────────────────────────────────
def hub_token() -> str | None:
    """Token for Hugging Face downloads: HF_TOKEN env > config.json.

    Resolved on every call so a token saved in the dashboard reaches the
    running API server as well as the processes it starts. ``None`` lets
    huggingface_hub use its own login (``hf auth login``).
    """
    return _get("huggingface.token") or None


# ── Local model runtime ────────────────────────────────────────
CPU_THREADS = _get("model.threads") or max(1, (os.cpu_count() or 2) // 2)
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
    pinned = os.environ.get("IMPRINT_WORKSPACE")
    if pinned:
        error = validate_workspace_name(pinned)
        if error or pinned not in get_known_workspaces():
            raise ValueError(f"Invalid or unknown pinned workspace: {pinned}")
        return pinned
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
