"""Centralized configuration schema for Imprint.

Single source of truth for all user-facing settings. Each setting has:
- A dot-notation key (e.g. "model.name") used by the CLI
- An env var name (e.g. IMPRINT_MODEL_NAME) for backwards compat
- A typed default value
- A short description

Precedence: env var > config.json > hardcoded default.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Setting:
    key: str         # dot-notation: "model.name"
    env: str         # IMPRINT_MODEL_NAME
    default: Any     # "Xenova/bge-m3"
    type: type       # str, int, float, bool
    desc: str        # one-line description


SETTINGS: list[Setting] = [
    # ── Embedding model ───────────────────────────────────────
    Setting("model.name",       "IMPRINT_MODEL_NAME",       "onnx-community/embeddinggemma-300m-ONNX",  str,   "HuggingFace embedding model repo"),
    Setting("model.file",       "IMPRINT_MODEL_FILE",       "auto",           str,   "ONNX model file (auto = pick by model/device)"),
    Setting("model.device",     "IMPRINT_DEVICE",           "auto",           str,   "Compute device: auto / cpu / gpu"),
    Setting("model.dim",        "IMPRINT_EMBEDDING_DIM",    768,              int,   "Embedding vector dimension"),
    Setting("model.seq_length", "IMPRINT_MAX_SEQ_LENGTH",   2048,             int,   "Token cap per embed call"),
    Setting("model.threads",    "IMPRINT_ONNX_THREADS",     4,                int,   "CPU intra-op threads for ONNX"),
    Setting("model.gpu_mem_mb", "IMPRINT_GPU_MEM_MB",       2048,             int,   "VRAM cap for ORT CUDA arena (MB)"),
    Setting("model.gpu_device", "IMPRINT_GPU_DEVICE",       0,                int,   "CUDA device ID"),
    Setting("model.pooling",    "IMPRINT_POOLING",          "auto",           str,   "Pooling strategy: auto / cls / mean / last"),

    # ── Qdrant ────────────────────────────────────────────────
    Setting("qdrant.host",      "IMPRINT_QDRANT_HOST",      "127.0.0.1",      str,   "Qdrant bind/connect host"),
    Setting("qdrant.port",      "IMPRINT_QDRANT_PORT",      6333,             int,   "Qdrant HTTP port"),
    Setting("qdrant.grpc_port", "IMPRINT_QDRANT_GRPC_PORT", 6334,             int,   "Qdrant gRPC port"),
    Setting("qdrant.version",   "IMPRINT_QDRANT_VERSION",   "v1.17.1",        str,   "Pinned Qdrant release for auto-download"),
    Setting("qdrant.no_spawn",  "IMPRINT_QDRANT_NO_SPAWN",  False,            bool,  "Skip auto-spawn (BYO server)"),

    # ── Chunker ───────────────────────────────────────────────
    Setting("chunker.overlap",             "IMPRINT_CHUNK_OVERLAP",        400,   int,   "Sliding overlap chars between chunks"),
    Setting("chunker.size_code",           "IMPRINT_CHUNK_SIZE_CODE",      4000,  int,   "Soft target chunk size for code"),
    Setting("chunker.size_prose",          "IMPRINT_CHUNK_SIZE_PROSE",     6000,  int,   "Soft target chunk size for prose"),
    Setting("chunker.hard_max",            "IMPRINT_CHUNK_HARD_MAX",       8000,  int,   "Absolute max chunk size"),
    Setting("chunker.semantic_threshold",  "IMPRINT_SEMANTIC_THRESHOLD",   0.5,   float, "Topic-shift threshold (lower = sharper splits)"),

    # ── Tagger ────────────────────────────────────────────────
    Setting("tagger.zero_shot",    "IMPRINT_ZERO_SHOT_TAGS",        True,        bool, "Enable zero-shot topic tagging"),
    Setting("tagger.llm",          "IMPRINT_LLM_TAGS",              False,       bool, "Enable LLM topic tagging (replaces zero-shot)"),
    Setting("tagger.llm_provider", "IMPRINT_LLM_TAGGER_PROVIDER",   "anthropic", str,  "LLM provider: anthropic / openai / ollama / vllm / gemini"),
    Setting("tagger.llm_model",    "IMPRINT_LLM_TAGGER_MODEL",      "claude-haiku-4-5", str, "LLM tagger model name"),
    Setting("tagger.llm_base_url", "IMPRINT_LLM_TAGGER_BASE_URL",   "",          str,  "LLM tagger API base URL override"),

    # ── Collection ────────────────────────────────────────────
    Setting("collection",       "IMPRINT_COLLECTION",       "memories",       str,   "Default Qdrant collection name"),
]

SETTINGS_BY_KEY: dict[str, Setting] = {s.key: s for s in SETTINGS}
SETTINGS_BY_ENV: dict[str, Setting] = {s.env: s for s in SETTINGS}


# ── Config file I/O ──────────────────────────────────────────

def _config_path() -> Path:
    from . import config as _cfg
    return _cfg.get_data_dir() / "config.json"


def read_config() -> dict:
    """Read config.json. Returns empty dict if missing/corrupt."""
    p = _config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def write_config(cfg: dict) -> None:
    """Atomic write of config.json. Prunes empty groups."""
    # Prune empty nested dicts
    pruned = {k: v for k, v in cfg.items() if v or not isinstance(v, dict)}
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        os.write(fd, (json.dumps(pruned, indent=2) + "\n").encode())
        os.close(fd)
        os.replace(tmp, str(p))
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


def _get_nested(d: dict, key: str) -> tuple[Any, bool]:
    """Get a value from nested dict using dot-notation key.
    Returns (value, found)."""
    parts = key.split(".")
    cur = d
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    if not isinstance(cur, dict) or parts[-1] not in cur:
        return None, False
    return cur[parts[-1]], True


def _set_nested(d: dict, key: str, value: Any) -> None:
    """Set a value in nested dict using dot-notation key."""
    parts = key.split(".")
    cur = d
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def _del_nested(d: dict, key: str) -> bool:
    """Delete a value from nested dict. Prunes empty parent dicts.
    Returns True if key existed."""
    parts = key.split(".")
    # Walk to parent, tracking path for pruning
    stack: list[tuple[dict, str]] = []
    cur = d
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return False
        stack.append((cur, part))
        cur = cur[part]
    if not isinstance(cur, dict) or parts[-1] not in cur:
        return False
    del cur[parts[-1]]
    # Prune empty parents bottom-up
    for parent, pkey in reversed(stack):
        if isinstance(parent[pkey], dict) and not parent[pkey]:
            del parent[pkey]
    return True


# ── Resolution ───────────────────────────────────────────────

def _cast(raw: str, typ: type) -> Any:
    """Cast a string value to the setting's type."""
    if typ is bool:
        return raw.lower() in ("1", "true", "on", "yes")
    if typ is int:
        return int(raw)
    if typ is float:
        return float(raw)
    return raw


