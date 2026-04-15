"""Image OCR extractor via pytesseract (pillow backend).

Disabled unless `ingest.ocr_enabled` is true. Requires the system
`tesseract` binary in addition to the Python bindings.
"""

from __future__ import annotations

import io
import os

from . import ExtractedDoc, ExtractorUnavailable, ExtractionError, register_ext, register_mime


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
        import pytesseract  # type: ignore
    except ImportError as e:
        raise ExtractorUnavailable(
            "image OCR needs pillow + pytesseract (+ system tesseract) — "
            "`pip install pillow pytesseract`"
        ) from e

    try:
        img = Image.open(io.BytesIO(data))
        text = pytesseract.image_to_string(img, lang=_ocr_lang())
    except Exception as e:
        raise ExtractionError(f"image OCR failed: {e}") from e

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
