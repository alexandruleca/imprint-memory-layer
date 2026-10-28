"""Chat agent for the Imprint dashboard chat panel.

Supports multiple providers:
- **local**: Runs a GGUF model in-process via llama-cpp-python (fully offline)
- **vllm / openai / ollama / gemini / anthropic**: Remote via OpenAI-compat API

All providers share one tool registry (JSON schemas over Imprint's MCP tools)
and one loop. Each backend speaks its native tool protocol: the local model
through its own chat template, remote providers through API function calling
(ADR 0059).

Structured so the import is cheap: we only try to import `llama_cpp` and
`huggingface_hub` at call time via helpers; the dashboard still works if they're missing.
The first local chat downloads the model; every installation has the binding (ADR 0086).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Generator, NamedTuple

from . import config
from . import config_schema
from . import server as _mcp_server
from . import tool_formats
from .chat_research import ResearchBudget, ResearchExhausted


# ── Optional deps ────────────────────────────────────────────

llama_cpp: Any = None
_llama_import_error: str | None = None


def _llama() -> Any:
    """The llama.cpp binding, or None when it does not load. Every
    installation has it (ADR 0086), so None means a damaged one."""
    global llama_cpp, _llama_import_error
    if llama_cpp is None:
        try:
            import llama_cpp as module  # type: ignore
            from imprint import _llama_compat
            _llama_compat.apply()
        except Exception as e:
            _llama_import_error = str(e)
            return None
        llama_cpp, _llama_import_error = module, None
    return llama_cpp


# ── Provider config ─────────────────────────────────────────

_CHAT_PROVIDER_DEFAULTS: dict[str, dict] = {
    "anthropic": {"model": "claude-haiku-4-5",  "key_env": "ANTHROPIC_API_KEY", "base_url": "https://api.anthropic.com/v1/"},
    "openai":    {"model": "gpt-4o-mini",       "key_env": "OPENAI_API_KEY"},
    "gemini":    {"model": "gemini-2.0-flash",   "key_env": "GOOGLE_API_KEY",  "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/"},
    "ollama":    {"model": "llama3.2",           "key_env": None, "base_url": "http://localhost:11434/v1"},
    "vllm":      {"model": "default",            "key_env": None, "base_url": "http://localhost:8000/v1"},
}


def _get_chat_provider() -> str:
    val, _ = config_schema.resolve("chat.provider")
    return str(val).lower()


def _get_chat_model() -> str:
    """Resolve chat model name: explicit config > provider default."""
    val, source = config_schema.resolve("chat.model")
    if source != "default" and val:
        return str(val)
    provider = _get_chat_provider()
    defaults = _CHAT_PROVIDER_DEFAULTS.get(provider, {})
    return defaults.get("model", "default")


def _is_remote_provider() -> bool:
    return _get_chat_provider() != "local"


# ── Tool registry ───────────────────────────────────────────
# One table: what each tool runs and the JSON schema the model sees. Each
# backend speaks its own native protocol from these schemas.

class Tool(NamedTuple):
    run: Callable[..., str]
    description: str
    params: dict[str, dict] = {}
    required: tuple[str, ...] = ()
    writes: bool = False


def _param(kind: str, description: str) -> dict:
    return {"type": kind, "description": description}


def _tool_search(query: str, workspace: str = "", **filters: Any) -> str:
    # Skip the auto-wake preamble that search() prepends on first call.
    # The chat agent has limited context (8k) — the full wake_up output
    # (project stats, essential context, facets) easily eats 3-4k chars,
    # leaving little room for actual results. The agent can call wake_up
    # or status explicitly when it needs an overview.
    ws, error = _mcp_server._validate_ws(workspace)
    if error:
        return error
    _mcp_server._woken_workspaces.add(ws)
    return _mcp_server.search(query=query, workspace=workspace, **filters)


def _tool_kg_add(subject: str, predicate: str, object: str, workspace: str = "", source: str = "") -> str:
    return _mcp_server.kg_edit(op="add", subject=subject, predicate=predicate, object=object,
                               source=source, workspace=workspace)


def _tool_kg_invalidate(fact_id: int, workspace: str = "") -> str:
    return _mcp_server.kg_edit(op="end", fact_id=fact_id, workspace=workspace)


_PROJECT = _param("string", "Only this project")
_SOURCE = _param("string", "Source path exactly as list_sources or search shows it")

TOOLS: dict[str, Tool] = {
    "search": Tool(
        _tool_search,
        "Semantic search across all stored memories. This is the primary tool. "
        "Use short, keyword-rich queries and narrow with filters when the domain is known.",
        {
            "query": _param("string", "2-4 keywords, e.g. 'auth middleware cors', not a full sentence"),
            "project": _PROJECT,
            "type": _param("string", "decision, pattern, finding, preference, bug or architecture"),
            "lang": _param("string", "Language: python, typescript, go, ..."),
            "layer": _param("string", "api, ui, tests, infra, cli or docs"),
            "kind": _param("string", "source, test, migration or readme"),
            "domain": _param("string", "Comma-separated domains: auth, db, api, ml, perf, security, ..."),
            "limit": _param("integer", "Maximum results (default 5)"),
            "offset": _param("integer", "Skip this many results, for paging"),
            "mode": _param("string", "hybrid (default) or semantic"),
            "source": _param("string", "Exact source path"),
        },
        required=("query",),
    ),
    "kg_query": Tool(
        _mcp_server.kg_query,
        "Query the temporal knowledge graph for structured facts (subject -> predicate -> object). "
        "Use for 'what does X use?', 'what was decided about Y?' and other relationship lookups. "
        "Each fact starts with its id, fact:<id>.",
        {
            "subject": _param("string", "Entity, partial match"),
            "predicate": _param("string", "Relationship, partial match"),
            "limit": _param("integer", "Maximum facts (default 20)"),
        },
    ),
    "status": Tool(
        _mcp_server.status,
        "Quick stats: total memories, active facts and memories per project.",
    ),
    "wake_up": Tool(
        _mcp_server.wake_up,
        "Full session context: projects, essential decisions, recent activity and active facts. "
        "Use when the user asks for a broad overview or what is in memory.",
    ),
    "list_sources": Tool(
        _mcp_server.list_sources,
        "List indexed source files with chunk counts. Start here to discover what code is in the knowledge base.",
        {
            "project": _PROJECT,
            "lang": _param("string", "Language: python, typescript, go, ..."),
            "layer": _param("string", "api, ui, tests, infra, config or docs"),
            "limit": _param("integer", "Maximum files (default 50)"),
        },
    ),
    "file_summary": Tool(
        _mcp_server.file_summary,
        "Overview of one indexed file: chunk count, tags, modification date and a preview. "
        "Use it before file_chunks to learn how many chunks exist.",
        {"source": _SOURCE, "project": _PROJECT},
        required=("source",),
    ),
    "file_chunks": Tool(
        _mcp_server.file_chunks,
        "Read the indexed content of one file by chunk index range.",
        {
            "source": _SOURCE,
            "start": _param("integer", "First chunk index, 0-based (default 0)"),
            "end": _param("integer", "Last chunk index, inclusive; -1 reads to the end (default)"),
            "project": _PROJECT,
        },
        required=("source",),
    ),
    "read_source": Tool(
        _mcp_server.read_source,
        "Read the authoritative redacted source and check whether all indexed chunks are present. "
        "Use next_start and the returned version to continue reading without mixing versions.",
        {"source": _SOURCE, "project": _PROJECT,
         "start": _param("integer", "Character offset (default 0)"),
         "max_chars": _param("integer", "Content characters (100–12000, default 4000)"),
         "version": _param("string", "Source version returned by the first read"),
         "verify": _param("boolean", "Verify indexed chunks against the manifest (default true)")},
        required=("source",),
    ),
    "store": Tool(
        _mcp_server.store,
        "Store a new memory: a self-contained note that makes sense months later and explains the WHY, not just the WHAT.",
        {
            "content": _param("string", "The note"),
            "project": _PROJECT,
            "type": _param("string", "decision, pattern, finding, preference, bug, architecture or milestone"),
            "tags": _param("string", "Comma-separated keywords"),
            "source": _param("string", "File path or conversation topic"),
        },
        required=("content",),
        writes=True,
    ),
    "kg_add": Tool(
        _tool_kg_add,
        "Add a structured fact to the knowledge graph, e.g. 'api-server uses NestJS' or 'auth decided JWT over sessions'.",
        {
            "subject": _param("string", "Entity"),
            "predicate": _param("string", "Relationship"),
            "object": _param("string", "Value"),
            "source": _param("string", "Where the fact came from"),
        },
        required=("subject", "predicate", "object"),
        writes=True,
    ),
    "kg_invalidate": Tool(
        _tool_kg_invalidate,
        "Mark a fact as ended, no longer true.",
        {"fact_id": _param("integer", "The number in the fact's fact:<id> from kg_query or wake_up")},
        required=("fact_id",),
        writes=True,
    ),
    "ingest_url": Tool(
        _mcp_server.ingest_url,
        "Fetch a URL (web page, PDF, ...), extract its content and store it as memories. "
        "Unchanged pages are skipped unless force is true.",
        {
            "url": _param("string", "http(s) URL"),
            "project": _param("string", "Project label (default 'urls')"),
            "force": _param("boolean", "Fetch again even if unchanged"),
        },
        required=("url",),
        writes=True,
    ),
}


def _tool_schemas(read_only: bool, extra: dict[str, Tool] | None = None) -> list[dict]:
    """The offered tools in the function-tool shape that OpenAI-compatible
    APIs and Hugging Face chat templates both take."""
    return [{"type": "function", "function": {
        "name": name, "description": tool.description,
        "parameters": {"type": "object", "properties": tool.params, "required": list(tool.required)},
    }} for name, tool in (TOOLS | (extra or {})).items() if not (read_only and tool.writes)]


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,), "array": (list,), "object": (dict,),
}


def _typed(value: Any, schema: dict) -> Any:
    """An argument typed by its parameter's schema. A string given for a
    non-string parameter is read as JSON: the Qwen XML format writes every
    value as text, and small models write numbers as strings."""
    if isinstance(value, str) and schema["type"] != "string":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _typed_args(name: str, args: dict, extra: dict[str, Tool] | None = None) -> dict:
    """A call's arguments typed by its tool's schema; others stay as written."""
    entry = (extra or {}).get(name) or TOOLS.get(name)
    params = entry.params if entry else {}
    return {key: _typed(value, params[key]) if key in params else value for key, value in args.items()}


def _is(value: Any, kind: str) -> bool:
    return isinstance(value, _JSON_TYPES[kind]) and (kind in ("boolean", "string") or not isinstance(value, bool))


def _run_tool(name: str, args: dict, workspace: str, read_only: bool, extra: dict[str, Tool] | None = None) -> str:
    """Run one call, its arguments typed (_typed_args). Every failure becomes
    the result the model reads."""
    tool = (extra or {}).get(name) or TOOLS.get(name)
    if tool is None:
        return f"[tool error] unknown tool: {name}"
    if read_only and tool.writes:
        return "Voice mode is read-only. Use the dashboard to make changes."
    # Session ownership wins over model-supplied workspace arguments.
    args = {key: value for key, value in args.items() if key != "workspace"}
    unknown = sorted(set(args) - set(tool.params))
    missing = [key for key in tool.required if key not in args]
    wrong = [f"{key} must be {tool.params[key]['type']}" for key, value in args.items()
             if key in tool.params and not _is(value, tool.params[key]["type"])]
    if unknown or missing or wrong:
        problems = ([f"missing required {', '.join(missing)}"] if missing else []) + \
                   ([f"unknown {', '.join(unknown)}"] if unknown else []) + wrong
        return f"[tool error] bad args for {name}: {'; '.join(problems)}"
    try:
        return tool.run(workspace=workspace, **args)
    except Exception as e:
        return f"[tool error] {name} raised: {e}"


SYSTEM_PROMPT = """You are Imprint, a local AI memory assistant. You help users explore, query, and manage their Imprint knowledge base — a semantic index of code, decisions, patterns, conversations, and structured facts. Everything runs locally and offline."""

# Guidance for using the tools; sent only when tools are offered.
TOOL_STRATEGY = """

