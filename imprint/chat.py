"""Chat agent for the Imprint dashboard chat panel.

Supports multiple providers:
- **local**: Runs a GGUF model in-process via llama-cpp-python (fully offline)
- **vllm / openai / ollama / gemini / anthropic**: Remote via OpenAI-compat API

All providers share the same tool loop backed by Imprint's MCP tools.

Structured so the import is cheap: we only try to import `llama_cpp` and
`huggingface_hub` at call time via helpers; the dashboard still works if they're missing.
The first local chat installs the binding and downloads the model (ADR 0035).
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Generator

from . import config
from . import config_schema
from . import server as _mcp_server


# ── Optional deps ────────────────────────────────────────────

llama_cpp: Any = None
_llama_import_error: str | None = None


def _llama() -> Any:
    """The llama.cpp binding, or None while it is not installed. A failed
    import is retried on the next call, so a binding installed while the
    dashboard runs is picked up without a restart."""
    global llama_cpp, _llama_import_error
    if llama_cpp is None:
        importlib.invalidate_caches()
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
    "anthropic": {"model": "claude-haiku-4-5",  "key_env": "ANTHROPIC_API_KEY"},
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

def _tool_search(query: str, workspace: str = "", **kwargs: Any) -> str:
    # Skip the auto-wake preamble that search() prepends on first call.
    # The chat agent has limited context (8k) — the full wake_up output
    # (project stats, essential context, facets) easily eats 3-4k chars,
    # leaving little room for actual results. The agent can call wake_up
    # or status explicitly when it needs an overview.
    ws, error = _mcp_server._validate_ws(workspace)
    if error:
        return error
    _mcp_server._woken_workspaces.add(ws)
    allowed = {"project", "type", "lang", "layer", "kind", "domain", "limit", "source", "mode", "offset"}
    filtered = {k: v for k, v in kwargs.items() if k in allowed}
    return _mcp_server.search(query=query, workspace=workspace, **filtered)


def _tool_kg_query(
    subject: str = "", predicate: str = "", limit: int = 20, workspace: str = "",
) -> str:
    return _mcp_server.kg_query(
        subject=subject, predicate=predicate, limit=limit, workspace=workspace,
    )


def _tool_status(workspace: str = "") -> str:
    return _mcp_server.status(workspace=workspace)


def _tool_wake_up(workspace: str = "") -> str:
    return _mcp_server.wake_up(workspace=workspace)


def _tool_list_sources(workspace: str = "", **kwargs: Any) -> str:
    allowed = {"project", "lang", "layer", "limit"}
    filtered = {k: v for k, v in kwargs.items() if k in allowed}
    return _mcp_server.list_sources(workspace=workspace, **filtered)


def _tool_file_summary(source: str, workspace: str = "", **kwargs: Any) -> str:
    return _mcp_server.file_summary(
        source=source, project=kwargs.get("project", ""), workspace=workspace,
    )


def _tool_file_chunks(source: str, workspace: str = "", **kwargs: Any) -> str:
    return _mcp_server.file_chunks(
        source=source,
        start=int(kwargs.get("start", 0)),
        end=int(kwargs.get("end", -1)),
        project=kwargs.get("project", ""),
        workspace=workspace,
    )


def _tool_store(content: str, workspace: str = "", **kwargs: Any) -> str:
    allowed = {"project", "type", "tags", "source"}
    filtered = {k: v for k, v in kwargs.items() if k in allowed}
    return _mcp_server.store(content=content, workspace=workspace, **filtered)


def _tool_kg_add(
    subject: str, predicate: str, object: str, workspace: str = "", **kwargs: Any,
) -> str:
    return _mcp_server.kg_edit(
        op="add", subject=subject, predicate=predicate, object=object,
        source=kwargs.get("source", ""), workspace=workspace,
    )


def _tool_kg_invalidate(fact_id: int, workspace: str = "") -> str:
    return _mcp_server.kg_edit(op="end", fact_id=fact_id, workspace=workspace)


def _tool_ingest_url(url: str, workspace: str = "", **kwargs: Any) -> str:
    return _mcp_server.ingest_url(
        url=url,
        project=kwargs.get("project", "urls"),
        force=bool(kwargs.get("force", False)),
        workspace=workspace,
    )


TOOLS: dict[str, Callable[..., str]] = {
    "search": _tool_search,
    "kg_query": _tool_kg_query,
    "status": _tool_status,
    "wake_up": _tool_wake_up,
    "list_sources": _tool_list_sources,
    "file_summary": _tool_file_summary,
    "file_chunks": _tool_file_chunks,
    "store": _tool_store,
    "kg_add": _tool_kg_add,
    "kg_invalidate": _tool_kg_invalidate,
    "ingest_url": _tool_ingest_url,
}


SYSTEM_PROMPT = """You are Imprint, a local AI memory assistant. You help users explore, query, and manage their Imprint knowledge base — a semantic index of code, decisions, patterns, conversations, and structured facts. Everything runs locally and offline.

