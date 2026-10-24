"""Bounded ONNX embeddings with explicit Qwen query and pooling semantics."""

from __future__ import annotations

import gc
import hashlib
import os
import sys
import threading

import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from huggingface_hub.errors import EntryNotFoundError
from tokenizers import Tokenizer

from . import config

_session = None
_cpu_session = None  # Lazy CPU fallback for GPU OOM on single items
_tokenizer = None
_gpu_retries = 0  # Count of batch reductions (reported after progress bar)
_model_path = None
_load_lock = threading.RLock()

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

    from . import config as _cfg
    so.intra_op_num_threads = max(1, _cfg.ONNX_THREADS)
    so.inter_op_num_threads = 1
    return so


def _preload_cuda_libs() -> None:
    """Load the CUDA and cuDNN libraries that the GPU runtime's NVIDIA wheels
    install (ADR 0038), so its CUDA provider finds them on Windows and Linux.
    A no-op on the CPU runtime. ONNX Runtime reports problems with print(),
    and stdout carries the MCP protocol, so they go to stderr."""
    import contextlib
    import sys

    with contextlib.redirect_stdout(sys.stderr):
        ort.preload_dlls()


def _resolve_providers() -> list:
    """Pick the ORT execution provider list based on config.DEVICE.

    `cpu` → CPU only. `gpu` → CUDA then CPU fallback. `auto` → CUDA if the
    GPU provider is actually registered, else CPU. Either runs on CPU when
    `model.gpu_device=auto` finds no GPU the CUDA runtime supports. The CPU
    provider is always listed last as a safety net so inference still runs
    when CUDA init fails (e.g. driver mismatch).
    """
    mode = config.DEVICE
    if mode != "cpu":
        _preload_cuda_libs()
    avail = set(ort.get_available_providers())
    want_gpu = mode == "gpu" or (mode == "auto" and "CUDAExecutionProvider" in avail)
    if want_gpu and "CUDAExecutionProvider" in avail:
        from . import cuda_devices
        device_id = cuda_devices.resolve(config.GPU_DEVICE)
        if device_id is None:
            print("imprint: no GPU the CUDA runtime supports; embedding on CPU", file=sys.stderr)
            return ["CPUExecutionProvider"]
        # Cap VRAM + use kSameAsRequested so arena doesn't grow unbounded.
        # Default arena_extend_strategy=kNextPowerOfTwo can OOM the GPU on
        # variable-length batches (each long batch doubles arena). Capping
        # gpu_mem_limit also prevents driver kills under WSL2 (shared VRAM
        # with host makes large caps risky). 2048MB is conservative but safe
        # across WSL2/consumer GPUs — raise via env on dedicated cards.
        from . import config as _cfg
        gpu_mem_mb = _cfg.GPU_MEM_MB
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
    with _load_lock:
        _load_locked()


def _load_locked():
    global _session, _tokenizer, _model_path
    if _session is not None:
        return
    # Hub cache files can be symlinks to blobs in different directories.
    # ORT validates external-data paths against the real graph directory;
    # those symlinks can make a legitimate companion file look like an escape.
    # local_dir materializes regular files together and retains download metadata.
    model_key = hashlib.sha256(f"{config.MODEL_NAME}@{config.MODEL_REVISION}".encode()).hexdigest()[:16]
    model_dir = config.get_data_dir() / "models" / "embeddings" / model_key
    def download(filename):
        return hf_hub_download(config.MODEL_NAME, filename, revision=config.MODEL_REVISION, local_dir=model_dir,
                               token=config.hub_token())
    model_path = download(config.MODEL_FILE)
    from . import model_profiles as profiles
    if (config.MODEL_NAME, config.MODEL_REVISION, config.MODEL_FILE) == (profiles.EMBEDDING_REPO, profiles.EMBEDDING_REVISION, profiles.EMBEDDING_FILE):
        from .downloads import verify_archive
        from pathlib import Path
        verify_archive(Path(model_path), profiles.EMBEDDING_SHA256)
    # Some models ship a companion data file (model.onnx_data) alongside the
    # main graph. Pull it into the same cache dir so ORT finds it.
    try:
        download(config.MODEL_FILE + "_data")
    except EntryNotFoundError:
        pass
    tok_path = download("tokenizer.json")
    session = ort.InferenceSession(
        model_path,
        sess_options=_build_session_options(),
        providers=_resolve_providers(),
    )
    tokenizer = Tokenizer.from_file(tok_path)
    pad_id = tokenizer.token_to_id("<|endoftext|>") if config.MODEL_FAMILY == "qwen3" else None
    tokenizer.enable_padding(pad_id=pad_id or 0, pad_token="<|endoftext|>" if pad_id is not None else "[PAD]")
    tokenizer.enable_truncation(max_length=config.MAX_SEQ_LENGTH)
    _model_path = model_path
    _tokenizer = tokenizer
    _session = session  # Publish only after tokenizer initialization succeeds.