# Strategy guide

1. **Start with search** for most questions. Use 2-4 keyword queries, not full sentences. Example: "auth middleware cors" not "what do we know about the authentication middleware and CORS configuration".
2. **Use filters** to narrow results when you know the domain. Searching for a Python bug? Add lang="python", type="bug".
3. **Drill into files** with the 3-step flow: list_sources → file_summary → file_chunks. Don't jump to file_chunks without knowing the source path.
4. **Cross-reference** search results with kg_query when you need to understand relationships between entities.
5. **Store findings** when the user shares a decision, discovers a pattern, or resolves a bug. Good memories are specific, self-contained, and explain WHY.
6. **Cite sources** — mention project names, file paths, or fact IDs so the user can trace your answers.
7. **Be concise** — summarize tool results, don't parrot them back. Pull out the key insight.
8. **Chain tools** across turns when needed. Example: search finds a pattern → file_summary gets context → file_chunks reads the actual code.
9. If the user asks you to remember or save something, use store or kg_add. If they say something is no longer true, use kg_invalidate.
10. If no tool is needed, answer directly."""


# ── Model loader ─────────────────────────────────────────────

_llm_lock = threading.Lock()
_llm: Any = None  # llama_cpp.Llama instance once loaded


def _models_dir() -> Path:
    d = config.get_data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cfg(key: str) -> Any:
    v, _ = config_schema.resolve(key)
    return v


def _resolve_local_model_path() -> tuple[Path | None, str | None]:
    """Resolve where the model SHOULD live without downloading.
    Returns (path, error). path may not yet exist."""
    explicit = _cfg("chat.model_path")
    if explicit:
        p = Path(explicit)
        return p, None
    fname = _cfg("chat.model_file")
    if not fname:
        return None, "chat.model_file is empty"
    return _models_dir() / fname, None


def download_model(progress_cb: Callable[[int, int], None] | None = None,
                   ) -> tuple[Path | None, str | None]:
    """Download the configured GGUF directly from HF resolve URL with
    incremental progress reported via progress_cb(downloaded, total).

    Returns (final_path, error)."""
    target, err = _resolve_local_model_path()
    if err or target is None:
        return None, err
    if target.exists():
        return target, None

    if _cfg("chat.model_path"):
        return None, f"model_path does not exist: {target}"
    try:
        from .local_llm import download
        return download(_cfg("chat.model_repo"), _cfg("chat.model_file"), target, progress_cb), None
    except Exception as exc:
        return None, f"download failed: {exc}"


def _resolve_model_path() -> tuple[Path | None, str | None]:
    """Return (existing_path_or_None, error)."""
    p, err = _resolve_local_model_path()
    if err or p is None:
        return None, err
    if p.exists():
        return p, None
    # Not cached — fall back to blocking download (no progress)
    return download_model()


def load_model() -> tuple[Any, str | None]:
    """Lazy-load the Llama model. Thread-safe singleton."""
    global _llm
    if _llm is not None:
        return _llm, None
    if _llama() is None:
        return None, _BINDING_BROKEN.format(_llama_import_error)

    with _llm_lock:
        if _llm is not None:
            return _llm, None

        path, err = _resolve_model_path()
        if err or path is None:
            return None, err or "could not resolve model path"

        try:
            from .local_llm import load
            from . import model_profiles as profiles
            managed = not _cfg("chat.model_path") and (_cfg("chat.model_repo"), _cfg("chat.model_file")) == (profiles.HELPER_REPO, profiles.HELPER_FILE)
            _llm = load(path, int(_cfg("chat.n_ctx")), int(_cfg("chat.n_gpu_layers")),
                        expected_sha256=profiles.HELPER_SHA256 if managed else None)
        except Exception as e:
            return None, f"model load failed: {e}"

    return _llm, None


# ── First-use provisioning ───────────────────────────────────
# The first local chat downloads the model. One process-wide job does it;
# every chat stream that needs it follows the job's events. A stream that
# closes never interrupts the job, which other chats may be following.

_BINDING_BROKEN = "llama.cpp does not load ({}). Run: imprint bootstrap --non-interactive"

class _Provisioning:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.finished = False
        self.error: str | None = None
        self._cond = threading.Condition()

    def emit(self, event: dict) -> None:
        with self._cond:
            self.events.append(event)
            self._cond.notify_all()

    def finish(self, error: str | None = None) -> None:
        with self._cond:
            self.error, self.finished = error, True
            self._cond.notify_all()

    def follow(self) -> Generator[dict, None, None]:
        """Every event from the start, then new ones until the job ends."""
        seen = 0
        while True:
            with self._cond:
                self._cond.wait_for(lambda: len(self.events) > seen or self.finished)
                batch, finished = self.events[seen:], self.finished
            seen += len(batch)
            yield from batch
            if finished:
                return


_provisioning_lock = threading.Lock()
_provisioning: _Provisioning | None = None


def _model_missing() -> bool:
    """The configured model is not downloaded yet. An explicit
    chat.model_path is the user's own file and is never downloaded."""
    path, err = _resolve_local_model_path()
    return not err and path is not None and not path.exists() and not _cfg("chat.model_path")


