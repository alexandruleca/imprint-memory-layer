import gc
import os

import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

from . import config

_session = None
_tokenizer = None

# Per-text char cap before tokenization. Tokenizer will still truncate to
# MAX_SEQ_LENGTH, but pre-trimming avoids spending memory tokenizing 50k chars
# only to throw most of it away. ~6 chars/token average → 1024*6 ≈ 6000.
_PRETRIM_CHARS = max(2048, config.MAX_SEQ_LENGTH * 8)


def _build_session_options() -> ort.SessionOptions:
    """Configure ONNX Runtime to release memory between inference calls.

    Defaults grow a CPU memory arena to the worst-case size and never shrink it.
    For batched embedding of variable-length text on a low-RAM box (WSL2) that
    arena pins hundreds of MB / GB indefinitely. Disabling the arena and
    mem-pattern planner is slightly slower but keeps memory bounded.

    Threads are also clamped — multiple intra-op threads each carry their own
    workspace allocator and inflate peak RSS.
    """
    so = ort.SessionOptions()
    so.enable_cpu_mem_arena = False
    so.enable_mem_pattern = False
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC

    threads = int(os.environ.get("KNOWLEDGE_ONNX_THREADS", "4"))
    so.intra_op_num_threads = max(1, threads)
    so.inter_op_num_threads = 1
    return so


def _load():
    global _session, _tokenizer
    if _session is not None:
        return
    model_path = hf_hub_download(config.MODEL_NAME, config.MODEL_FILE)
    tok_path = hf_hub_download(config.MODEL_NAME, "tokenizer.json")
    _session = ort.InferenceSession(model_path, sess_options=_build_session_options())
    _tokenizer = Tokenizer.from_file(tok_path)
    _tokenizer.enable_padding()
    _tokenizer.enable_truncation(max_length=config.MAX_SEQ_LENGTH)


def _embed_raw(texts: list[str]) -> np.ndarray:
    """Embed pre-prefixed texts into normalized vectors.

    Caller is responsible for keeping the batch size small and the texts
    similar in length (see embed_documents_batch for length bucketing).
    """
    _load()
    encoded = _tokenizer.encode_batch(texts)
    input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
    token_type_ids = np.zeros_like(input_ids)

    outputs = _session.run(
        None,
        {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        },
    )
    # Mean pooling
    token_embeddings = outputs[0]
    mask_expanded = np.expand_dims(attention_mask, -1).astype(np.float32)
    pooled = np.sum(token_embeddings * mask_expanded, axis=1) / np.clip(
        mask_expanded.sum(axis=1), 1e-9, None
    )
    # L2 normalize
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    result = pooled / norms

    # Drop large intermediates immediately so they can't pile up across calls.
    del encoded, input_ids, attention_mask, token_type_ids
    del outputs, token_embeddings, mask_expanded, pooled, norms
    return result


def embed_document(text: str) -> list[float]:
    """Embed a document/stored content. Uses 'search_document:' prefix for nomic."""
    return _embed_raw([f"search_document: {text[:_PRETRIM_CHARS]}"])[0].tolist()


def embed_query(text: str) -> list[float]:
    """Embed a search query. Uses 'search_query:' prefix for nomic."""
    return _embed_raw([f"search_query: {text[:_PRETRIM_CHARS]}"])[0].tolist()


def embed_documents_batch(texts: list[str], batch_size: int = 16) -> list[list[float]]:
    """Embed multiple documents in length-bucketed batches.

    Why bucketing matters: the tokenizer pads every text in a batch to the
    longest one, and ONNX activation memory scales with batch * seq_len. A
    single 2048-token chunk mixed with three 100-token chunks costs the same
    as a batch of four 2048-token chunks. Sorting by length and batching
    similar-length items keeps padding (and peak memory) low.

    The result list is reordered back to the caller's original order.
    """
    if not texts:
        return []

    pretrimmed = [t[:_PRETRIM_CHARS] for t in texts]

    # Sort indices by text length so each batch contains similar-length items.
    order = sorted(range(len(pretrimmed)), key=lambda i: len(pretrimmed[i]))

    vectors: list[list[float] | None] = [None] * len(pretrimmed)
    for i in range(0, len(order), batch_size):
        idx_slice = order[i:i + batch_size]
        batch = [f"search_document: {pretrimmed[j]}" for j in idx_slice]
        out = _embed_raw(batch)
        for k, j in enumerate(idx_slice):
            vectors[j] = out[k].tolist()
        del batch, out
        # Force release of the per-batch tensors before the next iteration.
        gc.collect()

    return vectors  # type: ignore[return-value]
