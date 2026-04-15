"""RTF extractor via striprtf (pure-python, tiny dep)."""

from __future__ import annotations

import os

from . import ExtractedDoc, ExtractorUnavailable, ExtractionError, register_ext, register_mime


def _extract_bytes(data: bytes, source_url: str = "") -> ExtractedDoc:
    try:
        from striprtf.striprtf import rtf_to_text  # type: ignore
    except ImportError as e:
        raise ExtractorUnavailable("striprtf not installed — `pip install striprtf`") from e

    try:
        raw = data.decode("utf-8", errors="ignore")
        text = rtf_to_text(raw)
    except Exception as e:
        raise ExtractionError(f"rtf parse failed: {e}") from e

    return ExtractedDoc(
        text=text.strip(),
        mime="application/rtf",
        metadata={},
        chunk_mode="prose",
    )


def extract(path: str) -> ExtractedDoc:
    with open(path, "rb") as f:
        doc = _extract_bytes(f.read())
    doc.metadata.setdefault("filename", os.path.basename(path))
    return doc


register_ext(".rtf", extract)
register_mime("application/rtf", _extract_bytes)
register_mime("text/rtf", _extract_bytes)