def _provision() -> _Provisioning:
    """Join the running provisioning job, or start one."""
    global _provisioning
    with _provisioning_lock:
        if _provisioning is None or _provisioning.finished:
            _provisioning = _Provisioning()
            threading.Thread(target=_run_provisioning, args=(_provisioning,), daemon=True).start()
        return _provisioning


def _run_provisioning(job: _Provisioning) -> None:
    try:
        if _model_missing():
            path, _ = _resolve_local_model_path()
            job.emit({"type": "download_start", "file": _cfg("chat.model_file"),
                      "repo": _cfg("chat.model_repo"), "path": str(path)})
            last_emit = 0.0

            def _cb(done: int, total: int) -> None:
                nonlocal last_emit
                now = time.time()
                if now - last_emit > 0.2 or (total and done >= total):
                    last_emit = now
                    job.emit({"type": "download_progress", "downloaded": done, "total": total})

            p, e = download_model(progress_cb=_cb)
            if e or p is None:
                raise RuntimeError(e or "download failed")
            job.emit({"type": "download_complete", "path": str(p)})
        job.finish()
    except Exception as e:
        job.finish(str(e))


def status() -> dict:
    """Reportable status for /api/chat/status without forcing a load."""
    enabled = bool(_cfg("chat.enabled"))
    provider = _get_chat_provider()

    if provider != "local":
        # Remote provider — always "ready" (no local model needed)
        defaults = _CHAT_PROVIDER_DEFAULTS.get(provider, {})
        key_env = defaults.get("key_env")
        missing_key = key_env and not os.environ.get(key_env)
        return {
            "enabled": enabled,
            "installed": True,
            "provider": provider,
            "model": _get_chat_model(),
            "model_ready": not missing_key,
            "model_path": "",
            "error": f"{key_env} not set" if missing_key else None,
        }

    # Local provider. A missing model is not an error: the first chat
    # downloads it. A binding that does not load is a damaged installation.
    installed = _llama() is not None
    broken = None if installed else _BINDING_BROKEN.format(_llama_import_error)
    explicit = _cfg("chat.model_path")
    if explicit:
        p = Path(explicit)
        return {
            "enabled": enabled,
            "installed": installed,
            "provider": "local",
            "model_ready": p.exists() or _llm is not None,
            "model_path": str(p),
            "error": broken or (None if p.exists() else f"model_path does not exist: {p}"),
        }

    fname = _cfg("chat.model_file")
    local = _models_dir() / fname if fname else None
    return {
        "enabled": enabled,
        "installed": installed,
        "provider": "local",
        "model_ready": (local is not None and local.exists()) or _llm is not None,
        "model_path": str(local) if local else "",
        "model_repo": _cfg("chat.model_repo"),
        "model_file": fname,
        "error": broken,
    }


