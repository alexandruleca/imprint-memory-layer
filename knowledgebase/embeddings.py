import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

from . import config

_session = None
_tokenizer = None


def _load():
    global _session, _tokenizer
    if _session is not None:
        return
    model_path = hf_hub_download(config.MODEL_NAME, "onnx/model.onnx")
    tok_path = hf_hub_download(config.MODEL_NAME, "tokenizer.json")
    _session = ort.InferenceSession(model_path)
    _tokenizer = Tokenizer.from_file(tok_path)
    _tokenizer.enable_padding()
    _tokenizer.enable_truncation(max_length=config.MAX_SEQ_LENGTH)


def _embed_raw(texts: list[str]) -> np.ndarray:
    """Embed pre-prefixed texts into normalized vectors."""
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
    return pooled / norms


def embed_document(text: str) -> list[float]:
    """Embed a document/stored content. Uses 'search_document:' prefix for nomic."""
    return _embed_raw([f"search_document: {text}"])[0].tolist()


def embed_query(text: str) -> list[float]:
    """Embed a search query. Uses 'search_query:' prefix for nomic."""
    return _embed_raw([f"search_query: {text}"])[0].tolist()
