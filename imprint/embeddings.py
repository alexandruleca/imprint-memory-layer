"""BGE-M3 embedding via ONNX Runtime.

Model produces 1024-dim dense vectors. No prefix needed (unlike nomic-embed
which uses `search_document:` / `search_query:`). Optional sparse head is
exposed for future hybrid search but not wired into storage yet.
"""

import gc
import os

import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

from . import config

_session = None
_cpu_session = None  # Lazy CPU fallback for GPU OOM on single items
_tokenizer = None

_PRETRIM_CHARS = max(2048, config.MAX_SEQ_LENGTH * 8)


def _build_session_options() -> ort.SessionOptions:
    """Keep ONNX memory bounded on low-RAM boxes. See earlier OOM fix notes."""
    so = ort.SessionOptions()
    so.enable_cpu_mem_arena = False
    so.enable_mem_pattern = False
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    # Suppress ORT C++ error messages on stderr (OOM errors are caught and
    # handled in Python — the raw C++ output just adds noise).
    # 0=VERBOSE, 1=INFO, 2=WARNING, 3=ERROR, 4=FATAL
    so.log_severity_level = 4

    threads = int(os.environ.get("IMPRINT_ONNX_THREADS", "4"))
    so.intra_op_num_threads = max(1, threads)
    so.inter_op_num_threads = 1
    return so


def _preload_cuda_libs() -> None:
    """Dlopen pip-installed CUDA libs so ORT-GPU finds them without needing
    LD_LIBRARY_PATH set at process start. Best-effort — silently no-ops if
    libs aren't installed (CPU path still works)."""
    import ctypes
    import glob
    import site

    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        nv = os.path.join(sp, "nvidia")
        if not os.path.isdir(nv):
            continue
        # Order matters: cuda_runtime → cublas → cudnn (cudnn depends on cublas).
        for sub in ("cuda_runtime", "cublas", "cudnn", "cufft", "curand"):
            for so in sorted(glob.glob(os.path.join(nv, sub, "lib", "lib*.so.*"))):
                try:
                    ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass
        return


def _resolve_providers() -> list:
    """Pick the ORT execution provider list based on config.DEVICE.

    `cpu` → CPU only. `gpu` → CUDA then CPU fallback. `auto` → CUDA if the
    GPU provider is actually registered, else CPU. The CPU provider is
    always listed last as a safety net so inference still runs when CUDA
    init fails (e.g. driver mismatch).
    """
    mode = config.DEVICE
    if mode != "cpu":
        _preload_cuda_libs()
    avail = set(ort.get_available_providers())
    want_gpu = mode == "gpu" or (mode == "auto" and "CUDAExecutionProvider" in avail)
    if want_gpu and "CUDAExecutionProvider" in avail:
        # Cap VRAM + use kSameAsRequested so arena doesn't grow unbounded.
        # Default arena_extend_strategy=kNextPowerOfTwo can OOM the GPU on
        # variable-length batches (each long batch doubles arena). Capping
        # gpu_mem_limit also prevents driver kills under WSL2 (shared VRAM
        # with host makes large caps risky). 2048MB is conservative but safe
        # across WSL2/consumer GPUs — raise via env on dedicated cards.
        gpu_mem_mb = int(os.environ.get("IMPRINT_GPU_MEM_MB", "2048"))
        device_id = int(os.environ.get("IMPRINT_GPU_DEVICE", "0"))
        cuda_opts = {
            "device_id": device_id,
            "arena_extend_strategy": "kSameAsRequested",
            "gpu_mem_limit": gpu_mem_mb * 1024 * 1024,
            "cudnn_conv_algo_search": "HEURISTIC",
            "do_copy_in_default_stream": True,
        }
        return [("CUDAExecutionProvider", cuda_opts), "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def _load():
    global _session, _tokenizer
    if _session is not None:
        return
    model_path = hf_hub_download(config.MODEL_NAME, config.MODEL_FILE)
    # BGE-M3 also ships a data file next to large ONNX files; make sure it's
    # pulled into the same cache dir so ORT finds it.
    try:
        hf_hub_download(config.MODEL_NAME, config.MODEL_FILE + "_data")
    except Exception:
        pass
    tok_path = hf_hub_download(config.MODEL_NAME, "tokenizer.json")
    _session = ort.InferenceSession(
        model_path,
        sess_options=_build_session_options(),
        providers=_resolve_providers(),
    )
    _tokenizer = Tokenizer.from_file(tok_path)
    _tokenizer.enable_padding()
    _tokenizer.enable_truncation(max_length=config.MAX_SEQ_LENGTH)


def _get_cpu_session() -> ort.InferenceSession:
    """Lazy CPU-only session for GPU OOM fallback on single items."""
    global _cpu_session
    if _cpu_session is not None:
        return _cpu_session
    _load()  # ensure model + tokenizer are ready
    # Model already cached by _load() — use local_files_only to skip network
    # checks and suppress huggingface progress bars.
    model_path = hf_hub_download(
        config.MODEL_NAME, config.MODEL_FILE, local_files_only=True,
    )
    _cpu_session = ort.InferenceSession(
        model_path,
        sess_options=_build_session_options(),
        providers=["CPUExecutionProvider"],
    )
    return _cpu_session


def _run_session(session: ort.InferenceSession, texts: list[str]) -> np.ndarray:
    """Run inference on a session, return L2-normalized 1024-dim vectors."""
    encoded = _tokenizer.encode_batch(texts)
    input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)

    inputs = {}
    input_names = {i.name for i in session.get_inputs()}
    if "input_ids" in input_names:
        inputs["input_ids"] = input_ids
    if "attention_mask" in input_names:
        inputs["attention_mask"] = attention_mask
    if "token_type_ids" in input_names:
        inputs["token_type_ids"] = np.zeros_like(input_ids)

    outputs = session.run(None, inputs)
    token_embeddings = outputs[0]

    cls = token_embeddings[:, 0, :]
    norms = np.linalg.norm(cls, axis=1, keepdims=True)
    result = cls / np.clip(norms, 1e-9, None)

    del encoded, input_ids, attention_mask, outputs, token_embeddings, cls, norms
    return result.astype(np.float32)