# ── Local protocol: the model's own chat template ───────────
# The GGUF's template renders the tool schemas, assistant tool_calls and
# tool-role results itself. The reply's call grammar depends on the model
# family; tool_formats parses each family's shape (ADR 0061).

def _local_format(llm: Any) -> tuple[tool_formats.Format | None, str | None]:
    """The tool-call format chat.tool_format selects for the loaded model, and
    a notice when auto finds none, so chat runs without tools."""
    choice = str(_cfg("chat.tool_format"))
    if choice == "none":
        return None, None
    if choice != "auto":
        if choice not in tool_formats.FORMATS:
            raise ValueError(f"unknown chat.tool_format: {choice}")
        return tool_formats.FORMATS[choice], None
    formatter = llm._imprint_formatter
    fmt = tool_formats.detect(lambda messages, tools: formatter(messages=messages, tools=tools).prompt,
                              formatter.eos_token)
    if fmt is None:
        return None, ("This model's chat template writes tool calls in no format Imprint recognizes, so this chat "
                      "runs without memory tools. If the model can call tools, choose its format in Settings, "
                      "Chat, Tool-call format (chat.tool_format).")
    return fmt, None


def _local_turn(llm: Any, messages: list[dict], tools: list[dict] | None, fmt: tool_formats.Format | None,
                max_tokens: int, temperature: float, *, deadline=None, cancelled=lambda: False) -> Generator[str, None, list[dict]]:
    """One assistant turn of the local model. Yields reply text and returns
    the tool calls. Tool-call markup is never yielded: the stream holds text
    back from a call marker on, and a trailing partial marker until it resolves."""
    from .local_llm import INFERENCE_LOCK, generate
    buffer, shown = "", 0
    from .chat_analysis import AnalysisCancelled
    while not INFERENCE_LOCK.acquire(timeout=.1):
        if cancelled():
            raise AnalysisCancelled()
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('Local model wait exceeded the research deadline')
    try:
        if cancelled():
            raise AnalysisCancelled()
        generation = generate(llm, messages, max_tokens, temperature, tools=tools, deadline=deadline)
        try:
            for piece in generation:
                if cancelled():
                    raise AnalysisCancelled()
                buffer += piece
                end = tool_formats.shown(fmt, buffer) if fmt else len(buffer)
                if end > shown:
                    yield buffer[shown:end]
                    shown = end
        finally:
            generation.close()
    finally:
        INFERENCE_LOCK.release()
    calls = fmt.parse(buffer[shown:]) if fmt else []
    if not calls and len(buffer) > shown:
        yield buffer[shown:]  # held back, but not a tool call
    # Mistral's templates require call ids of 9 alphanumerics.
    return [{"id": f"call{n:05d}", **call} for n, call in enumerate(calls)]


