"""Metadata tag derivation for chunks.

Four layered sources, ordered from cheap+reliable to rich+expensive:

  1. derive_deterministic(rel_path) → {lang, layer, kind} from file metadata.
  2. derive_keywords(content)       → domain:[...] via hand-rolled keyword dict.
  3. derive_zero_shot(vector)       → topics:[...] by cosine against label
                                       prototypes (on by default; opt-out via
                                       IMPRINT_ZERO_SHOT_TAGS=0).
  4. derive_llm(content)            → topics:[...] via LLM API (opt-in via
                                       IMPRINT_LLM_TAGS=1). Supports multiple
                                       providers: anthropic, openai, ollama,
                                       vllm, gemini. When enabled, replaces
                                       zero-shot.

`build_payload_tags` is the orchestrator — always runs (1) and (2), runs (3)
by default unless (4) is enabled. When LLM tagging is on, it replaces
zero-shot. Merges results into a single structured dict that matches the
vectorstore payload schema.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# ── 1. Deterministic (ext + path) ──────────────────────────────
_EXT_LANG = {
    ".py": "python",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".vue": "vue", ".svelte": "svelte",
    ".go": "go",
    ".rs": "rust",
    ".java": "java", ".kt": "kotlin",
    ".swift": "swift",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".c": "c", ".h": "c", ".hpp": "cpp",
    ".sql": "sql",
    ".graphql": "graphql", ".gql": "graphql",
    ".proto": "protobuf",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".md": "markdown", ".txt": "text",
    ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".html": "html", ".htm": "html", ".css": "css",
    # Document formats (routed through imprint.extractors).
    ".pdf": "pdf",
    ".docx": "docx", ".doc": "doc",
    ".pptx": "pptx",
    ".xlsx": "xlsx", ".csv": "csv", ".tsv": "csv",
    ".epub": "epub",
    ".rtf": "rtf",
    ".eml": "email", ".mbox": "email",
    ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".gif": "image", ".bmp": "image", ".tiff": "image",
    ".tif": "image", ".webp": "image",
}

_LAYER_PATTERNS = [
    ("api",       [r"(?:^|/)api(?:/|$)", r"(?:^|/)backend(?:/|$)", r"(?:^|/)server(?:/|$)"]),
    ("ui",        [r"(?:^|/)ui(?:/|$)", r"(?:^|/)frontend(?:/|$)", r"(?:^|/)components?(?:/|$)", r"(?:^|/)views?(?:/|$)", r"(?:^|/)pages?(?:/|$)"]),
    ("tests",     [r"(?:^|/)tests?(?:/|$)", r"(?:^|/)__tests__(?:/|$)", r"(?:^|/)spec(?:/|$)", r"\.test\.", r"\.spec\."]),
    ("infra",     [r"(?:^|/)infra(?:/|$)", r"(?:^|/)deploy(?:/|$)", r"(?:^|/)k8s(?:/|$)", r"(?:^|/)docker(?:/|$)", r"(?:^|/)terraform(?:/|$)"]),
    ("config",    [r"(?:^|/)config(?:/|$)", r"(?:^|/)settings(?:/|$)"]),
    ("migrations", [r"(?:^|/)migrations?(?:/|$)"]),
    ("docs",      [r"(?:^|/)docs?(?:/|$)", r"(?:^|/)documentation(?:/|$)"]),
    ("scripts",   [r"(?:^|/)scripts?(?:/|$)", r"(?:^|/)tools?(?:/|$)"]),
    ("cli",       [r"(?:^|/)cmd(?:/|$)", r"(?:^|/)cli(?:/|$)"]),
]
_LAYER_RES = [(name, [re.compile(p, re.IGNORECASE) for p in pats]) for name, pats in _LAYER_PATTERNS]


_DOC_EXTS = {".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".csv", ".tsv",
             ".epub", ".rtf", ".html", ".htm", ".eml", ".mbox"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp"}


def _derive_kind(rel_path: str) -> str:
    name = os.path.basename(rel_path).lower()
    stem, ext = os.path.splitext(name)
    # URL sources (prefixed with http:// or https://) → web.
    if rel_path.startswith(("http://", "https://")):
        return "web"
    if ext in _IMAGE_EXTS:
        return "ocr"
    if ext in _DOC_EXTS:
        return "document"
    if stem.startswith("test_") or stem.endswith("_test") or ".test." in name or ".spec." in name:
        return "test"
    if stem.startswith("migration_") or "migrate" in stem:
        return "migration"
    if name in {"readme.md", "readme.txt", "readme"}:
        return "readme"
    if stem.endswith("_types") or name.endswith(".d.ts"):
        return "types"
    if stem.startswith("__") or name == "__init__.py":
        return "module"
    return "source"


def derive_deterministic(rel_path: str) -> dict:
    is_url = rel_path.startswith(("http://", "https://"))
    ext = os.path.splitext(rel_path)[1].lower() if not is_url else ""
    if is_url:
        # URL path may or may not carry an ext; guess lang from URL ext
        # when present, otherwise default to html.
        from urllib.parse import urlparse
        path_part = urlparse(rel_path).path
        url_ext = os.path.splitext(path_part)[1].lower()
        lang = _EXT_LANG.get(url_ext, "html")
    else:
        lang = _EXT_LANG.get(ext, "")
    layer = ""
    p = rel_path.replace("\\", "/")
    for name, regs in _LAYER_RES:
        if any(r.search(p) for r in regs):
            layer = name
            break
    return {"lang": lang, "layer": layer, "kind": _derive_kind(rel_path)}


# ── 2. Keyword dictionary for domain tags ──────────────────────
# Order-insensitive. Each entry: domain_tag -> list of keyword patterns.
# Matches are whole-word (or part-of-identifier) and case-insensitive.
_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "auth": [
        r"\bauth(?:entication|orization)?\b", r"\bjwt\b", r"\boauth2?\b",
        r"\blogin\b", r"\blogout\b", r"\bsession\b", r"\btoken\b", r"\bcookie\b",
        r"\bpassword\b", r"\bbcrypt\b", r"\bsaml\b", r"\brbac\b",
    ],
    "db": [
        r"\bsql\b", r"\bquery\b", r"\bmigration\b", r"\btable\b", r"\bschema\b",
        r"\bpostgres\b", r"\bmysql\b", r"\bsqlite\b", r"\bmongo(?:db)?\b",
        r"\bredis\b", r"\borm\b", r"\bprisma\b", r"\bsequelize\b",
        r"\btransaction\b", r"\bindex\b", r"\bforeign\s+key\b",
    ],
    "api": [
        r"\brest\b", r"\brpc\b", r"\bgraphql\b", r"\bendpoint\b", r"\broute\b",
        r"\bwebhook\b", r"\bhttp\b", r"\brequest\b", r"\bresponse\b",
        r"\bcors\b", r"\bmiddleware\b", r"\bcontroller\b",
    ],
    "math": [
        r"\bmatrix\b", r"\bvector\b", r"\bquaternion\b", r"\bequation\b",
        r"\bgeometry\b", r"\blinear\s+algebra\b", r"\btrigonometry\b",
        r"\bdot\s+product\b", r"\bcross\s+product\b", r"\btransform(?:ation)?\b",
    ],
    "rendering": [
        r"\bwebgl\b", r"\bshader\b", r"\bthree\.?js\b", r"\bopengl\b",
        r"\bcanvas\b", r"\btexture\b", r"\bmaterial\b", r"\bmesh\b",
        r"\bgeometry\b", r"\brender(?:er|ing)?\b", r"\bframebuffer\b",
    ],
    "ui": [
        r"\breact\b", r"\bvue\b", r"\bsvelte\b", r"\bcomponent\b",
        r"\bhook\b", r"\bprop(?:s)?\b", r"\bstate\b", r"\bredux\b",
        r"\bzustand\b", r"\btailwind\b", r"\bmui\b", r"\bantd\b",
    ],
    "testing": [
        r"\bjest\b", r"\bmocha\b", r"\bvitest\b", r"\bpytest\b", r"\bcypress\b",
        r"\bplaywright\b", r"\bassertion\b", r"\bmock\b", r"\bstub\b", r"\bfixture\b",
    ],
    "infra": [
        r"\bdocker\b", r"\bkubernetes\b", r"\bk8s\b", r"\bterraform\b",
        r"\bci/?cd\b", r"\bgithub\s+actions\b", r"\bgitlab\s+ci\b",
        r"\bhelm\b", r"\bansible\b", r"\baws\b", r"\bgcp\b", r"\bazure\b",
    ],
    "ml": [
        r"\bembedding\b", r"\bvector\s+(?:store|db|database)\b", r"\bllm\b",
        r"\btransformer\b", r"\bonnx\b", r"\btokeniz(?:er|ation)\b",
        r"\bchunk(?:ing|er)?\b", r"\bcosine\s+similarity\b",
        r"\bhuggingface\b", r"\bopenai\b", r"\banthropic\b", r"\bclaude\b",
    ],
    "perf": [
        r"\bperformance\b", r"\blatenc(?:y|ies)\b", r"\bthroughput\b",
        r"\boom\b", r"\bmemory\s+leak\b", r"\bcpu\b", r"\bgpu\b",
        r"\bcache\b", r"\bcaching\b", r"\bbenchmark\b", r"\boptimiz(?:e|ation)\b",
    ],
    "security": [
        r"\bxss\b", r"\bcsrf\b", r"\bsql\s+injection\b", r"\bvulnerab(?:ility|le)\b",
        r"\bsanitiz(?:e|ation)\b", r"\bescape\b", r"\brate\s+limit(?:ing)?\b",
    ],
    "build": [
        r"\bwebpack\b", r"\bvite\b", r"\brollup\b", r"\besbuild\b",
        r"\bbabel\b", r"\bswc\b", r"\btsc\b", r"\bbuild\b",
    ],
    "payments": [
        r"\bstripe\b", r"\bpaypal\b", r"\bcheckout\b", r"\binvoice\b",
        r"\bsubscription\b", r"\bpricing\b",
    ],
}
_DOMAIN_RES = {k: [re.compile(p, re.IGNORECASE) for p in v] for k, v in _DOMAIN_KEYWORDS.items()}


def derive_keywords(content: str) -> list[str]:
    """Return list of domain tags matched by keyword dict. Deterministic order."""
    hits = []
    for domain, regs in _DOMAIN_RES.items():
        score = sum(1 for r in regs if r.search(content))
        if score >= 1:
            hits.append((domain, score))
    # Sort by score desc, cap to 5 most relevant
    hits.sort(key=lambda x: -x[1])
    return [h[0] for h in hits[:5]]


# ── 3. Zero-shot via embedding prototypes ──────────────────────
# Store pre-embedded label vectors in data/label_prototypes.npy once, reuse
# for every ingest. Cheap (one cosine per chunk per label).
_PROTOTYPE_LABELS = [
    "authentication and authorization",
    "database schema and queries",
    "api routing and http handlers",
    "mathematical operations and geometry",
    "3d rendering shaders and materials",
    "ui components state and rendering",
    "test fixtures and assertions",
    "infrastructure deployment and ci",
    "machine learning embeddings and models",
    "performance optimization and caching",
    "security vulnerabilities and sanitization",
    "build tooling and bundlers",
    "payments billing and subscriptions",
    "data ingestion and pipelines",
    "logging and observability",
    "error handling and retries",
]
_PROTOTYPE_TAGS = [
    "auth", "db", "api", "math", "rendering", "ui", "testing", "infra",
    "ml", "perf", "security", "build", "payments", "ingest", "logging", "errors",
]

_prototype_matrix = None


def _load_prototypes():
    global _prototype_matrix
    if _prototype_matrix is not None:
        return _prototype_matrix

    from . import config as _cfg
    import numpy as np

    cache_path = _cfg.get_data_dir() / "label_prototypes.npy"
    if cache_path.exists():
        try:
            mat = np.load(cache_path)
            if mat.shape == (len(_PROTOTYPE_LABELS), _cfg.EMBEDDING_DIM):
                _prototype_matrix = mat
                return mat
        except Exception:
            pass

    # Embed labels once and cache.
    from . import embeddings as emb
    vectors = emb.embed_documents_batch(_PROTOTYPE_LABELS, batch_size=8)
    import numpy as np
    mat = np.array(vectors, dtype=np.float32)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache_path, mat)
    except Exception:
        pass
    _prototype_matrix = mat
    return mat


def derive_zero_shot(vector: list[float], threshold: float = 0.35, top_k: int = 3) -> list[str]:
    """Match chunk vector against label prototypes via cosine similarity."""
    import numpy as np

    mat = _load_prototypes()
    v = np.array(vector, dtype=np.float32)
    # Both sides are L2-normalized by the embedder, so dot = cosine.
    sims = mat @ v
    ranked = sorted(
        [(float(s), _PROTOTYPE_TAGS[i]) for i, s in enumerate(sims)],
        reverse=True,
    )
    return [tag for s, tag in ranked[:top_k] if s >= threshold]


# ── 4. LLM-assisted topic tags (opt-in) ────────────────────────
_LLM_PROMPT = (
    "Classify this code/text chunk with 1-4 short lowercase topic tags. "
    "Tags should be nouns or noun-phrases suitable for filtering in a "
    "search index (e.g. 'auth', 'redis-cache', 'webgl-shader'). Return "
    "only a comma-separated list, no explanation.\n\n"
)

# Provider configs: default model + API key env var + base URL.
# Ollama and vLLM use OpenAI-compatible API with custom base_url.
_PROVIDER_DEFAULTS: dict[str, dict] = {
    "anthropic": {"model": "claude-haiku-4-5", "key_env": "ANTHROPIC_API_KEY"},
    "openai":    {"model": "gpt-4o-mini",      "key_env": "OPENAI_API_KEY"},
    "gemini":    {"model": "gemini-2.0-flash",  "key_env": "GOOGLE_API_KEY", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/"},
    "ollama":    {"model": "llama3.2",          "key_env": None, "base_url": "http://localhost:11434/v1"},
    "vllm":      {"model": "default",           "key_env": None, "base_url": "http://localhost:8000/v1"},
}


def _get_llm_provider() -> str:
    from .config_schema import resolve
    return str(resolve("tagger.llm_provider")[0]).lower()


def _get_llm_model() -> str:
    from .config_schema import resolve
    val, source = resolve("tagger.llm_model")
    if source != "default":
        return str(val)
    # If user didn't override model, use provider-specific default
    provider = _get_llm_provider()
    defaults = _PROVIDER_DEFAULTS.get(provider, _PROVIDER_DEFAULTS["anthropic"])
    return defaults["model"]


def _sanitize_tags(text: str) -> list[str]:
    """Parse comma-separated LLM response into sanitized tag list."""
    tags = [t.strip().lower() for t in text.split(",") if t.strip()]
    tags = [re.sub(r"[^a-z0-9\-]+", "-", t).strip("-") for t in tags]
    tags = [t for t in tags if 1 <= len(t) <= 32]
    return tags[:4]


def _derive_llm_anthropic(content: str, max_chars: int) -> list[str]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return []
    try:
        import anthropic
    except ImportError:
        return []
    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(
        model=_get_llm_model(),
        max_tokens=60,
        messages=[{"role": "user", "content": _LLM_PROMPT + content[:max_chars]}],
    )
    text = resp.content[0].text if resp.content else ""
    return _sanitize_tags(text)


def _derive_llm_openai_compat(content: str, max_chars: int) -> list[str]:
    """OpenAI-compatible provider (openai, ollama, vllm, gemini)."""
    provider = _get_llm_provider()
    defaults = _PROVIDER_DEFAULTS.get(provider, _PROVIDER_DEFAULTS["openai"])

    key_env = defaults.get("key_env")
    api_key = os.environ.get(key_env) if key_env else os.environ.get("IMPRINT_LLM_TAGGER_API_KEY", "no-key-needed")
    if key_env and not api_key:
        return []

    from .config_schema import resolve
    configured_url = resolve("tagger.llm_base_url")[0]
    base_url = configured_url if configured_url else defaults.get("base_url")

    try:
        import openai
    except ImportError:
        return []
    kwargs: dict = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = openai.OpenAI(**kwargs)
    resp = client.chat.completions.create(
        model=_get_llm_model(),
        max_tokens=60,
        messages=[
            {"role": "user", "content": _LLM_PROMPT + content[:max_chars]},
        ],
    )
    text = resp.choices[0].message.content or "" if resp.choices else ""
    return _sanitize_tags(text)


def derive_llm(content: str, max_chars: int = 3000) -> list[str]:
    """Opt-in: ask an LLM for topic tags.

    Provider controlled by IMPRINT_LLM_TAGGER_PROVIDER (default: anthropic).
    Model controlled by IMPRINT_LLM_TAGGER_MODEL (default per provider).
    Base URL override: IMPRINT_LLM_TAGGER_BASE_URL.
    """
    try:
        provider = _get_llm_provider()
        if provider == "anthropic":
            return _derive_llm_anthropic(content, max_chars)
        return _derive_llm_openai_compat(content, max_chars)
    except Exception:
        return []


# ── Orchestrator ───────────────────────────────────────────────
def build_payload_tags(
    content: str,
    rel_path: str = "",
    *,
    vector: list[float] | None = None,
    zero_shot: bool = True,
    llm: bool = False,
) -> dict:
    """Combine all four tag sources into the canonical payload shape.

    When ``llm=True``, LLM tagging replaces zero-shot (no point running both).
    Zero-shot is on by default (opt-out via ``zero_shot=False``).
    """
    d = derive_deterministic(rel_path) if rel_path else {"lang": "", "layer": "", "kind": ""}
    domain = derive_keywords(content)
    topics: list[str] = []

    if llm:
        # LLM tagging replaces zero-shot
        try:
            topics.extend(derive_llm(content))
        except Exception:
            pass
    elif zero_shot and vector is not None:
        try:
            topics.extend(derive_zero_shot(vector))
        except Exception:
            pass

    # Dedup while preserving order
    seen = set()
    topics = [t for t in topics if not (t in seen or seen.add(t))]

    return {
        "lang": d["lang"],
        "layer": d["layer"],
        "kind": d["kind"],
        "domain": domain,
        "topics": topics,
    }
