"""Image OCR using the bundled local OCR runtime.

Enabled by default; no system binaries are needed for the default language.
"""

from __future__ import annotations

import io
import os

from . import ExtractedDoc, ExtractorUnavailable, ExtractionError, OcrRuntimeMissing, register_ext, register_mime


_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp"}


def _ocr_lang() -> str:
    try:
        from ..config_schema import resolve
        return str(resolve("ingest.ocr_lang")[0]) or "eng"
    except Exception:
        return "eng"


def _ocr_enabled() -> bool:
    try:
        from ..config_schema import resolve
        return bool(resolve("ingest.ocr_enabled")[0])
    except Exception:
        return False


def _extract_bytes(data: bytes, source_url: str = "") -> ExtractedDoc:
    if not _ocr_enabled():
        raise ExtractorUnavailable(
            "image OCR disabled — set `imprint config set ingest.ocr_enabled true`"
        )
    try:
        from PIL import Image  # type: ignore
    except ImportError as e:
        raise OcrRuntimeMissing(f"image OCR runtime missing ({e}) — run imprint bootstrap") from e

    from .ocr import recognize
    with Image.open(io.BytesIO(data)) as img:
        text = recognize(img.convert("RGB"), _ocr_lang())

    return ExtractedDoc(
        text=(text or "").strip(),
        mime="image/*",
        metadata={"ocr": True, "ocr_lang": _ocr_lang()},
        chunk_mode="prose",
    )


def extract(path: str) -> ExtractedDoc:
    with open(path, "rb") as f:
        doc = _extract_bytes(f.read())
    doc.metadata.setdefault("filename", os.path.basename(path))
    return doc


for _e in _IMG_EXTS:
    register_ext(_e, extract)

register_mime("image/*", _extract_bytes)
register_mime("image/png", _extract_bytes)
register_mime("image/jpeg", _extract_bytes)
register_mime("image/gif", _extract_bytes)
register_mime("image/webp", _extract_bytes)
register_mime("image/tiff", _extract_bytes)
register_mime("image/bmp", _extract_bytes)
