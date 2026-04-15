"""Local Gemma chat agent for the `imprint viz` panel.

Runs a GGUF model in-process via llama-cpp-python and exposes an agentic tool
loop backed by Imprint's read-only MCP tools (search / kg_query / status /
wake_up). Fully offline after the model is cached.

Structured so the import is cheap: we only try to import `llama_cpp` and
`huggingface_hub` at call time via helpers; viz still works if they're missing.
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

try:  # pragma: no cover - import-time feature detection
    import llama_cpp  # type: ignore
    LLAMA_AVAILABLE = True
    _LLAMA_IMPORT_ERROR: str | None = None
except Exception as e:  # pragma: no cover
    llama_cpp = None  # type: ignore[assignment]
    LLAMA_AVAILABLE = False
    _LLAMA_IMPORT_ERROR = str(e)


# ── Tool registry (read-only subset of MCP tools) ────────────

def _tool_search(query: str, workspace: str = "", **kwargs: Any) -> str:
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


TOOLS: dict[str, Callable[..., str]] = {
    "search": _tool_search,
    "kg_query": _tool_kg_query,
    "status": _tool_status,
    "wake_up": _tool_wake_up,
}


SYSTEM_PROMPT = """You are Imprint, a local memory assistant. The user is exploring their Imprint memory store — a semantic index of code, decisions, patterns, and conversations. You have OFFLINE access to it via tools.

When you need information, emit exactly one tool call per turn, then wait for the [TOOL RESULT]. Only answer directly when you already have enough context.

Tool call format (emit the block on its own line, no commentary around it):
<tool_call>{"name": "search", "args": {"query": "auth middleware", "limit": 5}}</tool_call>

Available tools:
- search(query, project?, type?, domain?, lang?, layer?, kind?, limit?) — semantic search across stored memories. Best first stop.
- kg_query(subject?, predicate?, limit?) — query the temporal knowledge graph of structured facts.
- status() — memory store stats (projects, counts).
- wake_up() — full context summary: projects, essential decisions, recent activity, active facts.

Guidance:
- Prefer `search` for "what do we know about X". Use a short, keyword-rich query.
- Use `wake_up` only when asked for a broad overview of the memory.
- Cite memory hits using their source or project name when helpful.
- Be concise. Don't repeat tool output back verbatim — summarise.
- If no tool is needed, answer directly without emitting a <tool_call> block."""


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
    if not LLAMA_AVAILABLE:
        return {
            "enabled": enabled,
            "installed": False,
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
            "model_ready": p.exists() or _llm is not None,
            "model_path": str(p),
            "error": None if p.exists() else f"model_path does not exist: {p}",
        }

    fname = _cfg("chat.model_file")
    local = _models_dir() / fname if fname else None
    return {
        "enabled": enabled,
        "installed": True,
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
    # ── Auto-download with progress events if model is missing ──
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
                # Throttle: emit at most ~5/s, plus always at completion
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

    # Build llama-style message list
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in prior_messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "tool":
            # Represent historical tool calls/results as assistant + user pair
            # so the chat template stays happy with only user/assistant/system.
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
            stream = llm.create_chat_completion(
                messages=messages,
                stream=True,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=[_TOOL_CLOSE],
            )
        except Exception as e:
            yield {"type": "error", "error": f"generation failed: {e}"}
            yield {"type": "done"}
            return

        finish_reason: str | None = None
        stop_hit = False
        for chunk in stream:
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            text = delta.get("content") or ""
            if text:
                buffer += text
                yield {"type": "token", "text": text}
            fr = choices[0].get("finish_reason")
            if fr:
                finish_reason = fr

        # llama-cpp's `stop` strips the stop string before emitting, so if
        # finish_reason == "stop" and <tool_call> was opened we re-append close.
        if _TOOL_OPEN in buffer and _TOOL_CLOSE not in buffer:
            buffer += _TOOL_CLOSE
            stop_hit = True

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
