"""Document + URL extractors.

Each extractor takes a file path (or raw bytes / URL) and returns an
ExtractedDoc(text, mime, metadata). Heavy third-party deps (pypdf,
python-docx, trafilatura, pytesseract) are *optional*: a missing import
raises ExtractorUnavailable, and the caller skips with a warning rather
than crashing the whole ingest run.

Public entry points:

    dispatch_by_ext(path)      → route file path to extractor by extension
    dispatch_by_mime(mime, data, source_url="")
                               → route raw bytes by Content-Type (URL path)
    available_extensions()     → set of extensions we can handle
    is_doc_extension(ext)      → True if ext routes to a doc extractor
                                 (i.e. needs binary read + decode)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable


class ExtractorUnavailable(RuntimeError):
    """Raised when an extractor's optional dep isn't installed."""


class ExtractionError(RuntimeError):
    """Raised when extraction of an otherwise-supported file fails."""


@dataclass
class ExtractedDoc:
    text: str
    mime: str = ""
    metadata: dict = field(default_factory=dict)
    # If set, overrides default chunker dispatch (e.g. force "prose" on .pdf).
    chunk_mode: str | None = None


# Extractors may return a single doc or a list (e.g. one per conversation
# in a ChatGPT export).  Callers should normalise via _normalize_result().
ExtractorResult = ExtractedDoc | list[ExtractedDoc]


# ── Extractor registry ─────────────────────────────────────────
# Each callable: (path: str) -> ExtractorResult. Registered on import of
# the submodule. Kept as a plain dict so unavailable extractors can be
# registered lazily (stub raises ExtractorUnavailable at call time).

_BY_EXT: dict[str, Callable[[str], ExtractorResult]] = {}
_BY_MIME: dict[str, Callable[[bytes, str], ExtractorResult]] = {}


def register_ext(ext: str, fn: Callable[[str], ExtractorResult]) -> None:
    _BY_EXT[ext.lower()] = fn


def register_mime(mime: str, fn: Callable[[bytes, str], ExtractorResult]) -> None:
    _BY_MIME[mime.lower()] = fn


def available_extensions() -> set[str]:
    return set(_BY_EXT.keys())


# Extensions that our own registry knows about. Callers (cli_index) use
# this to decide "should I route through the extractor layer instead of
# doing the legacy plain text read?"
DOC_EXTENSIONS: set[str] = {
    ".pdf", ".docx", ".doc",
    ".pptx", ".xlsx", ".csv", ".tsv",
    ".epub", ".rtf",
    ".html", ".htm",
    ".eml", ".mbox",
    ".json",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp",
}


def is_doc_extension(ext: str) -> bool:
    return ext.lower() in DOC_EXTENSIONS


def _normalize_result(result: ExtractorResult) -> list[ExtractedDoc]:
    """Ensure extractor output is always a list."""
    if isinstance(result, list):
        return result
    return [result]


def dispatch_by_ext(path: str) -> list[ExtractedDoc]:
    """Route a file path to the matching extractor. Raises
    ExtractorUnavailable if the extractor's optional dep is missing.

    Always returns a list — extractors that split large files into
    logical pieces (e.g. one doc per conversation) produce multiple items.
    """
    ext = os.path.splitext(path)[1].lower()
    fn = _BY_EXT.get(ext)
    if fn is None:
        raise ExtractorUnavailable(f"no extractor registered for {ext!r}")
    return _normalize_result(fn(path))


def dispatch_by_mime(mime: str, data: bytes, source_url: str = "") -> list[ExtractedDoc]:
    """Route raw bytes (e.g. HTTP body) to an extractor by Content-Type.

    `mime` may include params (e.g. 'text/html; charset=utf-8'); we strip
    those and match on the bare type.

    Always returns a list.
    """
    base = mime.split(";", 1)[0].strip().lower()
    fn = _BY_MIME.get(base)
    if fn is None:
        # Family fallbacks
        if base.startswith("text/"):
            fn = _BY_MIME.get("text/*")
        elif base.startswith("image/"):
            fn = _BY_MIME.get("image/*")
    if fn is None:
        raise ExtractorUnavailable(f"no extractor registered for mime {base!r}")
    return _normalize_result(fn(data, source_url))


# ── Plain-text extractor (always available) ────────────────────
# For the file extensions already handled by the legacy pipeline we
# register a thin wrapper so cli_index can route *everything* through
# the dispatcher uniformly. Same behavior as the old
# open(..., errors='ignore').read() path.

_TEXT_EXTS = {
    ".md", ".txt", ".rst", ".mdx",
    ".ts", ".tsx", ".js", ".jsx", ".mjs",
    ".py", ".go", ".rs", ".java", ".kt", ".swift", ".rb", ".php",
    ".sql", ".graphql", ".gql", ".proto", ".sh", ".bash", ".zsh",
    ".vue", ".svelte", ".cs", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp",
    ".scala", ".lua", ".yaml", ".yml", ".toml",
    # .json handled by json_doc.py with ChatGPT-export auto-detect
}

_PROSE_TEXT_EXTS = {".md", ".txt", ".rst", ".mdx"}


def _extract_text(path: str) -> ExtractedDoc:
    with open(path, "r", errors="ignore") as f:
        text = f.read()
    ext = os.path.splitext(path)[1].lower()
    mime = "text/markdown" if ext in {".md", ".mdx"} else "text/plain"
    return ExtractedDoc(text=text, mime=mime, metadata={}, chunk_mode=None)


for _e in _TEXT_EXTS:
    register_ext(_e, _extract_text)


# ── Register doc extractors ────────────────────────────────────
# Each submodule self-registers on import. Import order doesn't matter;
# failures to import the heavy dep at extraction time are handled per
# extractor (raising ExtractorUnavailable).
from . import pdf as _pdf       # noqa: E402,F401
from . import docx as _docx     # noqa: E402,F401
from . import office as _office # noqa: E402,F401
from . import epub as _epub     # noqa: E402,F401
from . import rtf as _rtf       # noqa: E402,F401
from . import html as _html     # noqa: E402,F401
from . import email as _email   # noqa: E402,F401
from . import image as _image   # noqa: E402,F401
from . import json_doc as _json_doc  # noqa: E402,F401

# url extractor is callable directly, not via by-ext dispatch.
from . import url as _url       # noqa: E402,F401


__all__ = [
    "ExtractedDoc",
    "ExtractorResult",
    "ExtractorUnavailable",
    "ExtractionError",
    "register_ext",
    "register_mime",
    "available_extensions",
    "is_doc_extension",
    "dispatch_by_ext",
    "dispatch_by_mime",
    "DOC_EXTENSIONS",
]