def _get_cpu_session() -> ort.InferenceSession:
    with _load_lock:
        return _get_cpu_session_locked()


def _get_cpu_session_locked() -> ort.InferenceSession:
    """Lazy CPU-only session for GPU OOM fallback on single items."""
    global _cpu_session
    if _cpu_session is not None:
        return _cpu_session
    _load()  # ensure model + tokenizer are ready
    # Reuse the exact graph already provisioned by _load(), including its
    # regular-file external weights. No additional network/cache lookup.
    model_path = _model_path
    _cpu_session = ort.InferenceSession(
        model_path,
        sess_options=_build_session_options(),
        providers=["CPUExecutionProvider"],
    )
    return _cpu_session


def _pool(embeddings: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """Apply pooling strategy to token-level embeddings.

    If output is already 2D (batch, dim) — model handled pooling internally.
    If 3D (batch, seq_len, dim) — apply configured strategy.
    """
    if embeddings.ndim == 2:
        # Already pooled by the model (e.g. some ONNX exports)
        return embeddings
    if embeddings.ndim != 3 or embeddings.shape[:2] != attention_mask.shape:
        raise ValueError(f"Unsupported embedding output shape: {embeddings.shape}")

    mode = config.POOLING
    if mode == "auto":
        # BGE-M3 → CLS, others → mean
        if config.MODEL_FAMILY == "qwen3":
            mode = "last"
        elif config.MODEL_FAMILY == "bge-m3":
            mode = "cls"
        else:
            mode = "mean"

    if mode == "cls":
        return embeddings[:, 0, :]
    elif mode == "last":
        # Last non-padding token per sequence
        positions = np.broadcast_to(np.arange(attention_mask.shape[1]), attention_mask.shape)
        last = np.where(attention_mask != 0, positions, -1).max(axis=1)
        if np.any(last < 0):
            raise ValueError("Cannot pool an empty token sequence")
        return embeddings[np.arange(len(embeddings)), last, :]
    elif mode == "mean":
        # Mean pooling — mask out padding tokens
        mask = attention_mask[:, :, np.newaxis].astype(np.float32)
        return (embeddings * mask).sum(axis=1) / mask.sum(axis=1).clip(min=1e-9)
    raise ValueError(f"Unsupported pooling strategy: {mode}")


def _run_session(session: ort.InferenceSession, texts: list[str]) -> np.ndarray:
    """Run inference on a session, return L2-normalized embedding vectors."""
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
    if "position_ids" in input_names:
        inputs["position_ids"] = np.maximum(attention_mask.cumsum(axis=1) - 1, 0)
    # The pinned Qwen export exposes decoder KV inputs. Embedding is a full
    # sequence pass with no preceding context, so supply zero-length caches.
    if config.MODEL_FAMILY == "qwen3":
        for node in session.get_inputs():
            if node.name.startswith("past_key_values."):
                shape = node.shape
                dtype = {"tensor(float)": np.float32, "tensor(float16)": np.float16}.get(node.type)
                if len(shape) != 4 or not isinstance(shape[1], int) or not isinstance(shape[3], int) or dtype is None:
                    raise ValueError(f"Unsupported Qwen KV input: {node.name} {shape} {node.type}")
                inputs[node.name] = np.zeros((len(texts), shape[1], 0, shape[3]), dtype=dtype)
    if input_names - inputs.keys():
        raise ValueError(f"Unsupported ONNX inputs: {sorted(input_names - inputs.keys())}")

    names = [output.name for output in session.get_outputs()]
    output_name = config.MODEL_OUTPUT
    if output_name == "auto":
        output_name = next((name for name in ("sentence_embedding", "sentence_embeddings", "last_hidden_state", "token_embeddings", "pooler_output") if name in names), names[0])
    if output_name not in names:
        raise ValueError(f"ONNX output {output_name!r} missing; available: {names}")
    outputs = session.run([output_name], inputs)
    raw = outputs[0]

    pooled = _pool(raw, attention_mask).astype(np.float32)
    if config.MODEL_FAMILY == "qwen3" and 32 <= config.EMBEDDING_DIM <= pooled.shape[-1]:
        pooled = pooled[:, :config.EMBEDDING_DIM]
    if pooled.shape != (len(texts), config.EMBEDDING_DIM) or not np.isfinite(pooled).all():
        raise ValueError(f"Invalid embedding output {pooled.shape}; expected ({len(texts)}, {config.EMBEDDING_DIM}) finite values")
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    if np.any(norms <= 1e-9):
        raise ValueError("Embedding model returned a zero vector")
    result = pooled / np.clip(norms, 1e-9, None)

    del encoded, input_ids, attention_mask, outputs, raw, pooled, norms
    return result.astype(np.float32)


def _embed_raw(texts: list[str]) -> np.ndarray:
    """Embed texts into L2-normalized vectors (dimension per config.EMBEDDING_DIM)."""
    _load()
    return _run_session(_session, texts)


def _embed_raw_cpu(texts: list[str]) -> np.ndarray:
    """CPU fallback for texts that OOM on GPU."""
    _load()
    return _run_session(_get_cpu_session(), texts)


def embed_document(text: str) -> list[float]:
    """Embed a document into a dense vector."""
    return _embed_raw([text[:_PRETRIM_CHARS]])[0].tolist()


def embed_query(text: str) -> list[float]:
    """Embed a search query into a dense vector."""
    query = text[:_PRETRIM_CHARS]
    if config.QUERY_INSTRUCTION:
        query = f"Instruct: {config.QUERY_INSTRUCTION}\nQuery: {query}"
    return _embed_raw([query])[0].tolist()


def embed_documents_batch(texts: list[str], batch_size: int | None = None) -> list[list[float]]:
    """Conservative auto batches; explicit overrides remain available."""
    if batch_size is None:
        cfg_bs = config.BATCH_SIZE
        if cfg_bs > 0:
            batch_size = cfg_bs
        else:
            batch_size = 4 if config.DEVICE == "gpu" else 1
    if batch_size < 1:
        raise ValueError("Embedding batch size must be positive")
    return _embed_documents_batch(texts, batch_size)


def _is_gpu_error(exc: Exception) -> bool:
    """Detect CUDA/GPU errors — OOM, driver faults, async copy failures."""
    msg = str(exc)
    return (
        "Available memory" in msg
        or "out of memory" in msg.lower()
        or "CUDA_ERROR_OUT_OF_MEMORY" in msg
        or "CUDA failure" in msg
        or "cuda error" in msg.lower()
        or "cudaMemcpy" in msg
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
        if not _is_gpu_error(exc):
            raise
        import sys
        gc.collect()
        global _gpu_retries
        _gpu_retries += 1
        if batch_size <= 1:
            return _embed_raw_cpu(batch)
        new_size = max(1, batch_size // 2)
        parts: list[np.ndarray] = []
        for i in range(0, len(batch), new_size):
            sub = batch[i:i + new_size]
            parts.append(_embed_batch_with_oom_retry(sub, new_size))
        return np.concatenate(parts, axis=0)


def get_gpu_retries() -> int:
    """Return count of GPU batch reductions since last reset."""
    return _gpu_retries


def reset_gpu_retries() -> None:
    global _gpu_retries
    _gpu_retries = 0


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
