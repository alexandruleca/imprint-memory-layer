"""Shared optional GGUF runtime. Importing this module never loads a model."""
from __future__ import annotations

import threading
import tempfile
import time
from pathlib import Path

from . import model_profiles as profiles

INFERENCE_LOCK = threading.RLock()
_load_lock = threading.RLock()
_models = {}


def download(repo: str, filename: str, target: Path, progress_cb=None) -> Path:
    """Serialize downloads across roles/processes and verify the default artifact."""
    import httpx
    from filelock import FileLock
    from huggingface_hub.utils import build_hf_headers
    from . import config
    from .downloads import verify_archive

    target.parent.mkdir(parents=True, exist_ok=True)
    pinned = repo == profiles.HELPER_REPO and filename == profiles.HELPER_FILE
    revision = profiles.HELPER_REVISION if pinned else "main"
    with FileLock(str(target) + ".lock"):
        if target.exists():
            if pinned:
                verify_archive(target, profiles.HELPER_SHA256)
            return target
        fd, name = tempfile.mkstemp(dir=target.parent, suffix=".part")
        import os
        tmp = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                # Same credentials as hf_hub_download. httpx drops the
                # Authorization header when a redirect leaves huggingface.co.
                with httpx.stream("GET", f"https://huggingface.co/{repo}/resolve/{revision}/{filename}",
                                  headers=build_hf_headers(token=config.hub_token()),
                                  follow_redirects=True, timeout=httpx.Timeout(60, read=300)) as response:
                    response.raise_for_status()
                    total = int(response.headers.get("Content-Length") or 0)
                    done = 0
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        stream.write(chunk)
                        done += len(chunk)
                        if progress_cb:
                            progress_cb(done, total)
            if pinned:
                verify_archive(tmp, profiles.HELPER_SHA256)
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)
    return target


def load(path: Path, n_ctx: int, n_gpu_layers: int, *, expected_sha256: str | None = None):
    """Reuse identical configurations in one process; serialize all inference."""
    key = (str(path.resolve()), n_ctx, n_gpu_layers)
    with _load_lock:
        if expected_sha256:
            from .downloads import verify_archive
            verify_archive(path, expected_sha256)
        if key in _models:
            return _models[key]
        import llama_cpp
        from . import _llama_compat
        _llama_compat.apply()
        llm = llama_cpp.Llama(model_path=key[0], n_ctx=n_ctx,
                              n_gpu_layers=n_gpu_layers, n_threads=4, n_threads_batch=4,
                              n_batch=256, verbose=False)
        try:
            # Never deserialize a disk-backed prompt/state cache. The optional
            # binding still depends on unfixed diskcache (CVE-2025-69872).
            llm.set_cache(None)
            # Render the embedded template with thinking explicitly disabled.
            # Qwen3.5 does not implement the old /no_think prompt switch.
            from llama_cpp.llama_chat_format import Jinja2ChatFormatter
            template = llm.metadata.get("tokenizer.chat_template")
            if not template:
                raise ValueError("GGUF must include tokenizer.chat_template")
            def special(token_id):
                return llm.detokenize([token_id], special=True).decode() if token_id >= 0 else ""
            formatter = Jinja2ChatFormatter(
                template="{% set enable_thinking = false %}\n" + template,
                eos_token=special(llm.token_eos()), bos_token=special(llm.token_bos()),
                stop_token_ids=[llm.token_eos()],
            )
            llm.chat_handler = formatter.to_chat_handler()
            llm._imprint_formatter = formatter
            # Fail early if a custom template cannot render ordinary text.
            formatter(messages=[{"role": "user", "content": "Hello"}])
        except Exception:
            llm.close()
            raise
        _models[key] = llm
        return llm


def fit_messages(llm, messages: list[dict], max_tokens: int, tools: list[dict] | None = None) -> list[dict]:
    """Bound actual token usage, dropping old conversation turns first."""
    messages = list(messages)
    while True:
        prompt = llm._imprint_formatter(messages=messages, tools=tools).prompt
        count = len(llm.tokenize(prompt.encode(), add_bos=False, special=True))
        if count + max_tokens + 16 <= llm.n_ctx():
            return messages
        # The current request is the last user message; tool results have
        # their own role. Keep it when pruning a long tool loop.
        anchor = max((i for i, message in enumerate(messages) if message["role"] == "user"),
                     default=len(messages) - 1)
        if anchor > 1:
            del messages[1]
            while len(messages) > 2 and messages[1]["role"] != "user":
                del messages[1]
            continue
        # Drop the loop's oldest assistant turn with its tool results, keeping the latest.
        end = anchor + 2
        while end < len(messages) and messages[end]["role"] == "tool":
            end += 1
        if end >= len(messages):
            raise ValueError("Local helper context exceeded; shorten the input or increase the configured context")
        del messages[anchor + 1:end]


def _before(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("local model time limit reached")


def complete(llm, full_input: str, max_tokens: int = 512, schema: dict | None = None,
             deadline: float | None = None) -> str:
    """One bounded chat turn. A schema constrains the output with a grammar;
    without one the caller validates the text. A ``time.monotonic()`` deadline
    raises TimeoutError instead of starting after it, or of generating past it.
    Prompt processing itself is not interruptible."""
    with INFERENCE_LOCK:
        _before(deadline)
        messages = fit_messages(llm, [{"role": "user", "content": full_input}], max_tokens)
        constraint = {"response_format": {"type": "json_object", "schema": schema}} if schema else {}
        stream = llm.create_chat_completion(
            messages=messages, max_tokens=max_tokens, temperature=0.1, stream=True, **constraint,
        )
        parts = []
        try:
            for chunk in stream:
                _before(deadline)
                parts.append(chunk["choices"][0]["delta"].get("content") or "")
        finally:
            stream.close()
        return "".join(parts)