def _cast_config(val: Any, typ: type) -> Any:
    """Cast a config.json value (already parsed JSON) to the setting's type."""
    if typ is bool:
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("1", "true", "on", "yes")
    if typ is int:
        return int(val)
    if typ is float:
        return float(val)
    return str(val)


def resolve(key: str) -> tuple[Any, str]:
    """Resolve a setting value. Returns (value, source).
    Source is one of: "env", "config", "default"."""
    s = SETTINGS_BY_KEY.get(key)
    if s is None:
        raise KeyError(f"unknown setting: {key}")

    # 1. Env var takes priority
    env_val = os.environ.get(s.env)
    if env_val is not None:
        return _cast(env_val, s.type), "env"

    # 2. Config file
    cfg = read_config()
    cfg_val, found = _get_nested(cfg, key)
    if found:
        return _cast_config(cfg_val, s.type), "config"

    # 3. Default
    return s.default, "default"


def resolve_all() -> list[tuple[str, Any, str, Any, str]]:
    """Resolve all settings. Returns list of (key, value, source, default, desc)."""
    cfg = read_config()
    results = []
    for s in SETTINGS:
        # Check env
        env_val = os.environ.get(s.env)
        if env_val is not None:
            results.append((s.key, _cast(env_val, s.type), "env", s.default, s.desc))
            continue
        # Check config
        cfg_val, found = _get_nested(cfg, s.key)
        if found:
            results.append((s.key, _cast_config(cfg_val, s.type), "config", s.default, s.desc))
            continue
        # Default
        results.append((s.key, s.default, "default", s.default, s.desc))
    return results


# ── Mutations ────────────────────────────────────────────────

def set_value(key: str, raw: str) -> Any:
    """Validate and persist a setting to config.json.
    Returns the cast value."""
    s = SETTINGS_BY_KEY.get(key)
    if s is None:
        raise KeyError(f"unknown setting: {key}")
    try:
        val = _cast(raw, s.type)
    except (ValueError, TypeError) as e:
        raise ValueError(f"invalid value for {key} (expected {s.type.__name__}): {e}")
    # For JSON, bools stay native; ints/floats stay numeric
    json_val: Any = val
    cfg = read_config()
    _set_nested(cfg, key, json_val)
    write_config(cfg)
    return val


