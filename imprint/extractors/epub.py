"""EPUB extractor via ebooklib + beautifulsoup4."""

from __future__ import annotations

import io
import os

from . import ExtractedDoc, ExtractorUnavailable, ExtractionError, register_ext, register_mime


def _extract_bytes(data: bytes, source_url: str = "") -> ExtractedDoc:
    try:
        import ebooklib  # type: ignore
        from ebooklib import epub  # type: ignore
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError as e:
        raise ExtractorUnavailable(
            "epub extractor needs ebooklib + beautifulsoup4 — "
            "`pip install ebooklib beautifulsoup4`"
        ) from e

    # ebooklib only accepts file paths, so dump bytes to a temp file.
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        book = epub.read_epub(tmp_path)
    except Exception as e:
        raise ExtractionError(f"epub parse failed: {e}") from e
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    parts = []
    for item in book.get_items():
        if item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        try:
            soup = BeautifulSoup(item.get_content(), "html.parser")
            text = soup.get_text(separator="\n").strip()
            if text:
                parts.append(text)
        except Exception:
            continue

    meta: dict = {}
    try:
        for key in ("title", "creator", "language"):
            vals = book.get_metadata("DC", key)
            if vals:
                meta[key] = vals[0][0]
    except Exception:
        pass

    return ExtractedDoc(
        text="\n\n".join(parts).strip(),
        mime="application/epub+zip",
        metadata=meta,
        chunk_mode="prose",
    )


def extract(path: str) -> ExtractedDoc:
    with open(path, "rb") as f:
        doc = _extract_bytes(f.read())
    doc.metadata.setdefault("filename", os.path.basename(path))
    return doc


register_ext(".epub", extract)
register_mime("application/epub+zip", _extract_bytes)