def _trim_tool_result(result: str, max_chars: int = 4000) -> str:
    if len(result) <= max_chars:
        return result
    return result[:max_chars] + f"\n\n[...truncated {len(result) - max_chars} chars...]"


# ── Remote protocol: OpenAI-compatible function calling ─────

def _get_openai_client():
    """Build an OpenAI client for the configured remote provider."""
    import openai
    provider = _get_chat_provider()
    defaults = _CHAT_PROVIDER_DEFAULTS.get(provider, _CHAT_PROVIDER_DEFAULTS["openai"])

    key_env = defaults.get("key_env")
    api_key = (os.environ.get(key_env) if key_env
               else os.environ.get("IMPRINT_CHAT_API_KEY", "no-key-needed"))

    configured_url, _ = config_schema.resolve("chat.base_url")
    base_url = configured_url if configured_url else defaults.get("base_url")

    kwargs: dict = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return openai.OpenAI(**kwargs)


def _wire_message(message: dict) -> dict:
    """The API carries a call's arguments as a JSON string."""
    if not message.get("tool_calls"):
        return message
    return {**message, "tool_calls": [
        {**call, "function": {**call["function"], "arguments": json.dumps(call["function"]["arguments"])}}
        for call in message["tool_calls"]
    ]}


def _remote_turn(messages: list[dict], tools: list[dict] | None, max_tokens: int,
                 temperature: float, *, timeout: float | None = None, deadline=None,
                 cancelled=lambda: False) -> Generator[str, None, list[dict]]:
    """One assistant turn of the remote provider. Yields reply text and
    returns the tool calls assembled from the streamed tool_call deltas."""
    parts: dict[int, dict] = {}
    with _get_openai_client().chat.completions.create(
        model=_get_chat_model(),
        messages=[_wire_message(m) for m in messages],
        tools=tools,
        stream=True,
        max_tokens=max_tokens,
        temperature=temperature,
        **({"timeout": timeout} if timeout is not None else {}),
    ) as stream:
        for chunk in stream:
            if cancelled():
                from .chat_analysis import AnalysisCancelled
                raise AnalysisCancelled()
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('Model stream exceeded its deadline')
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
            for piece in delta.tool_calls or []:
                part = parts.setdefault(piece.index, {"id": "", "name": "", "arguments": ""})
                part["id"] = piece.id or part["id"]
                if piece.function:
                    part["name"] = piece.function.name or part["name"]
                    part["arguments"] += piece.function.arguments or ""
    calls = []
    for _, part in sorted(parts.items()):
        try:
            args = json.loads(part["arguments"] or "{}")
        except ValueError:
            args = None
        if not isinstance(args, dict):
            raise ValueError(f"provider sent malformed arguments for {part['name']}: {part['arguments'][:200]}")
        calls.append({"id": part["id"], "name": part["name"], "args": args})
    return calls


