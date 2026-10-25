"""OpenAI Privacy Filter spans for opt-in secret redaction (ADR 0060).

Privacy Filter labels every token of a text in one pass and decodes the
labels into BIOES spans with a constrained Viterbi pass. Imprint keeps only
its ``secret`` and ``account_number`` spans: names, emails, addresses,
phone numbers, dates and URLs are memory content, not credentials.

The pinned ONNX export downloads on first use, is checked against its
SHA-256 digests on every load, and runs on CPU with ``model.threads``
threads. Long texts are labelled in windows of ``WINDOW`` tokens, each with
``CONTEXT`` tokens of context on both sides (the model's attention band), so
memory stays bounded while every labelled token sees its neighbourhood.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from . import model_profiles as profiles

# Label ids of the pinned export: 0 is background; category c, in this
# order, is B, I, E, S at 1 + 4c to 4 + 4c (its config.json id2label).
CATEGORIES = ("account_number", "private_address", "private_date", "private_email",
              "private_person", "private_phone", "private_url", "secret")
KINDS = {"account_number": "account-number", "secret": "secret"}
WINDOW = 1024
CONTEXT = 128
_NEG = -1e9


def _decoder_tables():
    """Viterbi start, end and transition scores that allow only valid BIOES
    paths. Every bias is zero: the default operating point of the pinned
    revision's viterbi_calibration.json."""
    tags = ["O"] + [tag for _ in CATEGORIES for tag in "BIES"]
    categories = [None] + [index for index in range(len(CATEGORIES)) for _ in "BIES"]
    start = np.array([0.0 if tag in "OBS" else _NEG for tag in tags])
    end = np.array([0.0 if tag in "OES" else _NEG for tag in tags])
    transitions = np.full((len(tags), len(tags)), _NEG)
    for i, (tag, category) in enumerate(zip(tags, categories)):
        for j, (following, next_category) in enumerate(zip(tags, categories)):
            if (following in "OBS") if tag in "OES" else (following in "IE" and next_category == category):
                transitions[i, j] = 0.0
    return start, end, transitions


_START, _END, _TRANSITIONS = _decoder_tables()
_lock = threading.Lock()
_model = None


def _load():
    global _model
    with _lock:
        if _model is None:
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download
            from tokenizers import Tokenizer

            from . import config, embeddings
            from .downloads import verify_archive
            directory = config.get_data_dir() / "models" / "privacy-filter" / profiles.PRIVACY_FILTER_REVISION[:12]
            paths = {}
            for name, digest in profiles.PRIVACY_FILTER_SHA256.items():
                paths[name] = Path(hf_hub_download(profiles.PRIVACY_FILTER_REPO, name,
                                                   revision=profiles.PRIVACY_FILTER_REVISION,
                                                   local_dir=directory, token=config.hub_token()))
                verify_archive(paths[name], digest)
            session = ort.InferenceSession(str(paths[profiles.PRIVACY_FILTER_FILE]),
                                           sess_options=embeddings._build_session_options(),
                                           providers=["CPUExecutionProvider"])
            _model = session, Tokenizer.from_file(str(paths["tokenizer.json"]))
        return _model


def _log_probabilities(session, ids: list[int]) -> np.ndarray:
    rows = []
    for start in range(0, len(ids), WINDOW):
        left, right = max(0, start - CONTEXT), min(len(ids), start + WINDOW + CONTEXT)
        window = np.array([ids[left:right]], dtype=np.int64)
        logits = session.run(["logits"], {"input_ids": window, "attention_mask": np.ones_like(window)})[0][0]
        logits = logits[start - left:start - left + min(WINDOW, len(ids) - start)].astype(np.float64)
        peak = logits.max(axis=1, keepdims=True)
        rows.append(logits - peak - np.log(np.exp(logits - peak).sum(axis=1, keepdims=True)))
    return np.concatenate(rows)


def _decode(log_probabilities: np.ndarray) -> list[int]:
    score = log_probabilities[0] + _START
    back = np.empty((len(log_probabilities) - 1, len(_START)), dtype=np.int16)
    for step in range(1, len(log_probabilities)):
        candidates = score[:, None] + _TRANSITIONS
        back[step - 1] = candidates.argmax(axis=0)
        score = candidates.max(axis=0) + log_probabilities[step]
    path = [int((score + _END).argmax())]
    for step in range(len(back) - 1, -1, -1):
        path.append(int(back[step, path[-1]]))
    return path[::-1]


def labelled_spans(offsets: list[tuple[int, int]], path: list[int]) -> list[tuple[int, int, str]]:
    """Character spans of the redacted categories in a decoded label path."""
    found, begin = [], 0
    for index, label in enumerate(path):
        if not label:
            continue
        category, tag = CATEGORIES[(label - 1) // 4], "BIES"[(label - 1) % 4]
        if tag in "BS":
            begin = index
        if tag in "ES" and category in KINDS:
            found.append((offsets[begin][0], offsets[index][1], KINDS[category]))
    return found


def spans(text: str) -> list[tuple[int, int, str]]:
    """``(start, end, kind)`` of the secrets and account numbers in ``text``."""
    session, tokenizer = _load()
    encoding = tokenizer.encode(text, add_special_tokens=False)
    if not encoding.ids:
        return []
    return labelled_spans(encoding.offsets, _decode(_log_probabilities(session, encoding.ids)))