def reset_value(key: str) -> bool:
    """Remove a setting override. Returns True if it existed."""
    if key not in SETTINGS_BY_KEY:
        raise KeyError(f"unknown setting: {key}")
    cfg = read_config()
    removed = _del_nested(cfg, key)
    if removed:
        write_config(cfg)
    return removed


def reset_all() -> None:
    """Clear all overrides."""
    write_config({})


# ── CLI output functions (called from Go via python -c) ──────

C_RESET = "\033[0m"
C_GREEN = "\033[0;32m"
C_CYAN = "\033[0;36m"
C_YELLOW = "\033[1;33m"
C_DIM = "\033[2m"
C_BOLD = "\033[1m"


def cli_list() -> None:
    """Print all settings with current values, grouped."""
    rows = resolve_all()
    print()
    print(f"{C_GREEN}═══ Imprint Configuration ═══{C_RESET}")
    print()

    current_group = ""
    # Find max key length for alignment
    max_key = max(len(r[0]) for r in rows)
    max_val = max(len(str(r[1])) for r in rows)

    for key, val, source, default, desc in rows:
        group = key.split(".")[0] if "." in key else ""
        if group != current_group:
            if current_group:
                print()
            current_group = group

        val_str = str(val)
        if source == "default":
            src_str = f"{C_DIM}(default){C_RESET}"
            line = f"  {C_DIM}{key:<{max_key}}{C_RESET}  {val_str:<{max_val}}  {src_str}"
        elif source == "config":
            src_str = f"{C_CYAN}(config.json){C_RESET}"
            line = f"  {C_BOLD}{key:<{max_key}}{C_RESET}  {C_CYAN}{val_str:<{max_val}}{C_RESET}  {src_str}"
        else:  # env
            src_str = f"{C_YELLOW}(env){C_RESET}"
            line = f"  {C_BOLD}{key:<{max_key}}{C_RESET}  {C_YELLOW}{val_str:<{max_val}}{C_RESET}  {src_str}"
        print(line)

    print()


def cli_get(key: str) -> None:
    """Print one setting."""
    try:
        val, source = resolve(key)
    except KeyError:
        print(f"unknown setting: {key}", file=sys.stderr)
        _print_did_you_mean(key)
        sys.exit(1)
    s = SETTINGS_BY_KEY[key]
    print(f"{key} = {val}")
    print(f"  source:  {source}")
    print(f"  env var: {s.env}")
    print(f"  default: {s.default}")
    print(f"  {s.desc}")


def cli_set(key: str, value: str) -> None:
    """Set a config value."""
    try:
        val = set_value(key, value)
    except KeyError:
        print(f"unknown setting: {key}", file=sys.stderr)
        _print_did_you_mean(key)
        sys.exit(1)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    print(f"{C_GREEN}[+]{C_RESET} {key} = {val}")


def cli_reset(key: str) -> None:
    """Reset one setting to default."""
    try:
        removed = reset_value(key)
    except KeyError:
        print(f"unknown setting: {key}", file=sys.stderr)
        _print_did_you_mean(key)
        sys.exit(1)
    if removed:
        s = SETTINGS_BY_KEY[key]
        print(f"{C_GREEN}[+]{C_RESET} {key} reset to default ({s.default})")
    else:
        print(f"{C_DIM}[-]{C_RESET} {key} was already at default")


def cli_reset_all() -> None:
    """Reset all settings to defaults."""
    reset_all()
    print(f"{C_GREEN}[+]{C_RESET} all config overrides cleared")


def cli_status_overrides() -> None:
    """Print non-default settings for imprint status. Outputs nothing if all defaults."""
    rows = resolve_all()
    overrides = [(k, v, src) for k, v, src, _d, _desc in rows if src != "default"]
    if not overrides:
        return
    max_key = max(len(k) for k, _, _ in overrides)
    print("  Config overrides:")
    for key, val, source in overrides:
        if source == "config":
            src_tag = f"{C_CYAN}(config.json){C_RESET}"
        else:
            src_tag = f"{C_YELLOW}(env){C_RESET}"
        print(f"    {key:<{max_key}} = {val}  {src_tag}")


def _print_did_you_mean(key: str) -> None:
    """Suggest similar keys on typo."""
    candidates = []
    for s in SETTINGS:
        # Simple substring match
        if key in s.key or s.key.split(".")[-1].startswith(key.split(".")[-1][:3]):
            candidates.append(s.key)
    if candidates:
        print(f"  did you mean: {', '.join(candidates[:5])}", file=sys.stderr)
    else:
        groups = sorted(set(s.key.split(".")[0] for s in SETTINGS if "." in s.key))
        print(f"  valid groups: {', '.join(groups)}, collection", file=sys.stderr)