# ── Streaming tool loop ──────────────────────────────────────

def _prompt_tokens(llm, items, tools, remote):
    if remote:
        return (len(json.dumps({'messages': items, 'tools': tools}, ensure_ascii=False).encode()) + 2) // 3 + 512
    from .local_llm import INFERENCE_LOCK
    with INFERENCE_LOCK:
        prompt = llm._imprint_formatter(messages=items, tools=tools).prompt
        return len(llm.tokenize(prompt.encode(), add_bos=False, special=True))

def _history(prior_messages: list[dict]) -> list[dict]:
    """Stored conversation as native messages. A stored tool exchange is an
    assistant tool call followed by its tool-role result."""
    messages: list[dict] = []
    for n, m in enumerate(prior_messages):
        role = m.get("role")
        content = m.get("content", "")
        if role == "tool":
            call = {"id": f"hist{n:05d}", "type": "function", "function": {
                "name": m.get("tool_name") or "tool", "arguments": m.get("tool_args") or {}}}
            messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
        elif role in ("user", "assistant"):
            messages.append({"role": role, "content": content})
    return messages


def _research_fallback(messages: list[dict], *, spoken=False) -> str:
    """Keep retrieved evidence useful if the tools-free answer is unavailable.

    This is explicitly evidence, not an invented synthesis. Bound the excerpt
    size and use only this reply's results, after its last user message.
    """
    anchor = max(i for i, message in enumerate(messages) if message['role'] == 'user')
    evidence = [message['content'].strip() for message in messages[anchor + 1:]
                if message['role'] == 'tool' and message.get('content', '').strip()
                and not message['content'].startswith('[tool error]')]
    if spoken:
        return ("I reached the research limit and couldn't finish summarizing the results. "
                "Please ask a narrower follow-up so I can complete the answer.")
    intro = "I couldn't finish a complete answer from this research."
    if not evidence:
        return intro + " I don't have usable retrieved evidence to support an answer yet."
    excerpts = []
    for result in evidence[-3:]:
        excerpt = result[:1200] + ('\n[Excerpt shortened]' if len(result) > 1200 else '')
        excerpts.append('\n'.join('> ' + line for line in excerpt.splitlines()))
    return intro + " Retrieved evidence excerpts (incomplete, without synthesis):\n\n" + '\n\n'.join(excerpts)


