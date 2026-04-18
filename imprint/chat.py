"""Chat agent for the Imprint dashboard chat panel.

Supports multiple providers:
- **local**: Runs a GGUF model in-process via llama-cpp-python (fully offline)
- **vllm / openai / ollama / gemini / anthropic**: Remote via OpenAI-compat API

All providers share the same agentic tool loop backed by Imprint's read-only
MCP tools (search / kg_query / status / wake_up).

Structured so the import is cheap: we only try to import `llama_cpp` and
`huggingface_hub` at call time via helpers; the dashboard still works if they're missing.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Generator

from . import config
from . import config_schema
from . import server as _mcp_server


# ── Optional deps ────────────────────────────────────────────

import os

try:  # pragma: no cover - import-time feature detection
    import llama_cpp  # type: ignore
    from imprint import _llama_compat
    _llama_compat.apply()
    LLAMA_AVAILABLE = True
    _LLAMA_IMPORT_ERROR: str | None = None
except Exception as e:  # pragma: no cover
    llama_cpp = None  # type: ignore[assignment]
    LLAMA_AVAILABLE = False
    _LLAMA_IMPORT_ERROR = str(e)


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
    # The chat agent has limited context (16k) — the full wake_up output
    # (project stats, essential context, facets) easily eats 3-4k chars,
    # leaving little room for actual results. The agent can call wake_up
    # or status explicitly when it needs an overview.
    _mcp_server._session_woken = True
    allowed = {"project", "type", "lang", "layer", "kind", "domain", "limit"}
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

    fname = _cfg("chat.model_file")
    repo = _cfg("chat.model_repo")
    if not repo:
        return None, "chat.model_repo is empty (and model not cached)"

    try:
        import httpx  # type: ignore
    except Exception as e:
        return None, f"httpx required for model download ({e})"

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    url = f"https://huggingface.co/{repo}/resolve/main/{fname}"

    try:
        with httpx.stream(
            "GET", url, follow_redirects=True, timeout=httpx.Timeout(60.0, read=300.0),
        ) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb:
                        progress_cb(done, total)
        tmp.rename(target)
        return target, None
    except Exception as e:
        try:
            tmp.unlink()
        except Exception:
            pass
        return None, f"download failed: {e}"


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
    if not LLAMA_AVAILABLE:
        return None, f"llama-cpp-python not installed ({_LLAMA_IMPORT_ERROR})"

    with _llm_lock:
        if _llm is not None:
            return _llm, None

        path, err = _resolve_model_path()
        if err or path is None:
            return None, err or "could not resolve model path"

        try:
            _llm = llama_cpp.Llama(
                model_path=str(path),
                n_ctx=int(_cfg("chat.n_ctx")),
                n_gpu_layers=int(_cfg("chat.n_gpu_layers")),
                verbose=False,
            )
        except Exception as e:
            return None, f"model load failed: {e}"

    return _llm, None


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

    # Local provider
    if not LLAMA_AVAILABLE:
        return {
            "enabled": enabled,
            "installed": False,
            "provider": "local",
            "model_ready": False,
            "model_path": "",
            "error": f"llama-cpp-python not installed ({_LLAMA_IMPORT_ERROR})",
        }

    explicit = _cfg("chat.model_path")
    if explicit:
        p = Path(explicit)
        return {
            "enabled": enabled,
            "installed": True,
            "provider": "local",
            "model_ready": p.exists() or _llm is not None,
            "model_path": str(p),
            "error": None if p.exists() else f"model_path does not exist: {p}",
        }

    fname = _cfg("chat.model_file")
    local = _models_dir() / fname if fname else None
    return {
        "enabled": enabled,
        "installed": True,
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
        # ── Local: auto-download model if missing ──
        local_path, perr = _resolve_local_model_path()
        if not perr and local_path is not None and not local_path.exists() \
                and not _cfg("chat.model_path"):
            repo = _cfg("chat.model_repo")
            fname = _cfg("chat.model_file")
            yield {
                "type": "download_start",
                "file": fname, "repo": repo,
                "path": str(local_path),
            }

            q: queue.Queue = queue.Queue()
            def _cb(done: int, total: int) -> None:
                q.put(("progress", done, total))

            result: dict = {}
            def _worker() -> None:
                p, e = download_model(progress_cb=_cb)
                q.put(("done", p, e))

            threading.Thread(target=_worker, daemon=True).start()

            last_emit = 0.0
            while True:
                ev = q.get()
                kind = ev[0]
                if kind == "progress":
                    _, done, total = ev
                    now = time.time()
                    if now - last_emit > 0.2 or (total and done >= total):
                        last_emit = now
                        yield {
                            "type": "download_progress",
                            "downloaded": done, "total": total,
                        }
                else:  # "done"
                    _, p, e = ev
                    if e or p is None:
                        yield {"type": "error", "error": e or "download failed"}
                        yield {"type": "done"}
                        return
                    yield {"type": "download_complete", "path": str(p)}
                    break

        llm, err = load_model()
        if err or llm is None:
            yield {"type": "error", "error": err or "model unavailable"}
            yield {"type": "done"}
            return

    # Build message list
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
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

        try:
            for text in token_stream:
                buffer += text
                yield {"type": "token", "text": text}
        except Exception as e:
            yield {"type": "error", "error": f"generation failed: {e}"}
            yield {"type": "done"}
            return

        # Stop token strips the close tag — re-append if tool_call was opened
        if _TOOL_OPEN in buffer and _TOOL_CLOSE not in buffer:
            buffer += _TOOL_CLOSE

        call = _parse_tool_call(buffer)
        if call is None:
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
