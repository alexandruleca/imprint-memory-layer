"""Embeddings through llama.cpp, with explicit Qwen query and pooling semantics (ADR 0086).

The model is a GGUF that llama.cpp runs whole on one GPU, or on the CPU
(``llama_runtime``). Each input is capped at ``model.seq_length`` tokens
including the special tokens the model's tokenizer adds: Qwen3-Embedding
reads its vector at the end-of-text token it appends, so a long input loses
words from its end, never that token. Vectors are cut to ``model.dim``
(Matryoshka) and L2-normalized.
"""

from __future__ import annotations

import hashlib
import sys
import threading
from pathlib import Path

import numpy as np
from huggingface_hub import hf_hub_download

from . import config

_model = None
_on_gpu = False
_specials = 0  # Tokens the tokenizer adds around every input.
_lock = threading.RLock()  # A llama.cpp context serves one call at a time.

_PRETRIM_CHARS = max(2048, config.MAX_SEQ_LENGTH * 8)


def _pooling() -> int:
    """llama.cpp's pooling for ``model.pooling``; ``auto`` follows the model
    family, or the GGUF's own metadata for other models."""
    import llama_cpp
    mode = config.POOLING
    if mode == "auto":
        mode = {"qwen3": "last", "bge-m3": "cls"}.get(config.MODEL_FAMILY, "auto")
    return {"auto": llama_cpp.LLAMA_POOLING_TYPE_UNSPECIFIED, "cls": llama_cpp.LLAMA_POOLING_TYPE_CLS,
            "mean": llama_cpp.LLAMA_POOLING_TYPE_MEAN, "last": llama_cpp.LLAMA_POOLING_TYPE_LAST}[mode]


def _load():
    global _model, _on_gpu, _specials
    with _lock:
        if _model is not None:
            return _model
        if not config.MODEL_FILE:
            raise ValueError(f"Set model.file to the GGUF file in {config.MODEL_NAME}")
        model_key = hashlib.sha256(f"{config.MODEL_NAME}@{config.MODEL_REVISION}".encode()).hexdigest()[:16]
        model_dir = config.get_data_dir() / "models" / "embeddings" / model_key
        path = hf_hub_download(config.MODEL_NAME, config.MODEL_FILE, revision=config.MODEL_REVISION,
                               local_dir=model_dir, token=config.hub_token())
        from . import model_profiles as profiles
        if (config.MODEL_NAME, config.MODEL_REVISION, config.MODEL_FILE) == (profiles.EMBEDDING_REPO, profiles.EMBEDDING_REVISION, profiles.EMBEDDING_FILE):
            from .downloads import verify_archive
            verify_archive(Path(path), profiles.EMBEDDING_SHA256)
        from . import llama_runtime
        length = config.MAX_SEQ_LENGTH
        model, on_gpu = llama_runtime.load(
            path, 0 if config.DEVICE == "cpu" else -1, embedding=True, pooling_type=_pooling(),
            n_ctx=length, n_batch=length, n_ubatch=length,
            n_threads=config.CPU_THREADS, n_threads_batch=config.CPU_THREADS)
        if config.DEVICE == "gpu" and not on_gpu:
            print("imprint: model.device is gpu, but llama.cpp has no GPU here; embedding on the CPU", file=sys.stderr)
        _specials = len(model.tokenize(b"a")) - len(model.tokenize(b"a", add_bos=False))
        _on_gpu = on_gpu
        _model = model
        return model


def on_gpu() -> bool:
    """Whether embeddings run on a GPU. Loads the model."""
    _load()
    return _on_gpu


def _fit(model, text: str) -> str:
    """``text``, shortened from its end until its tokens and the tokenizer's
    special tokens fit ``model.seq_length``."""
    room = config.MAX_SEQ_LENGTH - _specials
    tokens = model.tokenize(text.encode("utf-8"), add_bos=False)
    cut = room
    while len(tokens) > room:
        # The text up to a cut can tokenize into more tokens than the cut
        # kept: cut deeper until it fits.
        text = model.detokenize(tokens[:cut]).decode("utf-8", "ignore")
        tokens = model.tokenize(text.encode("utf-8"), add_bos=False)
        cut -= 8
    return text


def _embed_raw(texts: list[str]) -> np.ndarray:
    """Embed texts into L2-normalized vectors (dimension per config.EMBEDDING_DIM)."""
    with _lock:
        model = _load()
        raw = np.array(model.embed([_fit(model, text) for text in texts], normalize=False, truncate=False),
                       dtype=np.float32)
    if raw.ndim != 2 or len(raw) != len(texts):
        raise ValueError(f"Invalid embedding output {raw.shape} for {len(texts)} inputs")
    if config.MODEL_FAMILY == "qwen3" and 32 <= config.EMBEDDING_DIM <= raw.shape[-1]:
        raw = raw[:, :config.EMBEDDING_DIM]
    if raw.shape[-1] != config.EMBEDDING_DIM or not np.isfinite(raw).all():
        raise ValueError(f"Invalid embedding output {raw.shape}; expected ({len(texts)}, {config.EMBEDDING_DIM}) finite values")
    norms = np.linalg.norm(raw, axis=1, keepdims=True)
    if np.any(norms <= 1e-9):
        raise ValueError("Embedding model returned a zero vector")
    return raw / norms


def embed_document(text: str) -> list[float]:
    """Embed a document into a dense vector."""
    return _embed_raw([text[:_PRETRIM_CHARS]])[0].tolist()


def embed_query(text: str) -> list[float]:
    """Embed a search query into a dense vector."""
    query = text[:_PRETRIM_CHARS]
    if config.QUERY_INSTRUCTION:
        query = f"Instruct: {config.QUERY_INSTRUCTION}\nQuery: {query}"
    return _embed_raw([query])[0].tolist()


def default_batch_size() -> int:
    """Texts per embedding call: ``model.batch_size``, else 8 on a GPU, where
    llama.cpp packs them into one pass, and 1 on the CPU, so progress moves
    after every text."""
    return config.BATCH_SIZE or (8 if on_gpu() else 1)


def embed_documents_batch(texts: list[str], batch_size: int | None = None) -> list[list[float]]:
    """Embed documents in batches of similar length."""
    size = batch_size or default_batch_size()
    if size < 1:
        raise ValueError("Embedding batch size must be positive")
    pretrimmed = [t[:_PRETRIM_CHARS] for t in texts]
    order = sorted(range(len(pretrimmed)), key=lambda i: len(pretrimmed[i]))
    vectors: list[list[float] | None] = [None] * len(pretrimmed)
    for start in range(0, len(order), size):
        indices = order[start:start + size]
        for index, vector in zip(indices, _embed_raw([pretrimmed[i] for i in indices])):
            vectors[index] = vector.tolist()
    return vectors  # type: ignore[return-value]