def _embed_raw(texts: list[str]) -> np.ndarray:
    """Embed texts into L2-normalized 1024-dim vectors."""
    _load()
    return _run_session(_session, texts)


def _embed_raw_cpu(texts: list[str]) -> np.ndarray:
    """CPU fallback for texts that OOM on GPU."""
    _load()
    return _run_session(_get_cpu_session(), texts)


def embed_document(text: str) -> list[float]:
    """Embed a document. BGE-M3 takes raw text — no prefix required."""
    return _embed_raw([text[:_PRETRIM_CHARS]])[0].tolist()


def embed_query(text: str) -> list[float]:
    """Embed a search query. Same model path as documents for BGE-M3."""
    return _embed_raw([text[:_PRETRIM_CHARS]])[0].tolist()


def embed_documents_batch(texts: list[str], batch_size: int | None = None) -> list[list[float]]:
    """batch_size None → auto: 2 on GPU (VRAM-safe for WSL2/2GB caps), 16 on CPU."""
    if batch_size is None:
        batch_size = 2 if config.DEVICE != "cpu" else 16
    return _embed_documents_batch(texts, batch_size)


def _is_oom_error(exc: Exception) -> bool:
    """Detect ORT CUDA OOM. Matches arena exhaustion + cudaMalloc failures."""
    msg = str(exc)
    return (
        "Available memory" in msg
        or "out of memory" in msg.lower()
        or "CUDA_ERROR_OUT_OF_MEMORY" in msg
    )


def _embed_batch_with_oom_retry(batch: list[str], batch_size: int) -> np.ndarray:
    """Embed a batch; on OOM halve the batch and retry recursively.

    When batch_size reaches 1 and GPU still OOMs, falls back to CPU for
    that item. Arena state can be fragmented after OOM — force gc between
    retries so the next attempt starts with more headroom.
    """
    try:
        return _embed_raw(batch)
    except Exception as exc:
        if not _is_oom_error(exc):
            raise
        import sys
        gc.collect()
        if batch_size <= 1:
            print(
                "[embeddings] chunk too large for GPU, using CPU fallback",
                file=sys.stderr,
            )
            return _embed_raw_cpu(batch)
        new_size = max(1, batch_size // 2)
        print(
            f"[embeddings] reducing batch {batch_size} → {new_size} (GPU memory pressure)",
            file=sys.stderr,
        )
        parts: list[np.ndarray] = []
        for i in range(0, len(batch), new_size):
            sub = batch[i:i + new_size]
            parts.append(_embed_batch_with_oom_retry(sub, new_size))
        return np.concatenate(parts, axis=0)


def _embed_documents_batch(texts: list[str], batch_size: int) -> list[list[float]]:
    """Embed multiple documents in length-bucketed batches."""
    if not texts:
        return []

    pretrimmed = [t[:_PRETRIM_CHARS] for t in texts]
    order = sorted(range(len(pretrimmed)), key=lambda i: len(pretrimmed[i]))

    vectors: list[list[float] | None] = [None] * len(pretrimmed)
    for i in range(0, len(order), batch_size):
        idx_slice = order[i:i + batch_size]
        batch = [pretrimmed[j] for j in idx_slice]
        out = _embed_batch_with_oom_retry(batch, batch_size)
        for k, j in enumerate(idx_slice):
            vectors[j] = out[k].tolist()
        del batch, out
        gc.collect()

    return vectors  # type: ignore[return-value]