# Tool calling

When you need information or want to perform an action, emit exactly ONE tool call per turn inside <tool_call> tags, then STOP and wait for the [TOOL RESULT]. Never emit two tool calls in one turn. Only answer directly when you already have enough context.

Format (on its own line, no surrounding commentary):
<tool_call>{"name": "tool_name", "args": {"key": "value"}}</tool_call>

# Available tools

## Searching & discovery
- **search**(query, project?, type?, domain?, lang?, layer?, kind?, limit?) — Semantic search across all stored memories. This is your primary tool. Use short, keyword-rich queries. Filters narrow results: type can be decision/pattern/finding/preference/bug/architecture; lang can be python/typescript/go/etc; layer can be api/ui/tests/infra/cli/docs; domain can be auth/db/api/ml/perf/security/etc.
- **kg_query**(subject?, predicate?, limit?) — Query the temporal knowledge graph for structured facts (entity → relationship → value). Use for "what does X use?", "what was decided about Y?", relationship lookups.
- **status**() — Quick stats: total memories, active facts, projects breakdown.
- **wake_up**() — Full session context: all projects, essential decisions, recent activity, active facts. Use when the user asks for a broad overview or "what's in memory".

## File retrieval (read indexed code without filesystem access)
- **list_sources**(project?, lang?, layer?, limit?) — List all indexed source files with chunk counts. Start here to discover what code is in the KB.
- **file_summary**(source, project?) — Overview of one indexed file: chunk count, tags, modification date, and a preview. Use before reading chunks to see if the file has what you need.
- **file_chunks**(source, start?, end?, project?) — Retrieve actual content of a file by chunk index range (0-based, inclusive). Use file_summary first to know how many chunks exist.

## Writing & managing memories
- **store**(content, project?, type?, tags?, source?) — Store a new memory. Write it as a self-contained note that makes sense months later. Include the WHY, not just the WHAT. type: decision/pattern/finding/preference/bug/architecture/milestone. tags: comma-separated keywords.
- **kg_add**(subject, predicate, object, source?) — Add a structured fact to the knowledge graph. Use for relationships: "api-server uses NestJS", "auth decided JWT over sessions".
- **kg_invalidate**(fact_id) — Mark a fact as ended/no longer true. Get the fact_id from kg_query results.

## Ingesting external content
- **ingest_url**(url, project?, force?) — Fetch a URL (webpage, PDF, etc), extract content, chunk it, and store as memories. Deduplicates by ETag/Last-Modified unless force=true.

# Strategy guide

