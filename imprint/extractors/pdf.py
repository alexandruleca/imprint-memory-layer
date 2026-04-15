"""PDF extractor.

Primary: pypdf for text-layer PDFs (fast, pure-python).
Fallback (opt-in): OCR via pdf2image + pytesseract for scanned PDFs
with no text layer — only triggered when the primary pass returns
near-empty output AND `ingest.ocr_enabled` config is true.
"""

from __future__ import annotations

import os

from . import ExtractedDoc, ExtractorUnavailable, ExtractionError, register_ext, register_mime


def _ocr_enabled() -> bool:
    try:
        from ..config_schema import resolve
        return bool(resolve("ingest.ocr_enabled")[0])
    except Exception:
        return False


def _ocr_lang() -> str:
    try:
        from ..config_schema import resolve
        return str(resolve("ingest.ocr_lang")[0]) or "eng"
    except Exception:
        return "eng"


def _extract_text_pypdf(data: bytes) -> tuple[str, dict]:
    try:
        import pypdf  # type: ignore
    except ImportError as e:
        raise ExtractorUnavailable("pypdf not installed — `pip install pypdf`") from e

    import io
    reader = pypdf.PdfReader(io.BytesIO(data))
    pages = []
    for p in reader.pages:
        try:
            pages.append(p.extract_text() or "")
        except Exception:
            pages.append("")
    text = "\n\n".join(pages).strip()

    meta: dict = {"page_count": len(reader.pages)}
    try:
        info = reader.metadata or {}
        for src, dst in [("/Title", "title"), ("/Author", "author"), ("/Subject", "subject")]:
            v = info.get(src)
            if v:
                meta[dst] = str(v)
    except Exception:
        pass
    return text, meta


def _extract_ocr(data: bytes, lang: str) -> str:
    try:
        import pdf2image  # type: ignore
        import pytesseract  # type: ignore
    except ImportError as e:
        raise ExtractorUnavailable(
            "OCR needs pdf2image + pytesseract (and system tesseract + poppler)"
        ) from e

    images = pdf2image.convert_from_bytes(data)
    chunks = []
    for img in images:
        try:
            chunks.append(pytesseract.image_to_string(img, lang=lang))
        except Exception:
            continue
    return "\n\n".join(chunks).strip()


def _extract_bytes(data: bytes, source_url: str = "") -> ExtractedDoc:
    try:
        text, meta = _extract_text_pypdf(data)
    except ExtractorUnavailable:
        raise
    except Exception as e:
        raise ExtractionError(f"pdf parse failed: {e}") from e

    # If text layer is empty/near-empty and OCR is enabled, try OCR.
    if len(text) < 50 and _ocr_enabled():
        try:
            ocr_text = _extract_ocr(data, _ocr_lang())
            if ocr_text:
                text = ocr_text
                meta["ocr"] = True
        except ExtractorUnavailable:
            pass
        except Exception:
            pass

    return ExtractedDoc(
        text=text,
        mime="application/pdf",
        metadata=meta,
        chunk_mode="prose",
    )


def extract(path: str) -> ExtractedDoc:
    with open(path, "rb") as f:
        data = f.read()
    doc = _extract_bytes(data)
    # Include file path in metadata for downstream debugging.
    doc.metadata.setdefault("bytes", len(data))
    doc.metadata.setdefault("filename", os.path.basename(path))
    return doc


register_ext(".pdf", extract)
register_mime("application/pdf", _extract_bytes)
