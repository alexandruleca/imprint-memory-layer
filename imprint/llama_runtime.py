"""How Imprint runs llama.cpp: on which GPU (ADR 0087), and what it logs (ADR 0086).

llama.cpp splits a model's layers across every discrete GPU by default. On a
machine with a fast and a slow card the slow one paces every batch: on an RTX
4080 Laptop beside a GTX 1060, embedding ran at 1,763 tokens/s split across
both and at 7,316 on the 4080 alone. Imprint therefore runs each model whole
on one GPU. ``model.gpu_device`` names it; ``auto`` takes the discrete GPU
with the most memory. Integrated GPUs count only when there is no discrete
one, as llama.cpp itself decides: their memory is the system's and says
nothing about speed.

Device facts come from ggml's device registry, which the binding loads but
does not wrap. The list here is the one llama.cpp offloads to, so a GPU's
position in it is llama.cpp's ``main_gpu``.

Models load with ``verbose=True`` and llama.cpp's log goes through a callback
that passes on errors only. The binding's quiet mode instead points file
descriptors 1 and 2 at the null device while a model loads, and in an MCP
server standard output carries the protocol. What the binding itself prints
while loading, the model's metadata and chat template, goes to a buffer that
only a failed load reports; the loaded model is quiet.
"""
from __future__ import annotations

import contextlib
import ctypes
import io
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

# enum ggml_backend_dev_type
_GPU, _IGPU = 1, 2
_GGML_LOG_LEVEL_ERROR = 3
_log_callback = None  # Referenced for as long as llama.cpp may call it.
# Loads redirect sys.stderr, which is process-wide: one at a time.
_construct_lock = threading.Lock()


@dataclass(frozen=True)
class Gpu:
    index: int  # llama.cpp's main_gpu
    name: str
    memory_mb: int


def gpus() -> list[Gpu]:
    """The GPUs llama.cpp offloads to, in its order. Empty for a CPU build
    and for a GPU build that found no GPU."""
    import llama_cpp
    from llama_cpp import _ctypes_extensions, _ggml

    _quiet_logs()
    llama_cpp.llama_backend_init()
    if not llama_cpp.llama_supports_gpu_offload():
        return []
    registry = _ggml.libggml
    devices = _ctypes_extensions.load_shared_library("ggml-base", Path(llama_cpp.__file__).parent / "lib")
    registry.ggml_backend_dev_count.restype = ctypes.c_size_t
    registry.ggml_backend_dev_get.argtypes = [ctypes.c_size_t]
    registry.ggml_backend_dev_get.restype = ctypes.c_void_p
    devices.ggml_backend_dev_type.argtypes = [ctypes.c_void_p]
    devices.ggml_backend_dev_type.restype = ctypes.c_int
    devices.ggml_backend_dev_description.argtypes = [ctypes.c_void_p]
    devices.ggml_backend_dev_description.restype = ctypes.c_char_p
    devices.ggml_backend_dev_memory.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
    found: dict[int, list[tuple[str, int]]] = {_GPU: [], _IGPU: []}
    for i in range(registry.ggml_backend_dev_count()):
        device = registry.ggml_backend_dev_get(i)
        kind = devices.ggml_backend_dev_type(device)
        if kind in found:
            free, total = ctypes.c_size_t(), ctypes.c_size_t()
            devices.ggml_backend_dev_memory(device, ctypes.byref(free), ctypes.byref(total))
            name = devices.ggml_backend_dev_description(device).decode("utf-8", "replace")
            found[kind].append((name, total.value >> 20))
    return [Gpu(index, name, memory) for index, (name, memory) in enumerate(found[_GPU] or found[_IGPU])]


def choose(setting: str, available: list[Gpu]) -> Gpu | None:
    """The GPU ``model.gpu_device`` selects, or None when it selects none."""
    if setting == "auto":
        return max(available, key=lambda gpu: gpu.memory_mb, default=None)
    return next((gpu for gpu in available if gpu.index == int(setting)), None)


def placement(n_gpu_layers: int) -> dict:
    """``llama_cpp.Llama`` arguments that put a model on the chosen GPU, or
    on the CPU when ``n_gpu_layers`` is 0 or no GPU is chosen."""
    from . import config
    gpu = choose(str(config.GPU_DEVICE), gpus()) if n_gpu_layers else None
    if gpu is None:
        return {"n_gpu_layers": 0}
    import llama_cpp
    return {"n_gpu_layers": n_gpu_layers, "split_mode": llama_cpp.LLAMA_SPLIT_MODE_NONE, "main_gpu": gpu.index}


def load(path: str, n_gpu_layers: int, **options):
    """A ``llama_cpp.Llama`` for ``path`` on the chosen GPU, else on the CPU,
    and whether it runs on a GPU. A GPU that cannot hold the model, for
    example one with too little free memory, leaves it on the CPU."""
    import llama_cpp
    from . import _llama_compat
    _llama_compat.apply()
    _quiet_logs()
    place = placement(n_gpu_layers)
    try:
        return _construct(path, **options, **place), bool(place["n_gpu_layers"])
    except Exception as error:
        if not place["n_gpu_layers"]:
            raise
        print(f"imprint: {Path(path).name} runs on the CPU; the GPU could not load it ({error})", file=sys.stderr)
        return _construct(path, **options, n_gpu_layers=0), False


def _construct(path: str, **options):
    import llama_cpp
    printed = io.StringIO()
    with _construct_lock:
        try:
            with contextlib.redirect_stderr(printed):
                model = llama_cpp.Llama(model_path=path, verbose=True, **options)
        except Exception:
            sys.stderr.write(printed.getvalue())  # Why loading failed, in llama.cpp's words.
            raise
    model.verbose = False
    return model


def _quiet_logs() -> None:
    """Pass on llama.cpp's errors, to standard error, and nothing else."""
    global _log_callback
    if _log_callback is not None:
        return
    import llama_cpp

    @llama_cpp.llama_log_callback
    def callback(level, text, user_data):
        if level == _GGML_LOG_LEVEL_ERROR:
            sys.stderr.write(text.decode("utf-8", "replace"))

    _log_callback = callback
    llama_cpp.llama_log_set(callback, ctypes.c_void_p(0))
