"""Monkeypatches for upstream llama-cpp-python bugs.

Import once after `import llama_cpp`; `apply()` is idempotent.
"""

from __future__ import annotations


_PATCHED = False


def apply() -> None:
    """Patch LlamaModel.close so a partially-initialized model doesn't spew
    ``AttributeError: 'LlamaModel' object has no attribute 'sampler'`` during
    garbage collection. Upstream close() touches ``self.sampler`` before
    __init__ sets it; when llama_model_load_from_file returns null the init
    raises and __del__ then drowns the real error in tracebacks.
    """
    global _PATCHED
    if _PATCHED:
        return

    try:
        from llama_cpp import _internals  # type: ignore
    except Exception:
        return

    model_cls = getattr(_internals, "LlamaModel", None)
    if model_cls is None:
        return

    orig_close = model_cls.close

    def _safe_close(self):
        if not hasattr(self, "_exit_stack"):
            return
        try:
            orig_close(self)
        except AttributeError:
            pass

    model_cls.close = _safe_close
    _PATCHED = True
