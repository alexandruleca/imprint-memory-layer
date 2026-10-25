"""Centralized configuration schema for Imprint.

Single source of truth for all user-facing settings. Each setting has:
- A dot-notation key (e.g. "model.name") used by the CLI
- An env var name (e.g. IMPRINT_MODEL_NAME) for backwards compat
- A typed default value
- A short description

Precedence: env var > config.json > hardcoded default.

Secret settings resolve like any other, but every display surface (CLI,
dashboard API) shows them through ``display_value`` so the stored value is
never echoed back.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import tool_formats
from .model_profiles import EMBEDDING_REPO, HELPER_REPO, HELPER_FILE


@dataclass(frozen=True)
class Choice:
    value: Any
    label: str
    detail: str = ""
    unavailable: str = ""  # why this host cannot use it; empty when it can


@dataclass(frozen=True)
class Setting:
    key: str         # dot-notation: "model.name"
    env: str         # IMPRINT_MODEL_NAME
    default: Any     # "Xenova/bge-m3"
    type: Callable[[Any], Any]  # str, int, float, bool, or a parser like device_index
    desc: str        # one-line description
    secret: bool = False  # credential: never displayed, only its last 4 characters
    # Known values: a tuple, or a function that reads them from this host.
    choices: tuple[Choice, ...] | Callable[[], tuple[Choice, ...]] = ()
    custom: bool = False  # values outside choices are valid too

    def options(self) -> tuple[Choice, ...]:
        return self.choices() if callable(self.choices) else self.choices


def device_index(raw: Any) -> str:
    """``auto`` or a CUDA device index."""
    value = str(raw).strip().lower()
    if value != "auto" and not value.isdigit():
        raise ValueError("expected auto or a CUDA device index")
    return value if value == "auto" else str(int(value))


def _gpu_devices() -> tuple[Choice, ...]:
    from . import cuda_devices
    major = cuda_devices.runtime_major()
    minimum = cuda_devices.minimum_compute(major)
    options = [Choice("auto", "Auto", "First GPU the installed CUDA runtime supports")]
    for device in cuda_devices.devices():
        compute = "%d.%d" % device.compute
        unavailable = ""
        if cuda_devices.supported(device, major) is False:
            unavailable = f"CUDA {major} needs compute capability {minimum[0]}.{minimum[1]}+, this GPU has {compute}"
        options.append(Choice(str(device.index), f"{device.index}: {device.name}",
                              f"{device.memory_mb / 1024:.0f} GB, compute {compute}", unavailable))
    return tuple(options)


AUTO = (Choice("auto", "Auto"),)
PROVIDER_DEFAULT = (Choice("", "Provider default"),)
TOOL_FORMATS = (Choice("auto", "Auto", "Detected from the model's chat template"),
                *(Choice(f.name, f.label, f.detail) for f in tool_formats.FORMATS.values()),
                Choice("none", "None", "Chat without tools"))
PROVIDERS = (Choice("local", "Local", "Bundled llama.cpp model"), Choice("anthropic", "Anthropic"),
             Choice("openai", "OpenAI"), Choice("gemini", "Gemini"), Choice("ollama", "Ollama"),
             Choice("vllm", "vLLM"))
GPU_LAYERS = (Choice(-1, "All layers"), Choice(0, "None", "CPU only"))


SETTINGS: list[Setting] = [
    # ── Embedding model ───────────────────────────────────────
    Setting("model.name",       "IMPRINT_MODEL_NAME",       EMBEDDING_REPO,  str,   "HuggingFace embedding model repo"),
    Setting("model.revision",   "IMPRINT_MODEL_REVISION",   "auto",           str,   "HF revision (auto = pinned default export, main for custom repos)", choices=AUTO, custom=True),
    Setting("model.file",       "IMPRINT_MODEL_FILE",       "auto",           str,   "ONNX model file (auto = pick by model/device)", choices=AUTO, custom=True),
    Setting("model.device",     "IMPRINT_DEVICE",           "cpu",            str,   "Compute device: auto / cpu / gpu",
            choices=(Choice("auto", "Auto", "GPU when the CUDA runtime is installed, else CPU"),
                     Choice("cpu", "CPU"), Choice("gpu", "GPU", "CUDA, falling back to CPU"))),
    Setting("model.dim",        "IMPRINT_EMBEDDING_DIM",    1024,             int,   "Embedding vector dimension (known model defaults inferred unless overridden)"),
    Setting("model.seq_length", "IMPRINT_MAX_SEQ_LENGTH",   2048,             int,   "Token cap per embed call"),
    Setting("model.threads",    "IMPRINT_ONNX_THREADS",     4,                int,   "CPU intra-op threads for ONNX"),
    Setting("model.gpu_mem_mb", "IMPRINT_GPU_MEM_MB",       2048,             int,   "VRAM cap for ORT CUDA arena (MB)"),
    Setting("model.gpu_device", "IMPRINT_GPU_DEVICE",       "auto",           device_index, "CUDA device index (auto = first GPU the CUDA runtime supports)",
            choices=_gpu_devices, custom=True),
    Setting("model.batch_size", "IMPRINT_BATCH_SIZE",       0,                int,   "Embedding batch size (0 = conservative auto: 1 CPU, 4 GPU)",
            choices=(Choice(0, "Auto", "1 on CPU, 4 on GPU"),), custom=True),
    Setting("model.pooling",    "IMPRINT_POOLING",          "auto",           str,   "Pooling strategy: auto / cls / mean / last",
            choices=(Choice("auto", "Auto", "By model family"), Choice("cls", "CLS token"),
                     Choice("mean", "Mean"), Choice("last", "Last token"))),
    Setting("model.query_instruction", "IMPRINT_QUERY_INSTRUCTION", "auto", str, "Query instruction (auto = model-specific; Qwen wraps Instruct/Query)",
            choices=AUTO, custom=True),
    Setting("model.output",     "IMPRINT_MODEL_OUTPUT",     "auto",           str,   "ONNX output name (auto = preferred pooled/token output)", choices=AUTO, custom=True),

    # ── Hugging Face Hub ──────────────────────────────────────
    # The env var is the standard one, so an exported HF_TOKEN overrides the
    # stored token. Unset in both, downloads fall back to `hf auth login`.
    Setting("huggingface.token", "HF_TOKEN",                "",               str,   "Hugging Face access token for model downloads", secret=True),

    # ── Qdrant ────────────────────────────────────────────────
    Setting("qdrant.host",      "IMPRINT_QDRANT_HOST",      "127.0.0.1",      str,   "Qdrant host (auto-spawn requires loopback)"),
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
    Setting("tagger.llm_provider", "IMPRINT_LLM_TAGGER_PROVIDER",   "local",     str,  "LLM provider: local / anthropic / openai / ollama / vllm / gemini", choices=PROVIDERS),
    Setting("tagger.llm_model",    "IMPRINT_LLM_TAGGER_MODEL",      "",          str,  "Server model override (default per provider); local uses GGUF settings",
            choices=PROVIDER_DEFAULT, custom=True),
    Setting("tagger.llm_base_url", "IMPRINT_LLM_TAGGER_BASE_URL",   "",          str,  "LLM tagger API base URL override"),

    # ── Tagger local model (llama-cpp) ───────────────────────
    Setting("tagger.local.model_repo",   "IMPRINT_TAGGER_MODEL_REPO",       HELPER_REPO,                    str,  "HF repo for local tagger GGUF"),
    Setting("tagger.local.model_file",   "IMPRINT_TAGGER_MODEL_FILE",       HELPER_FILE,                    str,  "GGUF filename within repo"),
    Setting("tagger.local.model_path",   "IMPRINT_TAGGER_MODEL_PATH",       "",                              str,  "Absolute path to GGUF (overrides repo/file)"),
    Setting("tagger.local.n_ctx",        "IMPRINT_TAGGER_N_CTX",            8192,                            int,  "Tagger model context window tokens"),
    Setting("tagger.local.n_gpu_layers", "IMPRINT_TAGGER_N_GPU_LAYERS",     -1,                              int,  "GPU layers to offload (-1 = all)", choices=GPU_LAYERS, custom=True),

    # ── Redaction (ADR 0060) ──────────────────────────────────
    # Rules for known credential formats always run; this adds a model.
    Setting("redact.privacy_filter", "IMPRINT_REDACT_PRIVACY_FILTER", False, bool,
            "Also redact secrets and account numbers OpenAI Privacy Filter finds (CPU; ~810 MB download on first use; seconds per chunk)"),

    # ── Ingest (docs + urls) ──────────────────────────────────
    Setting("ingest.max_file_bytes",    "IMPRINT_INGEST_MAX_FILE_BYTES",    2_000_000,    int,  "Per-file byte cap for source text indexing"),
    Setting("ingest.doc_formats",       "IMPRINT_INGEST_DOC_FORMATS",       "pdf,docx,pptx,xlsx,csv,epub,rtf,html,eml,json", str,  "Comma-separated doc formats enabled for file walker"),
    Setting("ingest.ocr_enabled",       "IMPRINT_INGEST_OCR",               False,       bool, "OCR for scanned PDFs + images (needs tesseract)"),
    Setting("ingest.ocr_lang",          "IMPRINT_INGEST_OCR_LANG",          "eng",       str,  "Tesseract language codes (e.g. 'eng+fra')"),
    Setting("ingest.max_doc_size_mb",   "IMPRINT_INGEST_MAX_DOC_MB",        25,          int,  "Per-file byte cap for document extraction"),
    Setting("ingest.url_timeout_sec",   "IMPRINT_INGEST_URL_TIMEOUT",       30,          int,  "HTTP connect timeout for URL fetch (seconds)"),
    Setting("ingest.url_read_timeout_sec","IMPRINT_INGEST_URL_READ_TIMEOUT", 300,         int,  "Per-chunk read timeout for URL fetch (seconds) — increase for very large files"),
    Setting("ingest.url_user_agent",    "IMPRINT_INGEST_URL_USER_AGENT",    "imprint/1.0", str, "HTTP User-Agent header for URL fetch"),
    Setting("ingest.url_respect_robots","IMPRINT_INGEST_URL_RESPECT_ROBOTS", True,       bool, "Check robots.txt before fetching URLs"),
    Setting("ingest.use_kreuzberg",    "IMPRINT_INGEST_USE_KREUZBERG",    False, bool, "Use kreuzberg (ELv2) for doc extraction — replaces PDF/DOCX/image extractors, adds archives"),
    Setting("ingest.use_obscura",      "IMPRINT_INGEST_USE_OBSCURA",      False, bool, "Use obscura headless browser for HTML URL fetch (JS rendering, needs obscura in PATH)"),
    Setting("ingest.url_follow",       "IMPRINT_INGEST_URL_FOLLOW",       False, bool, "Follow same-domain links found on ingested pages"),
    Setting("ingest.url_follow_depth", "IMPRINT_INGEST_URL_FOLLOW_DEPTH", 1,     int,  "Max link-follow depth (1 = links on seed pages only)"),
    Setting("ingest.url_follow_max",   "IMPRINT_INGEST_URL_FOLLOW_MAX",   20,    int,  "Max discovered URLs to queue per ingest run"),

    # ── Chat (optional local helper in dashboard) ───────────────
    Setting("voice.runtime_dir",   "IMPRINT_VOICE_RUNTIME_DIR",   "",                               str,   "Explicit external cache with the built native live voice runtime"),
    Setting("voice.avatar",        "IMPRINT_VOICE_AVATAR",        "echo",                           str,   "How Echo looks; change it anytime",
            choices=(Choice("echo", "Echo", "Holographic core, rings that turn and pulse as it speaks"),
                     Choice("iris", "Iris", "Indigo straight cut, pale lavender eyes"),
                     Choice("kai", "Kai", "Spiky orange hair, a determined frown"))),
    Setting("chat.enabled",        "IMPRINT_CHAT_ENABLED",        True,                             bool,  "Enable dashboard chat panel"),
    Setting("chat.provider",       "IMPRINT_CHAT_PROVIDER",       "local",                          str,   "Chat provider: local / vllm / openai / ollama / gemini / anthropic", choices=PROVIDERS),
    Setting("chat.model",          "IMPRINT_CHAT_MODEL",          "",                               str,   "Model name for remote providers (default per provider)",
            choices=PROVIDER_DEFAULT, custom=True),
    Setting("chat.base_url",       "IMPRINT_CHAT_BASE_URL",       "",                               str,   "Base URL override for OpenAI-compat providers"),
    Setting("chat.model_repo",     "IMPRINT_CHAT_MODEL_REPO",     HELPER_REPO,                      str,   "HF repo for GGUF auto-download (local provider)"),
    Setting("chat.model_file",     "IMPRINT_CHAT_MODEL_FILE",     HELPER_FILE,                      str,   "GGUF filename within repo (local provider)"),
    Setting("chat.model_path",     "IMPRINT_CHAT_MODEL_PATH",     "",                               str,   "Absolute path to GGUF (overrides repo/file, local provider)"),
    Setting("chat.n_ctx",          "IMPRINT_CHAT_N_CTX",          8192,                             int,   "Chat context window tokens"),
    Setting("chat.n_gpu_layers",   "IMPRINT_CHAT_N_GPU_LAYERS",   -1,                               int,   "GPU layers to offload (-1 = all)", choices=GPU_LAYERS, custom=True),
    Setting("chat.max_tokens",     "IMPRINT_CHAT_MAX_TOKENS",     1024,                             int,   "Max tokens per chat response"),
    Setting("chat.temperature",    "IMPRINT_CHAT_TEMPERATURE",    0.3,                              float, "Chat sampling temperature"),
    Setting("chat.max_tool_iters", "IMPRINT_CHAT_MAX_TOOL_ITERS", 6,                                int,   "Max tool-call iterations per chat turn"),
    Setting("chat.tool_format",    "IMPRINT_CHAT_TOOL_FORMAT",    "auto",                           str,   "Tool-call format of the local model's chat template (local provider)",
            choices=TOOL_FORMATS),

    # ── Session summarizer (opt-in, local-first) ─────────────
    Setting("summarizer.enabled",       "IMPRINT_SUMMARIZER_ENABLED",       False,              bool, "Enable LLM-based session summarizer (runs on Stop hook)"),
    Setting("summarizer.provider",      "IMPRINT_SUMMARIZER_PROVIDER",      "local",            str,  "Provider: local (shares tagger model) / ollama / vllm / anthropic / openai / gemini",
            choices=PROVIDERS),
    Setting("summarizer.model",         "IMPRINT_SUMMARIZER_MODEL",         "qwen3.5:4b",       str,  "Model name for server providers; local uses tagger.local settings"),
    Setting("summarizer.base_url",      "IMPRINT_SUMMARIZER_BASE_URL",      "",                 str,  "API base URL override"),
    Setting("summarizer.min_messages",  "IMPRINT_SUMMARIZER_MIN_MSGS",      5,                  int,  "Skip sessions with fewer messages"),
    Setting("summarizer.max_input_tokens","IMPRINT_SUMMARIZER_MAX_IN",      5000,               int,  "Estimated transcript token budget before summarizing (local also checks actual context)"),

    # ── Recall (MCP tool: batched reads, optional local model) ─
    # The model is the tagger's provider when it runs on this computer:
    # local (llama.cpp) or ollama/vllm at a loopback URL. Remote providers
    # never see recall questions or evidence.
    Setting("recall.llm",             "IMPRINT_RECALL_LLM",         False, bool, "Let the local model plan recall reads and answer from the evidence (tagger provider local, or ollama/vllm on loopback)"),
    Setting("recall.llm_timeout_sec", "IMPRINT_RECALL_LLM_TIMEOUT", 30,    int,  "Seconds one recall waits for the local model before returning the evidence without it"),

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

def _cast(raw: str, typ: Callable[[Any], Any]) -> Any:
    """Cast a string value to the setting's type."""
    if typ is bool:
        return raw.lower() in ("1", "true", "on", "yes")
    return typ(raw)


def _cast_config(val: Any, typ: Callable[[Any], Any]) -> Any:
    """Cast a config.json value (already parsed JSON) to the setting's type."""
    if typ is bool:
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("1", "true", "on", "yes")
    return typ(val)


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


def display_value(setting: Setting, value: Any) -> Any:
    """The value as shown to users: secrets reveal only whether they are set
    and their last four characters."""
    if not setting.secret or not value:
        return value
    value = str(value)
    return "****" + (value[-4:] if len(value) >= 12 else "")


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
    if s.choices and not s.custom and val not in {c.value for c in s.options()}:
        raise ValueError(f"invalid value for {key}: expected one of {', '.join(str(c.value) for c in s.options())}")
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
    rows = [(key, display_value(SETTINGS_BY_KEY[key], val), source, default, desc)
            for key, val, source, default, desc in rows]
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
    print(f"{key} = {display_value(s, val)}")
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
    print(f"{C_GREEN}[+]{C_RESET} {key} = {display_value(SETTINGS_BY_KEY[key], val)}")


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
    overrides = [(k, display_value(SETTINGS_BY_KEY[k], v), src) for k, v, src, _d, _desc in rows if src != "default"]
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