1. **Start with search** for most questions. Use 2-4 keyword queries, not full sentences. Example: "auth middleware cors" not "what do we know about the authentication middleware and CORS configuration".
2. **Use filters** to narrow results when you know the domain. Searching for a Python bug? Add lang="python", type="bug".
3. **Drill into files** with the 3-step flow: list_sources → file_summary → file_chunks. Don't jump to file_chunks without knowing the source path.
4. **Cross-reference** search results with kg_query when you need to understand relationships between entities.
5. **Store findings** when the user shares a decision, discovers a pattern, or resolves a bug. Good memories are specific, self-contained, and explain WHY.
6. **Cite sources** — mention project names, file paths, or fact IDs so the user can trace your answers.
7. **Be concise** — summarize tool results, don't parrot them back. Pull out the key insight.
8. **Chain tools** across turns when needed. Example: search finds a pattern → file_summary gets context → file_chunks reads the actual code. You have up to 6 tool turns per message.
9. If the user asks you to remember or save something, use store or kg_add. If they say something is no longer true, use kg_invalidate.
10. If no tool is needed, answer directly without a <tool_call> block."""


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
        return None, f"llama-cpp-python not installed ({_llama_import_error}). Run: imprint profile add-llm"

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
# The first local chat installs the binding (`imprint profile add-llm`) and
# downloads the model. One process-wide job does both; every chat stream that
# needs it follows the job's events. A stream that closes never interrupts
# the job: stopping uv mid-install would leave a half-written venv.

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


def _install_binding(job: _Provisioning) -> None:
    from .queue import _strip_ansi, find_imprint_binary
    env = {**os.environ, "PYTHONUTF8": "1"}
    tail: list[str] = []
    with subprocess.Popen(
        [find_imprint_binary(), "profile", "add-llm"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding="utf-8", errors="replace", bufsize=1,
        start_new_session=True, env=env,
    ) as proc:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = _strip_ansi(raw).rstrip()
            if line:
                tail = (tail + [line])[-5:]
                job.emit({"type": "install_progress", "line": line})
    if proc.returncode != 0:
        raise RuntimeError("installing llama-cpp-python failed:\n" + "\n".join(tail))
    if _llama() is None:
        raise RuntimeError(f"llama-cpp-python does not import after installing: {_llama_import_error}")


def _run_provisioning(job: _Provisioning) -> None:
    try:
        if _llama() is None:
            job.emit({"type": "install_start"})
            _install_binding(job)
            job.emit({"type": "install_complete"})

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

    # Local provider. A missing binding or model is not an error: the first
    # chat provisions both.
    installed = _llama() is not None
    explicit = _cfg("chat.model_path")
    if explicit:
        p = Path(explicit)
        return {
            "enabled": enabled,
            "installed": installed,
            "provider": "local",
            "model_ready": p.exists() or _llm is not None,
            "model_path": str(p),
            "error": None if p.exists() else f"model_path does not exist: {p}",
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
        "error": None,
    }


# ── Tool-call parsing ────────────────────────────────────────

_TOOL_OPEN = "<tool_call>"
_TOOL_CLOSE = "</tool_call>"
_TOOL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def _partial_open_tag(text: str) -> int:
    """Length of a trailing "<tool_c…" that may still become a tool call."""
    for n in range(min(len(_TOOL_OPEN) - 1, len(text)), 0, -1):
        if _TOOL_OPEN.startswith(text[-n:]):
            return n
    return 0


def _parse_tool_call(text: str) -> dict | None:
    """Find the first valid tool-call JSON block in `text`."""
    m = _TOOL_RE.search(text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or "name" not in obj:
        return None
    name = obj.get("name")
    args = obj.get("args") or obj.get("arguments") or {}
    if name not in TOOLS or not isinstance(args, dict):
        return None
    return {"name": name, "args": args, "raw": m.group(0)}


def _trim_tool_result(result: str, max_chars: int = 4000) -> str:
    if len(result) <= max_chars:
        return result
    return result[:max_chars] + f"\n\n[...truncated {len(result) - max_chars} chars...]"


# ── Remote provider (OpenAI-compat) streaming ───────────────

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


def _stream_remote(messages: list[dict], max_tokens: int, temperature: float,
                   ) -> Generator[str, None, str | None]:
    """Stream tokens from a remote OpenAI-compat provider.

    Yields token strings. Returns the finish_reason via GeneratorExit is not
    applicable here — instead the caller collects the buffer.
    """
    client = _get_openai_client()
    model = _get_chat_model()

    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        max_tokens=max_tokens,
        temperature=temperature,
        stop=[_TOOL_CLOSE],
    )
    for chunk in stream:
        choices = chunk.choices or []
        if not choices:
            continue
        delta = choices[0].delta
        text = delta.content or ""
        if text:
            yield text


def _stream_local(llm: Any, messages: list[dict], max_tokens: int,
                  temperature: float) -> Generator[str, None, None]:
    """Stream tokens from local llama-cpp model. Yields token strings."""
    from .local_llm import INFERENCE_LOCK, fit_messages
    with INFERENCE_LOCK:
        messages = fit_messages(llm, messages, max_tokens)
        stream = llm.create_chat_completion(
            messages=messages,
            stream=True,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=[_TOOL_CLOSE],
        )
        for chunk in stream:
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            text = delta.get("content") or ""
            if text:
                yield text


# ── Streaming tool loop ──────────────────────────────────────

def stream_reply(
    prior_messages: list[dict],
    user_message: str,
    workspace: str = "",
) -> Generator[dict, None, None]:
    """Yield SSE-ready event dicts.

    Event types:
      {"type": "install_start"} / {"type": "install_progress", "line": str} / {"type": "install_complete"}
      {"type": "download_start", "file", "repo", "path"} / {"type": "download_progress", "downloaded", "total"}
      {"type": "download_complete", "path": str}
      {"type": "load_start"}                         # loading the local model into memory
      {"type": "token", "text": str}
      {"type": "tool_call", "name": str, "args": dict}
      {"type": "tool_result", "name": str, "result": str}
      {"type": "assistant_message", "text": str}   # final cleaned assistant text
      {"type": "error", "error": str}
      {"type": "done"}
    """
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
        # ── Local: install the binding and download the model on first use ──
        if _llama() is None or _model_missing():
            job = _provision()
            yield from job.follow()
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

    # Build message list
    from .device import current
    device_context = json.dumps({'current_device': current(), 'working_directory': os.getcwd()})
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT +
        '\n\n# Device and path context\n' + device_context +
        '\nShared memories and conversations remain useful on every device. '
        'Original paths in historical text and tool results are provenance, not local file instructions. '
        'Only path_context.local_source_path is a verified local path hint; verify live contents before edits.'}]
    for m in prior_messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "tool":
            tname = m.get("tool_name") or "tool"
            messages.append({
                "role": "assistant",
                "content": f"<tool_call>{json.dumps({'name': tname, 'args': m.get('tool_args') or {}})}</tool_call>",
            })
            messages.append({"role": "user", "content": f"[TOOL RESULT] {content}"})
        elif role in ("user", "assistant"):
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_message})

    max_iters = int(_cfg("chat.max_tool_iters"))
    max_tokens = int(_cfg("chat.max_tokens"))
    temperature = float(_cfg("chat.temperature"))

    final_assistant_chunks: list[str] = []

    for _iter in range(max_iters):
        buffer = ""
        try:
            if remote:
                token_stream = _stream_remote(messages, max_tokens, temperature)
            else:
                token_stream = _stream_local(llm, messages, max_tokens, temperature)
        except Exception as e:
            yield {"type": "error", "error": f"generation failed: {e}"}
            yield {"type": "done"}
            return

        visible = 0
        try:
            for text in token_stream:
                buffer += text
                # A tool call is text in the reply: stream only what precedes
                # it, holding back a tail that may still become its open tag.
                end = buffer.find(_TOOL_OPEN)
                if end < 0:
                    end = len(buffer) - _partial_open_tag(buffer)
                if end > visible:
                    yield {"type": "token", "text": buffer[visible:end]}
                    visible = end
        except Exception as e:
            yield {"type": "error", "error": f"generation failed: {e}"}
            yield {"type": "done"}
            return

        # Stop token strips the close tag — re-append if tool_call was opened
        if _TOOL_OPEN in buffer and _TOOL_CLOSE not in buffer:
            buffer += _TOOL_CLOSE

        call = _parse_tool_call(buffer)
        if call is None:
            if len(buffer) > visible:
                yield {"type": "token", "text": buffer[visible:]}
            final_assistant_chunks.append(buffer)
            break

        # Announce + execute
        yield {"type": "tool_call", "name": call["name"], "args": call["args"]}
        try:
            fn = TOOLS[call["name"]]
            result = fn(workspace=workspace, **call["args"])
        except TypeError as e:
            result = f"[tool error] bad args for {call['name']}: {e}"
        except Exception as e:
            result = f"[tool error] {call['name']} raised: {e}"
        result = _trim_tool_result(result)
        yield {"type": "tool_result", "name": call["name"], "result": result}

        # Feed into the next iteration
        pre_call = buffer.split(_TOOL_OPEN, 1)[0].strip()
        if pre_call:
            final_assistant_chunks.append(pre_call + "\n")
        messages.append({
            "role": "assistant",
            "content": (pre_call + ("\n" if pre_call else "") + call["raw"]).strip(),
        })
        messages.append({"role": "user", "content": f"[TOOL RESULT] {result}"})
    else:
        yield {
            "type": "error",
            "error": f"max tool iterations ({max_iters}) reached",
        }

    final_text = "".join(final_assistant_chunks).strip()
    if final_text:
        yield {"type": "assistant_message", "text": final_text}
    yield {"type": "done"}