def stream_reply(
    prior_messages: list[dict],
    user_message: str,
    workspace: str = "",
    *,
    cancel_event: threading.Event | None = None,
    read_only: bool = False,
) -> Generator[dict, None, None]:
    """Yield SSE-ready event dicts.

    Event types:
      {"type": "download_start", "file", "repo", "path"} / {"type": "download_progress", "downloaded", "total"}
      {"type": "download_complete", "path": str}
      {"type": "load_start"}                         # loading the local model into memory
      {"type": "notice", "text": str, "title"?: str}  # model capability or normal research completion
      {"type": "analysis", "text": str}              # section-worker progress; never worker findings
      {"type": "token", "text": str}
      {"type": "tool_call", "name": str, "args": dict}
      {"type": "tool_result", "name": str, "result": str}
      {"type": "assistant_message", "text": str}   # final cleaned assistant text
      {"type": "error", "error": str}
      {"type": "done"}
    """
    cancelled = lambda: cancel_event is not None and cancel_event.is_set()
    if cancelled():
        return
    remote = _is_remote_provider()
    llm = None

    if remote:
        # Validate remote provider connectivity
        provider = _get_chat_provider()
        defaults = _CHAT_PROVIDER_DEFAULTS.get(provider)
        if not defaults:
            yield {"type": "error", "error": f"unknown chat provider: {provider}"}
            yield {"type": "done"}
            return
        key_env = defaults.get("key_env")
        if key_env and not os.environ.get(key_env):
            yield {"type": "error", "error": f"{key_env} not set for provider '{provider}'"}
            yield {"type": "done"}
            return
    else:
        # ── Local: download the model on first use ──
        if _model_missing():
            job = _provision()
            for event in job.follow():
                if cancelled():
                    return
                yield event
            if job.error:
                yield {"type": "error", "error": job.error}
                yield {"type": "done"}
                return

        if _llm is None:
            yield {"type": "load_start"}
        llm, err = load_model()
        if err or llm is None:
            yield {"type": "error", "error": err or "model unavailable"}
            yield {"type": "done"}
            return

    # Remote providers call tools natively. A local model calls them in its
    # template's format; without one it chats without tools (ADR 0061).
    fmt = None
    if not remote:
        fmt, notice = _local_format(llm)
        if notice and not prior_messages:
            yield {"type": "notice", "text": notice}
    tools = _tool_schemas(read_only) if remote or fmt else None

    # Build message list
    from .device import current
    device_context = json.dumps({'current_device': current(), 'working_directory': os.getcwd()})
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT + (TOOL_STRATEGY if tools else "") +
        '\n\n# Device and path context\n' + device_context +
        '\nShared memories and conversations remain useful on every device. '
        'Original paths in historical text and tool results are provenance, not local file instructions. '
        'Only path_context.local_source_path is a verified local path hint; verify live contents before edits.'}]
    if read_only:
        messages[0]["content"] += (
            '\n\nThis is an English voice conversation. Keep spoken replies brief, usually two or three sentences. '
            'Use plain speech without markdown or code blocks. Only read tools are available in voice mode. '
            'Do not attempt to save, change, ingest or delete anything; direct changes to the dashboard.'
        )
    messages += _history(prior_messages)
    messages.append({"role": "user", "content": user_message})

    max_tokens = int(_cfg("chat.max_tokens"))
    temperature = float(_cfg("chat.temperature"))

    from .chat_analysis import AnalysisCancelled, SectionAnalysis, WORKER_TIMEOUT
    from .local_llm import INFERENCE_LOCK, generate
    budget = ResearchBudget(seconds=int(_cfg('chat.research_timeout_sec')),
                            tokens=int(_cfg('chat.research_token_budget')),
                            patience=int(_cfg('chat.research_no_progress_rounds')))

    def measure(items, offered):
        return _prompt_tokens(llm, items, offered, remote)

    def worker_generate(items, output_tokens):
        budget.reserve(measure(items, None), output_tokens)
        deadline = min(time.monotonic() + WORKER_TIMEOUT, budget.deadline)
        if remote:
            generation = _remote_turn(items, None, output_tokens, 0.1,
                                      timeout=min(WORKER_TIMEOUT, budget.remaining()), deadline=deadline, cancelled=cancelled)
            try:
                for piece in generation:
                    if cancelled():
                        raise AnalysisCancelled()
                    budget.check_time()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Section worker time limit reached")
                    yield piece
            finally:
                generation.close()
        else:
            while not INFERENCE_LOCK.acquire(timeout=.1):
                if cancelled():
                    raise AnalysisCancelled()
                if time.monotonic() >= deadline:
                    raise TimeoutError("Section worker waited too long for the local model")
            try:
                if cancelled():
                    raise AnalysisCancelled()
                generation = generate(llm, items, output_tokens, 0.1, deadline=deadline)
                try:
                    yield from generation
                finally:
                    generation.close()
            finally:
                INFERENCE_LOCK.release()

    analysis = SectionAnalysis(context=int(_cfg("chat.n_ctx")) if remote else llm.n_ctx(),
                               workers=int(_cfg("chat.analysis_workers")), max_tokens=max_tokens,
                               measure=measure, generate=worker_generate, cancelled=cancelled)
    reread = Tool(analysis.read, "Reread an original passage from section analysis in this reply. "
                  "Use source_id and character offsets from worker reports to verify exact details.", {
                      "source_id": _param("string", "Reply-local source ID exactly as section analysis shows it"),
                      "start": _param("integer", "Starting character offset (default 0)"),
                      "max_chars": _param("integer", "Characters to read, 1–4000 (default 2000)"),
                  }, required=("source_id",))

    def extra_tools():
        return {"read_chat_section": reread} if analysis.sources else {}

    def offered_tools():
        return _tool_schemas(read_only, extra_tools()) if tools is not None else None

    final_assistant_chunks: list[str] = []
    finish_reason = None
    completed_rounds = 0

    # Tool rounds and answer generation have separate budgets. In particular,
    # the last batch of results must reach an answer turn (ADRs 0101, 0102).
    while True:
        if cancelled():
            return
        finish_reason = finish_reason or budget.reason()
        finalizing = finish_reason is not None
        if finalizing:
            explanation = {
                'no_progress': 'Recent lookups are no longer adding new evidence.',
                'context': 'The research context is full.',
                'time': 'The research time budget is complete.',
                'tokens': 'The research token budget is complete.',
            }[finish_reason]
            yield {'type': 'notice', 'code': 'research_complete', 'title': 'Finishing the answer',
                   'text': explanation + ' Answering with the information gathered so far.'}
            messages[0] = {**messages[0], 'content': messages[0]['content'].replace(TOOL_STRATEGY, '') +
                '\n\n# Finish the answer\nThe research budget is complete and no tools are available. '
                'Answer the original user question now using the evidence already gathered. '
                'Cite available sources, state uncertainties and clearly distinguish missing information. '
                'Do not request or emit tool calls, promise more research, or claim actions not confirmed by tool results.'}
        active_offers = (lambda: None) if finalizing else offered_tools
        try:
            messages = yield from analysis.prepare(messages, active_offers)
        except AnalysisCancelled:
            return
        except ResearchExhausted as error:
            if not finalizing:
                finish_reason = error.reason
                continue
            fallback = _research_fallback(messages, spoken=read_only)
            yield {'type': 'token', 'text': fallback}
            final_assistant_chunks.append(fallback)
            break
        except Exception as e:
            if finalizing:
                fallback = _research_fallback(messages, spoken=read_only)
                yield {'type': 'token', 'text': fallback}
                final_assistant_chunks.append(fallback)
                break
            if not budget.remaining():
                finish_reason = 'time'
                continue
            if completed_rounds:
                # A source/section limit reached during research should still
                # leave room to answer with the evidence already obtained.
                finish_reason = 'context'
                continue
            yield {"type": "error", "error": f"section analysis failed: {e}"}
            yield {"type": "done"}
            return
        if cancelled():
            return
        active_tools = active_offers()
        if not finalizing:
            try:
                budget.reserve(measure(messages, active_tools), max_tokens)
            except ResearchExhausted as error:
                finish_reason = error.reason
                continue
        # A reserved answer has its own bounded time and output allowance.
        deadline = time.monotonic() + WORKER_TIMEOUT if finalizing else budget.deadline
        if remote:
            turn = _remote_turn(messages, active_tools, max_tokens, temperature,
                                timeout=WORKER_TIMEOUT if finalizing else budget.remaining(),
                                deadline=deadline, cancelled=cancelled)
        else:
            turn = _local_turn(llm, messages, active_tools, fmt, max_tokens, temperature,
                               deadline=deadline, cancelled=cancelled)
        text = ""
        try:
            while True:
                piece = next(turn)
                if cancelled():
                    return
                text += piece
                yield {"type": "token", "text": piece}
        except StopIteration as end:
            calls = end.value
        except Exception as e:
            if cancelled():
                return
            if not finalizing and (isinstance(e, ResearchExhausted) or not budget.remaining()):
                if text.strip():
                    final_assistant_chunks.append(text.strip() + '\n')
                    messages.append({'role': 'assistant', 'content': text})
                finish_reason = 'time'
                continue
            if finalizing:
                # Preserve any streamed answer and add an honest evidence
                # fallback. A bounded research stop is never a failed reply.
                fallback = ('\n\n' if text.strip() else '') + _research_fallback(messages, spoken=read_only)
                yield {'type': 'token', 'text': fallback}
                final_assistant_chunks.append(text + fallback)
                break
            yield {"type": "error", "error": f"generation failed: {e}"}
            yield {"type": "done"}
            return
        finally:
            turn.close()

        if finalizing:
            # Keep parsing local markup so models that ignore the no-tools
            # instruction cannot leak calls or execute a final mutation.
            if not text.strip() or calls:
                fallback = ('\n\n' if text.strip() else '') + _research_fallback(messages, spoken=read_only)
                yield {'type': 'token', 'text': fallback}
                text += fallback
            final_assistant_chunks.append(text)
            break
        if not calls:
            final_assistant_chunks.append(text)
            break
        calls = [{**call, "args": _typed_args(call["name"], call["args"], extra_tools())} for call in calls]

        if text.strip():
            final_assistant_chunks.append(text.strip() + "\n")
        # Content stays a string, empty without text: some chat templates
        # (Qwen3's GGUF) fail on a null or missing content.
        messages.append({"role": "assistant", "content": text, "tool_calls": [
            {"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": call["args"]}}
            for call in calls
        ]})
        round_results = []
        for call in calls:
            if cancelled():
                return
            yield {"type": "tool_call", "name": call["name"], "args": call["args"]}
            if budget.remaining():
                result = _run_tool(call["name"], call["args"], workspace, read_only, extra_tools())
            else:
                # Complete the provider protocol for pending calls without
                # starting more work or mutations after a deadline.
                result = '[research stopped] This call was not executed because the time budget ended.'
            if not analysis.workers:
                result = _trim_tool_result(result)
            yield {"type": "tool_result", "name": call["name"], "result": result}
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            entry = extra_tools().get(call['name']) or TOOLS.get(call['name'])
            round_results.append((result, bool(entry and entry.writes)))
        completed_rounds += 1
        budget.observe(round_results)

    final_text = "".join(final_assistant_chunks).strip()
    if final_text:
        yield {"type": "assistant_message", "text": final_text}
    yield {"type": "done"}
