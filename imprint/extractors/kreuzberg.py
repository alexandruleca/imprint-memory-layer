"""Kreuzberg extractor (opt-in, ELv2 license).

When ``ingest.use_kreuzberg=true``, this module overwrites the existing
per-extension / per-mime registrations with kreuzberg-backed implementations.
It also adds archive formats (.zip, .tar, .7z, …) and extra document types
that our native extractors do not handle.

Enable:
    imprint config set ingest.use_kreuzberg true

Turning the setting on installs kreuzberg into Imprint's venv (ADR 0121). The
first extraction that finds it missing installs it once per process; when
kreuzberg is unavailable or fails on a file, the native extractor it replaced
handles that file.

License notice: kreuzberg is published under the Elastic License 2.0 (ELv2);
turning the setting on accepts it. It may NOT be offered as a hosted / SaaS
service. Internal / self-hosted use is permitted. Review
https://www.elastic.co/licensing/elastic-license before enabling in a
multi-tenant deployment.
"""

from __future__ import annotations

import functools
import os

from . import (
    _BY_EXT,
    _BY_MIME,
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


def _kreuzberg():
    """The kreuzberg module, installed once per process when missing."""
    try:
        import kreuzberg  # type: ignore
        return kreuzberg
    except ImportError:
        pass
    from .. import optional_tools
    if optional_tools.ensure_once("kreuzberg"):
        try:
            import kreuzberg  # type: ignore
            return kreuzberg
        except ImportError:
            pass
    raise ExtractorUnavailable("kreuzberg unavailable: " + optional_tools.status("kreuzberg"))


def _extract_file(path: str) -> ExtractedDoc:
    kreuzberg = _kreuzberg()
    try:
        result = kreuzberg.extract_file_sync(path)
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


def _extract_bytes(mime: str, data: bytes, source_url: str = "") -> ExtractedDoc:
    kreuzberg = _kreuzberg()
    try:
        # kreuzberg needs the MIME type of bytes; it does not sniff them.
        result = kreuzberg.extract_bytes_sync(data, mime)
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


def _or_native(extract, native):
    """extract, falling back to the native extractor it replaces when
    kreuzberg is unavailable or fails (its OCR, for one, needs Tesseract
    language data that Imprint does not install)."""
    if native is None:
        return extract

    def run(*args):
        try:
            return extract(*args)
        except (ExtractorUnavailable, ExtractionError):
            return native(*args)
    return run


def _register() -> None:
    for ext in _PROSE_EXTS:
        register_ext(ext, _or_native(_extract_file, _BY_EXT.get(ext)))
        DOC_EXTENSIONS.add(ext)

    # Also override common mime types for formats kreuzberg handles better
    for mime in _PROSE_MIMES | {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "image/png", "image/jpeg", "image/gif", "image/webp",
        "image/tiff", "image/bmp",
    }:
        register_mime(mime, _or_native(functools.partial(_extract_bytes, mime), _BY_MIME.get(mime)))


if _get_kreuzberg_enabled():
    _register()
