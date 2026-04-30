"""Kreuzberg extractor (opt-in, ELv2 license).

When ``ingest.use_kreuzberg=true`` AND the ``kreuzberg`` package is installed,
this module overwrites the existing per-extension / per-mime registrations with
kreuzberg-backed implementations. It also adds archive formats (.zip, .tar,
.7z, …) and extra document types that our native extractors do not handle.

Enable:
    imprint config set ingest.use_kreuzberg true
    pip install kreuzberg[all]   # or kreuzberg for core only

License notice: kreuzberg is published under the Elastic License 2.0 (ELv2).
It may NOT be offered as a hosted / SaaS service. Internal / self-hosted use
is permitted. Review https://www.elastic.co/licensing/elastic-license before
enabling in a multi-tenant deployment.
"""

from __future__ import annotations

import os

from . import (
    ExtractedDoc,
    ExtractorUnavailable,
    ExtractionError,
    DOC_EXTENSIONS,
    register_ext,
    register_mime,
)


def _get_kreuzberg_enabled() -> bool:
    try:
        from ..config_schema import resolve
        val, _ = resolve("ingest.use_kreuzberg")
        return bool(val)
    except Exception:
        return False


def _assemble(result) -> str:
    """Join main content with any table markdown extracted by kreuzberg."""
    parts = [result.content or ""]
    for table in getattr(result, "tables", None) or []:
        md = getattr(table, "markdown", None) or ""
        if md:
            parts.append(md)
    return "\n\n".join(p for p in parts if p).strip()


def _make_meta(result) -> dict:
    raw = getattr(result, "metadata", None) or {}
    meta: dict = {}
    for attr in ("title", "author", "date", "description", "language"):
        v = raw.get(attr) if isinstance(raw, dict) else getattr(raw, attr, None)
        if v:
            meta[attr] = str(v)
    langs = getattr(result, "detected_languages", None)
    if langs:
        meta["detected_languages"] = list(langs)
    return meta


# ── prose extensions replaced/added by kreuzberg ─────────────────────────
_PROSE_EXTS = {
    ".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".csv",
    ".epub", ".rtf", ".html", ".htm", ".eml", ".mbox",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp",
    # new formats not handled by existing extractors
    ".zip", ".tar", ".tgz", ".gz", ".bz2", ".7z",
    ".pages", ".numbers", ".key",
    ".odt", ".ods", ".odp",
    ".ipynb",
    ".svg",
}

_PROSE_MIMES = {
    "application/zip",
    "application/x-tar",
    "application/x-7z-compressed",
    "application/x-bzip2",
    "application/gzip",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
    "application/x-ipynb+json",
}

# .ipynb is code-ish but we let kreuzberg extract and let chunker decide
_CODE_EXTS: set[str] = set()


def _extract_file(path: str) -> ExtractedDoc:
    try:
        from kreuzberg import extract_file_sync  # type: ignore
    except ImportError as e:
        raise ExtractorUnavailable(
            "kreuzberg not installed — `pip install kreuzberg[all]`"
        ) from e
    try:
        result = extract_file_sync(path)
    except Exception as e:
        raise ExtractionError(f"kreuzberg failed on {path}: {e}") from e
    text = _assemble(result)
    if not text:
        raise ExtractionError(f"kreuzberg returned empty content for {path}")
    ext = os.path.splitext(path)[1].lower()
    chunk_mode = None if ext in _CODE_EXTS else "prose"
    return ExtractedDoc(
        text=text,
        mime=getattr(result, "mime_type", "") or "",
        metadata={**_make_meta(result), "filename": os.path.basename(path)},
        chunk_mode=chunk_mode,
    )


def _extract_bytes(data: bytes, source_url: str = "") -> ExtractedDoc:
    try:
        from kreuzberg import extract_bytes_sync  # type: ignore
    except ImportError as e:
        raise ExtractorUnavailable(
            "kreuzberg not installed — `pip install kreuzberg[all]`"
        ) from e
    try:
        result = extract_bytes_sync(data)
    except Exception as e:
        raise ExtractionError(f"kreuzberg failed on bytes from {source_url}: {e}") from e
    text = _assemble(result)
    if not text:
        raise ExtractionError(f"kreuzberg returned empty content for {source_url}")
    return ExtractedDoc(
        text=text,
        mime=getattr(result, "mime_type", "") or "",
        metadata={**_make_meta(result), **({"source_url": source_url} if source_url else {})},
        chunk_mode="prose",
    )


def _register() -> None:
    for ext in _PROSE_EXTS:
        register_ext(ext, _extract_file)
        DOC_EXTENSIONS.add(ext)

    for mime in _PROSE_MIMES:
        register_mime(mime, _extract_bytes)

    # Also override common mime types for formats kreuzberg handles better
    for mime in (
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "image/png", "image/jpeg", "image/gif", "image/webp",
        "image/tiff", "image/bmp",
    ):
        register_mime(mime, _extract_bytes)


if _get_kreuzberg_enabled():
    _register()
